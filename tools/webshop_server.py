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
    """尝试常见 gym id 创建文本环境（不同 WebShop 版本注册名略有差异）。"""
    import gym
    from web_agent_site.envs import WebAgentTextEnv

    candidates = [
        # 新版 WebAgentTextEnv 已固定文本模式，可能不接受 observation_mode / num_products 形参
        lambda: gym.make("WebAgentTextEnv-v0", num_products=num_products),
        lambda: gym.make("WebAgentTextEnv-v0"),
        # 老版本注册名，带显式文本模式
        lambda: gym.make("WebShop-v0", observation_mode="text", num_products=num_products),
        lambda: gym.make("WebShop-v1", observation_mode="text", num_products=num_products),
    ]
    last_err: Optional[BaseException] = None
    for c in candidates:
        try:
            return c()
        except Exception as e:  # noqa: BLE001  尝试下一种
            last_err = e
    raise RuntimeError(f"无法创建 WebShop gym 环境（已尝试 WebAgentTextEnv-v0/WebShop-v0/v1）: {last_err}")


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


@app.route("/reset", methods=["POST"])
def reset():
    data = request.get_json(silent=True) or {}
    num = int(data.get("num_products", 1000))
    env = _get_env(num)
    task_index = data.get("task_index")
    try:
        out = env.reset(int(task_index)) if task_index is not None else env.reset()
    except TypeError:
        out = env.reset()
    if isinstance(out, tuple) and len(out) >= 2:
        obs, info = out[0], out[1]
        goal = info.get("goal", "") if isinstance(info, dict) else ""
    else:
        obs, goal = out, ""
    return jsonify({"session_id": "default", "goal": str(goal), "observation": str(obs)})


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
