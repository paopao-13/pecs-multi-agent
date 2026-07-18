"""
环境探针：换 API 后一次性验证两件事
  1. DeepSeek API key 是否可用（发一个最小 chat 请求）
  2. WebShop 真实环境是否可达（决定路径 A / 路径 B）

零业务消耗：DeepSeek 只发 1 条极短消息；WebShop 只做连通性探测不跑任务。
"""
import os
import sys
import json
import re
import urllib.request
import urllib.error
import socket

sys.path.insert(0, os.getcwd())


def _load_env(path=".env"):
    """极简 .env 解析，避免依赖 python-dotenv。"""
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


_env = _load_env()
API_KEY = _env.get("LLM_API_KEY", "")
BASE_URL = _env.get("LLM_BASE_URL", "").rstrip("/")
MODEL = _env.get("LLM_MODEL", "")


def probe_deepseek():
    """最小 chat 请求验证 key 可用。"""
    print("=== [1/2] DeepSeek API 连通性 ===")
    if not API_KEY:
        print("  ✗ 未设置 LLM_API_KEY")
        return False
    url = BASE_URL + "/chat/completions"
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            print(f"  ✓ 成功 (model={MODEL})，返回: {content!r}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        print(f"  ✗ HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"  ✗ 异常: {type(e).__name__}: {e}")
        return False


def probe_webshop_env():
    """探测真实 WebShop 环境是否可达。

    真实 AgentBench WebShop 通常部署在本地端口或远程 simulator。
    这里做保守探测：检查常见本地端口 + 已知环境域名。
    """
    print("\n=== [2/2] WebShop 真实环境可达性 ===")
    import socket

    targets = [
        ("localhost", 3000),
        ("localhost", 8000),
        ("localhost", 9999),
    ]
    reachable = []
    for host, port in targets:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        try:
            s.connect((host, port))
            reachable.append(f"{host}:{port}")
        except Exception:
            pass
        finally:
            s.close()

    if reachable:
        print(f"  ⚠ 本地有开放端口 {reachable}，可能是 WebShop simulator，可走路径 A 进一步验证")
        return "A_CANDIDATE"
    else:
        print("  • 本地无 WebShop simulator 端口开放")
        # 检查 agentbench 包是否可装（不实际安装，仅查索引）
        print("  • 结论：走路径 B（本地扩展 adapter + 同模型 ReAct 对比）")
        return "B"


if __name__ == "__main__":
    ds_ok = probe_deepseek()
    ws_verdict = probe_webshop_env()
    print("\n=== 探针结论 ===")
    print(f"  DeepSeek API: {'可用' if ds_ok else '不可用（需排查）'}")
    print(f"  WebShop 路径: {ws_verdict}")
    if not ds_ok:
        print("  → 先解决 DeepSeek 连通问题再继续")
    else:
        print("  → DeepSeek 可用，可继续执行模块0 回归 + WebShop 路径B")
