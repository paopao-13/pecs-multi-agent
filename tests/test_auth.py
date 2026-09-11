"""API Key 鉴权与租户归属的单元测试。

覆盖：
  - 未配置 PECS_API_KEYS 时鉴权自动关闭（保证本地开发/CI/评测行为不变）
  - 缺失 / 错误 / 正确的 Key 分别对应 401 / 401 / 放行
  - 跨租户访问 thread_id 必须 404（且不能泄露资源是否存在）
  - 端到端：/run_task 与 /api/replay 的鉴权接线正确
"""
import pytest
from fastapi import HTTPException, Request

import scripts.auth as auth
import scripts.api as api


def _make_request(headers=None) -> Request:
    """构造最小 Request（只需 headers 即可满足鉴权依赖）"""
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


@pytest.fixture
def _auth_on(monkeypatch):
    """开启鉴权：两张 key，分属 t1 / t2"""
    monkeypatch.setattr(auth, "_KEY_TABLE", {"key-t1": "t1", "key-t2": "t2"})
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)


@pytest.fixture
def _auth_off(monkeypatch):
    monkeypatch.setattr(auth, "_KEY_TABLE", {})
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)


# ------------------------------------------------ 未启用时完全放行
def test_disabled_auth_returns_anonymous(_auth_off):
    assert auth.require_api_key(_make_request()) == "-"


def test_disabled_auth_allows_any_thread(_auth_off):
    auth.assert_thread_owner("someone-else-task", "-")  # 不抛异常即通过


# ------------------------------------------------ 启用后的校验
def test_missing_key_rejected(_auth_on):
    with pytest.raises(HTTPException) as e:
        auth.require_api_key(_make_request())
    assert e.value.status_code == 401


def test_wrong_key_rejected(_auth_on):
    with pytest.raises(HTTPException) as e:
        auth.require_api_key(_make_request({"X-API-Key": "nope"}))
    assert e.value.status_code == 401


def test_valid_key_returns_tenant(_auth_on):
    assert auth.require_api_key(_make_request({"X-API-Key": "key-t1"})) == "t1"
    assert auth.require_api_key(_make_request({"X-API-Key": "key-t2"})) == "t2"


def test_owner_thread_allowed(_auth_on):
    auth.assert_thread_owner("t1-abc-123", "t1")  # 不抛异常即通过


def test_cross_tenant_thread_returns_404(_auth_on):
    """跨租户必须 404 —— 403 会确认资源存在，等于泄露 thread_id 有效性"""
    with pytest.raises(HTTPException) as e:
        auth.assert_thread_owner("t1-abc-123", "t2")
    assert e.value.status_code == 404


# ------------------------------------------------ 端到端接线
def test_run_task_requires_key_when_enabled(monkeypatch, _auth_on):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")  # 避免启动期打网络
    with TestClient(api.app) as client:
        r_no_key = client.post("/run_task", json={"query": "hi"})
        r_ok = client.post(
            "/run_task", json={"query": "hi"}, headers={"X-API-Key": "key-t1"}
        )
        r_cross = client.post(
            "/run_task",
            json={"query": "hi", "thread_id": "t2-xxx"},
            headers={"X-API-Key": "key-t1"},
        )

    assert r_no_key.status_code == 401
    # 有 key 但 LLM 未配置 → 走到依赖检查，返回 503（说明鉴权已放行）
    assert r_ok.status_code == 503
    # 跨租户 thread_id → 404（且发生在 503 之前）
    assert r_cross.status_code == 404


def test_replay_requires_key(monkeypatch, _auth_on):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")
    with TestClient(api.app) as client:
        r_no_key = client.get("/api/replay/t1-abc")
        r_cross = client.get("/api/replay/t2-abc", headers={"X-API-Key": "key-t1"})

    assert r_no_key.status_code == 401
    assert r_cross.status_code == 404


def test_run_task_works_without_auth_configured(monkeypatch, _auth_off):
    """未启用鉴权时，既有调用方式必须完全不变（向后兼容的关键回归）"""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")
    with TestClient(api.app) as client:
        r = client.post("/run_task", json={"query": "hi"})
    # 未鉴权 → 放行到依赖检查 → 503（而非 401）
    assert r.status_code == 503


def test_metrics_requires_key_when_enabled(monkeypatch, _auth_on):
    """/metrics 与 /metrics/prom：任何有效 Key 可拉取，匿名拒绝。

    取舍：指标是系统级数据（不含租户隔离业务内容），因此不限定单一
    prometheus Key——业务租户排查自己调用时也需要看指标。
    /health 系列保持无鉴权（K8s probe 不注入 Key），一并回归。
    """
    from fastapi.testclient import TestClient

    with TestClient(api.app) as client:
        r_no_key = client.get("/metrics")
        r_ok = client.get("/metrics", headers={"X-API-Key": "key-t2"})
        r_prom_ok = client.get("/metrics/prom", headers={"X-API-Key": "key-t1"})
        r_prom_no = client.get("/metrics/prom")
        r_health = client.get("/health")  # probe 必须免 Key

    assert r_no_key.status_code == 401
    assert r_ok.status_code == 200
    assert r_prom_ok.status_code in (200, 503)  # 200 正常；503 仅当 prom 多进程未初始化
    assert r_prom_no.status_code == 401
    assert r_health.status_code == 200


def test_metrics_open_without_auth_configured(monkeypatch, _auth_off):
    """未启用鉴权时 /metrics 保持匿名可达（向后兼容回归）。"""
    from fastapi.testclient import TestClient

    with TestClient(api.app) as client:
        assert client.get("/metrics").status_code == 200
