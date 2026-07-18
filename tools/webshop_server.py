"""
WebShop 文本环境 HTTP 桥（运行在 WebShop 环境侧）

在装有 princeton-nlp/webshop（或其带 Dockerfile 的 fork，如 ai-nikolai/WebShop）
的 Python 环境里运行本文件，把进程内的 WebAgentTextEnv-v0 包装成 HTTP 服务，
供 PECS（任意 Python 版本）通过 tools/webshop_env.py 远程驱动。

前置（在运行此文件的环境里）：
  - 已把本文件拷到 webshop 目录：cp tools/webshop_server.py ./（无需 pip install -e）
  - 已 pip 安装运行依赖：gym==0.24.0 numpy beautifulsoup4 flask rich cleantext tqdm rank_bm25 thefuzz scikit_learn spacy + en_core_web_sm
  - 已把数据放到 webshop/data/（items_shuffle_1000.json / items_ins_v2_1000.json / items_human_ins.json）
  - 【注意】本项目已用纯 Python rank_bm25 搜索后端（web_agent_site/engine/bm25_search.py）
    替代原版 pyserini/Lucene，故【不需要 Java、不需要构建索引、不需要 torch】

启动：
  conda activate webshop
  python webshop_server.py --port 8000 --num-products 1000

端点：
  GET  /health                              -> {"status": "ok"}
  POST /reset  {task_index?, num_products?} -> {session_id, goal, observation}
  POST /step   {action, session_id?}        -> {observation, reward, done, info}
"""
from __future__ import annotations

import argparse
import threading
from typing import Optional

from flask import Flask, request, jsonify

app = Flask(__name__)
_env = None
_env_lock = threading.Lock()


def _make_env(num_products: int):
    """创建 WebShop 文本环境（绕过 gym wrapper，直接实例化 WebAgentTextEnv）。

    gym 的 OrderEnforcing wrapper 的 reset 不接受 session 参数，
    导致无法传 task_index 选 goal。直接实例化 WebAgentTextEnv 可绕过此限制。
    """
    import gym
    from web_agent_site.envs import WebAgentTextEnv

    # 方案 1（推荐）：直接实例化，完全绕过 gym wrapper
    try:
        from web_agent_site.envs.web_agent_text_env import WebAgentTextEnv as _WATE
        env = _WATE(num_products=num_products, observation_mode="text_rich")
        print(f"[webshop_server] 直接实例化 WebAgentTextEnv 成功, observation_mode=text_rich")
        return env
    except Exception as e:
        print(f"[webshop_server] 直接实例化失败: {e}, 回退 gym.make")

    # 方案 2：回退到 gym.make（wrapper 限制下 reset 无法传 task_index）
    candidates = [
        lambda: gym.make("WebAgentTextEnv-v0", num_products=num_products, observation_mode="text_rich"),
        lambda: gym.make("WebAgentTextEnv-v0", num_products=num_products, observation_mode="text"),
        lambda: gym.make("WebAgentTextEnv-v0", num_products=num_products),
        lambda: gym.make("WebAgentTextEnv-v0"),
    ]
    last_err: Optional[BaseException] = None
    for c in candidates:
        try:
            return c()
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"无法创建 WebShop gym 环境: {last_err}")


def _get_env(num_products: int):
    """懒加载并缓存全局 gym 环境（线程安全）。"""
    global _env
    if _env is None:
        with _env_lock:
            if _env is None:
                _env = _make_env(num_products)
                _env.reset()
    return _env


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


def _match_task_by_instruction(env, instruction: str) -> Optional[int]:
    """按 instruction 与所有 goal 的词重叠度，找最匹配的 task_index。

    WebShop 真实环境的 goal 是随机分配的，若 PECS 的 instruction 是"找绿茶"
    但随机到"找运动鞋"，LLM 按指令搜索必然 0 分。本函数遍历所有 goal，
    取与 instruction 关键词重叠最高的 task_index，保证 goal 与 instruction 语义一致。
    """
    if not instruction:
        return None
    try:
        # goals 在 SimServer 里（env.server.goals），不在 env.unwrapped.goals
        inner = env.unwrapped if hasattr(env, "unwrapped") else env
        server = getattr(inner, "server", None) or inner
        goals = getattr(server, "goals", None)
        if not goals:
            return None
        import re as _re
        inst_tokens = set(_re.findall(r"[a-z]+", instruction.lower()))
        stop = {"find", "me", "a", "an", "the", "with", "under", "for", "and", "of", "to", "in", "on", "at", "least", "bags", "count"}
        inst_tokens -= stop
        if not inst_tokens:
            return None
        best_idx, best_score = None, 0
        for idx, g in enumerate(goals):
            g_text = g.get("instruction_text", "") if isinstance(g, dict) else str(g)
            g_tokens = set(_re.findall(r"[a-z]+", g_text.lower())) - stop
            if not g_tokens:
                continue
            overlap = len(inst_tokens & g_tokens) / max(len(inst_tokens), 1)
            if overlap > best_score:
                best_score, best_idx = overlap, idx
        # 诊断日志：最佳匹配结果
        best_goal = goals[best_idx].get("instruction_text", "")[:100] if best_idx is not None else "无"
        # 至少 25% 关键词重叠才采用
        return best_idx if best_score >= 0.25 else None
    except Exception:
        return None


@app.route("/reset", methods=["POST"])
def reset():
    data = request.get_json(silent=True) or {}
    num = int(data.get("num_products", 1000))
    env = _get_env(num)
    task_index = data.get("task_index")
    instruction = data.get("instruction", "")
    # 若未显式指定 task_index，但传了 instruction，按语义匹配最合适的 task
    if task_index is None and instruction:
        matched = _match_task_by_instruction(env, instruction)
        if matched is not None:
            task_index = matched
    # 关键修复：SimServer 用 session_id 缓存 goal，若 session_id 已存在则复用旧 goal
    # （L511 if session_id not in user_sessions 才会分配新 goal）。
    # env.reset(session) 的 session 若是 int，会同时作为 session_id 和 goal 索引（L246-247）。
    # 所以传 task_index(int) 作为 session，每次都唯一（不同 task_index 产生不同 session_id），
    # 强制 receive 按 task_index 重新分配 goal。
    # 直接用 env 实例（已绕过 wrapper），reset(session) 可传 task_index 选 goal
    try:
        if task_index is not None:
            out = env.reset(int(task_index))
        else:
            out = env.reset()
    except TypeError:
        out = env.reset()
    except Exception:
        out = env.reset()
    if isinstance(out, tuple) and len(out) >= 2:
        obs, info = out[0], out[1]
        goal = info.get("goal", "") if isinstance(info, dict) else ""
    else:
        obs, goal = out, ""
    # WebShop reset 返回 (obs, None)，goal 藏在 obs 的 Instruction 行里
    # text_rich 模式: "Instruction: \nFind me...\n" ；html 模式: <h4>Instruction:<br>Find me...</h4>
    if not goal and obs:
        import re as _re_goal
        # text_rich/text 模式：Instruction: 后换行，goal 在下一行直到换行结束
        m = _re_goal.search(r'Instruction:\s*\n\s*(Find me[^\n]+)', obs)
        if not m:
            # html 模式兜底
            m = _re_goal.search(r'instruction-text[^>]*>.*?<h4>(.*?)</h4>', obs, _re_goal.S)
        if m:
            goal = m.group(1).replace("<br>", "").replace("Instruction:", "").strip()
    return jsonify({"session_id": str(task_index) if task_index is not None else "default", "goal": str(goal), "observation": str(obs)})


@app.route("/step", methods=["POST"])
def step():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", ""))
    env = _get_env(int(data.get("num_products", 1000)))
    obs, reward, done, info = env.step(action)
    return jsonify(
        {
            "observation": str(obs),
            "reward": float(reward if reward is not None else 0.0),
            "done": bool(done),
            "info": info if isinstance(info, dict) else {},
        }
    )


def main():
    parser = argparse.ArgumentParser(description="WebShop text-env HTTP bridge")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--num-products", type=int, default=1000)
    args = parser.parse_args()

    try:
        _get_env(args.num_products)
        print(f"[webshop_server] env ready (num_products={args.num_products})")
    except Exception as e:  # 预热失败也先起服务，便于排查
        print(f"[webshop_server] WARN: env init failed: {e}")

    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
