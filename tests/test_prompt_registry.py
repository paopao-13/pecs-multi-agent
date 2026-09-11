"""Prompt 版本注册表与回滚端点测试。

覆盖：
  - resolve：基线逐字回退 / 覆盖文件生效 / 空覆盖文件视为无效 / 读失败回退
  - set_active_version：合法切换 / 非法版本号拒绝
  - has_override / status 输出契约
  - API 端点：/health 暴露版本、/admin/prompt/rollback 的鉴权与租户权限
"""
import json

import pytest

from tools import prompt_registry as pr


@pytest.fixture(autouse=True)
def _restore_version(monkeypatch, tmp_path):
    """每个用例独立：版本回 v0、工作目录切到 tmp（隔离 prompts/ 相对路径）。"""
    monkeypatch.chdir(tmp_path)  # override_path 用相对路径 prompts/v{N}/
    pr.set_active_version("v0")
    yield
    pr.set_active_version("v0")


BASELINE = "基线提示词"


def test_resolve_baseline_when_no_override():
    """无覆盖文件 → 逐字返回基线（v0 默认态）。"""
    assert pr.resolve("planner", BASELINE) == BASELINE


def test_resolve_override_file_used(tmp_path):
    """v1 目录有覆盖文件 → 用文件内容。"""
    d = tmp_path / "prompts" / "v1"
    d.mkdir(parents=True)
    (d / "planner.txt").write_text("新版提示词", encoding="utf-8")
    pr.set_active_version("v1")
    assert pr.resolve("planner", BASELINE) == "新版提示词"
    # 其他角色仍回基线（按角色独立覆盖）
    assert pr.resolve("critic", BASELINE) == BASELINE


def test_resolve_empty_override_ignored(tmp_path):
    """空覆盖文件视为无效（防误放空 prompt 打崩 LLM 调用）。"""
    d = tmp_path / "prompts" / "v1"
    d.mkdir(parents=True)
    (d / "planner.txt").write_text("", encoding="utf-8")
    pr.set_active_version("v1")
    assert pr.resolve("planner", BASELINE) == BASELINE


def test_resolve_missing_version_falls_back_to_baseline(tmp_path):
    """切到不存在的版本（如 v99）→ 等价于回基线（宽松处理）。"""
    pr.set_active_version("v99")
    assert pr.resolve("planner", BASELINE) == BASELINE


def test_resolve_baseline_explicit_version_arg():
    """显式传 version 参数时不看当前激活版本。"""
    pr.set_active_version("v1")
    assert pr.resolve("planner", BASELINE, version="v0") == BASELINE


def test_set_active_version_rejects_invalid():
    with pytest.raises(ValueError):
        pr.set_active_version("latest")
    with pytest.raises(ValueError):
        pr.set_active_version("")
    with pytest.raises(ValueError):
        pr.set_active_version("vx")


def test_status_contract(tmp_path):
    d = tmp_path / "prompts" / "v1"
    d.mkdir(parents=True)
    (d / "critic.txt").write_text("x", encoding="utf-8")
    pr.set_active_version("v1")
    s = pr.status()
    assert s["version"] == "v1"
    assert s["sources"]["critic"] == f"override:{pr.override_path('critic', 'v1')}"
    assert s["sources"]["planner"] == "baseline"
    assert set(s["sources"]) == set(pr.ROLES)


# ============ API 端点 ============

def test_health_exposes_prompt_version(monkeypatch):
    import scripts.api as api
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")
    with TestClient(api.app) as client:
        body = client.get("/health").json()
    assert body["prompt_version"] == pr.get_active_version()


def test_admin_endpoints_auth_flow(monkeypatch):
    """回滚端点：未启用鉴权时放行（与鉴权体系一致）；启用后非 admin 租户 403。

    test_auth 的 monkeypatch 惯例：直接改 auth 模块的表与开关。
    """
    import scripts.api as api
    import scripts.auth as auth
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "LLM_API_KEY", "")
    monkeypatch.setattr(auth, "_KEY_TABLE", {"k1": "tenant_a", "k2": "tenant_jixiang"})
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)

    with TestClient(api.app) as client:
        r_no_key = client.post("/admin/prompt/rollback?target=v1")
        r_forbidden = client.post(
            "/admin/prompt/rollback?target=v1", headers={"X-API-Key": "k1"}
        )
        r_admin = client.post(
            "/admin/prompt/rollback?target=v1", headers={"X-API-Key": "k2"}
        )
        r_status = client.get("/admin/prompt/status", headers={"X-API-Key": "k2"})
        r_bad = client.post(
            "/admin/prompt/rollback?target=bogus", headers={"X-API-Key": "k2"}
        )

    assert r_no_key.status_code == 401
    assert r_forbidden.status_code == 403
    assert r_admin.status_code == 200
    assert r_admin.json()["switched_to"] == "v1"
    assert r_status.status_code == 200
    assert r_status.json()["version"] == "v1"
    assert r_bad.status_code == 400
    pr.set_active_version("v0")  # 恢复，防污染其他用例
