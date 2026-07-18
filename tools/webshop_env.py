"""
真实 WebShop 环境 HTTP 客户端（PECS 侧）

== 架构澄清（重要）==
真实 WebShop 文本环境（princeton-nlp/webshop 的 WebAgentTextEnv-v0）是
「进程内」运行的：它依赖本地的 pyserini 搜索引擎（需 Java + 商品索引 +
spaCy 模型），并不是简单地连一个 Flask :3000 HTTP 服务。

为了避免与 PECS 自身环境（Python 3.13 + langgraph 等）的版本/依赖冲突，
我们把 WebShop 跑在「独立环境」里（conda py3.8 或 Docker 容器），并起一个
轻量 HTTP 桥（tools/webshop_server.py）暴露 reset/step 接口；
PECS 这边只做 HTTP 客户端，无需安装 webshop / gym / Java。

== 接口（与 tools/webshop.py 的调用保持完全一致）==
    env = WebShopEnv(server_url="http://localhost:8000")
    goal, obs = env.reset(task_index=0)        # 取一道购物任务 + 初始观察
    obs, reward, done, info = env.step("search[green tea]")
    env.health()                                # 探活

== 切换开关 ==
设置环境变量 WEBSHOP_SERVER_URL 即启用真实环境（tools/webshop.py 的
use_real_env() 据此判断），PECS 业务代码无需改动。端口指向 HTTP 桥（:8000）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

try:
    import requests
except ImportError:  # 兜底：用标准库，保证 PECS 侧零额外依赖
    requests = None
    import json as _json
    import urllib.request


_DEFAULT_URL = os.environ.get("WEBSHOP_SERVER_URL", "http://localhost:8000").rstrip("/")


class WebShopEnvError(RuntimeError):
    """真实 WebShop 环境无法连接/配置时抛出。"""


class WebShopEnv:
    """WebShop 文本环境的 HTTP 客户端（连 tools/webshop_server.py）。"""

    def __init__(self, server_url: Optional[str] = None, max_steps: int = 15):
        self.server_url = (server_url or _DEFAULT_URL).rstrip("/")
        self.max_steps = max_steps
        self._session_id: Optional[str] = None

    # ---- 底层 HTTP ------------------------------------------------------
    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.server_url}{path}"
        if requests is not None:
            resp = requests.post(url, json=payload, timeout=180)
            resp.raise_for_status()
            return resp.json()
        data = _json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            return _json.loads(r.read().decode())

    # ---- 交互 ----------------------------------------------------------
    def reset(self, task_index: Optional[int] = None) -> Tuple[str, str]:
        """开始一道任务。返回 (购物目标文本, 初始观察文本)。"""
        body: Dict[str, Any] = {"num_products": 1000}
        if task_index is not None:
            body["task_index"] = int(task_index)
        d = self._post("/reset", body)
        self._session_id = d.get("session_id")
        return str(d.get("goal", "")), str(d.get("observation", ""))

    def step(self, action: str) -> Tuple[str, float, bool, Dict[str, Any]]:
        """执行一个动作。返回 (观察, 奖励, 是否结束, 附加信息)。"""
        d = self._post("/step", {"action": str(action), "session_id": self._session_id})
        return (
            str(d.get("observation", "")),
            float(d.get("reward", 0.0) or 0.0),
            bool(d.get("done", False)),
            d.get("info", {}) or {},
        )

    def health(self) -> bool:
        """探活：HTTP 桥是否就绪。"""
        try:
            if requests is not None:
                return (
                    requests.get(f"{self.server_url}/health", timeout=10)
                    .json()
                    .get("status")
                    == "ok"
                )
            req = urllib.request.Request(f"{self.server_url}/health")
            with urllib.request.urlopen(req, timeout=10) as r:
                return _json.loads(r.read().decode()).get("status") == "ok"
        except Exception:
            return False

    def close(self):
        self._session_id = None
