"""
LLM 依赖故障显式化测试（D1/D2 修复）

背景（为什么需要这组测试）：
  既有实现里 LLM 调用失败是"静默"的 —— call_llm 重试耗尽后返回一段
  "[LLM调用失败] ..." 文本，Planner 拿去做 json.loads 抛 JSONDecodeError，
  最终产出空计划；Synthesizer 则返回"无法生成答案"。调用方（API）看到的
  是一个 success=True 的空答案，**无从区分**"任务本身无解"与"依赖故障"。

覆盖：
  1. LLM 失败信号原语（LLM_FAILURE_PREFIX / is_llm_failure / LLMInvocationError）
  2. call_llm_json 在失败时显式抛出，而非抛误导性的 JSONDecodeError
  3. Planner / Synthesizer 把失败写入 AgentState.llm_error（可机读）
  4. api._probe_llm 启动探测（三态 ok / auth_error / unknown）+ 结论到启动状态的映射
  5. /run_task 对"LLM 失败 + 零步骤"返回 success=False（而非 200+空答案）
"""
import pytest
from fastapi.testclient import TestClient

import scripts.api as api
from agents.llm_utils import (
    LLM_FAILURE_PREFIX,
    LLMInvocationError,
    call_llm_json,
    is_llm_failure,
)
from agents.planner import planner_node
from agents.synthesizer import synthesizer_node
from graph.state import AgentState


# ============================================================
# 1. 失败信号原语
# ============================================================

class TestFailurePrimitives:
    def test_prefix_is_the_convention(self):
        """前缀必须与项目既有约定一致（heuristics/synthesizer/run_resumable 均按此判定）"""
        assert LLM_FAILURE_PREFIX == "[LLM调用失败]"

    def test_is_llm_failure_matches_prefix(self):
        assert is_llm_failure("[LLM调用失败] 401 Unauthorized") is True

    @pytest.mark.parametrize("text", ["", None, "正常答案", "部分提到 [LLM调用失败] 但不是开头"])
    def test_is_llm_failure_rejects_others(self, text):
        assert is_llm_failure(text) is False

    def test_agent_state_has_llm_error_default_none(self):
        assert AgentState().llm_error is None
        assert AgentState(query="q").llm_error is None


# ============================================================
# 2. call_llm_json 显式抛出
# ============================================================

class TestCallLlmJsonRaises:
    def test_failure_raises_llm_invocation_error(self, monkeypatch):
        """call_llm 返回失败文本 → call_llm_json 应抛 LLMInvocationError，
        而不是在非 JSON 文本上抛 JSONDecodeError（后者会掩盖真实原因）"""
        monkeypatch.setattr(
            "agents.llm_utils.call_llm",
            lambda *a, **kw: (f"{LLM_FAILURE_PREFIX} RuntimeError: boom", 0),
        )
        with pytest.raises(LLMInvocationError):
            call_llm_json("p", "s", role="planner")

    def test_failure_error_message_carries_reason(self, monkeypatch):
        monkeypatch.setattr(
            "agents.llm_utils.call_llm",
            lambda *a, **kw: (f"{LLM_FAILURE_PREFIX} 401 Unauthorized", 0),
        )
        with pytest.raises(LLMInvocationError) as ei:
            call_llm_json("p", "s")
        assert "401" in str(ei.value)

    def test_valid_json_still_parses(self, monkeypatch):
        """回归：正常 JSON 不受影响"""
        monkeypatch.setattr(
            "agents.llm_utils.call_llm",
            lambda *a, **kw: ('{"steps": []}', 7),
        )
        data, tokens = call_llm_json("p", "s")
        assert data == {"steps": []}
        assert tokens == 7


# ============================================================
# 3. Planner / Synthesizer 上报 llm_error
# ============================================================

def _base_state(**over):
    state = {
        "query": "请简要介绍量子计算的发展历史与主要里程碑事件",  # 知识类，非确定性任务
        "token_used": 0,
        "token_budget": 50000,
        "logs": [],
        "use_heuristics": True,
    }
    state.update(over)
    return state


class TestPlannerReportsError:
    def test_llm_failure_sets_llm_error(self, monkeypatch):
        monkeypatch.setattr("agents.planner.LLM_API_KEY", "sk-test")
        def boom(*a, **kw):
            raise LLMInvocationError(f"{LLM_FAILURE_PREFIX} 401")
        monkeypatch.setattr("agents.planner.call_llm_json", boom)

        out = planner_node(_base_state())
        assert out["llm_error"], "Planner 两次 LLM 调用都失败时必须记录 llm_error"
        assert "LLMInvocationError" in out["llm_error"]

    def test_no_failure_keeps_llm_error_none(self, monkeypatch):
        monkeypatch.setattr("agents.planner.LLM_API_KEY", "sk-test")
        monkeypatch.setattr(
            "agents.planner.call_llm_json",
            lambda *a, **kw: ({"steps": [{"action": "search", "description": "d", "args": {}}]}, 12),
        )
        out = planner_node(_base_state())
        assert out["llm_error"] is None


class TestSynthesizerReportsError:
    def _res(self):
        return [{"action": "search", "description": "d", "result": "r", "step_id": 1, "success": True}]

    def test_llm_failure_sets_llm_error(self, monkeypatch):
        monkeypatch.setattr("agents.synthesizer.LLM_API_KEY", "sk-test")
        monkeypatch.setattr(
            "agents.synthesizer.call_llm",
            lambda *a, **kw: (f"{LLM_FAILURE_PREFIX} 401", 0),
        )
        out = synthesizer_node(_base_state(results=self._res()))
        assert out["llm_error"], "Synthesizer LLM 综合失败时必须记录 llm_error"

    def test_preserves_upstream_llm_error(self, monkeypatch):
        """上游（Planner）已记录失败，本节点成功综合时不得把它'洗掉'"""
        monkeypatch.setattr("agents.synthesizer.LLM_API_KEY", "sk-test")
        monkeypatch.setattr(
            "agents.synthesizer.call_llm",
            lambda *a, **kw: ("这是一个足够长的正常综合答案，用于通过反思阈值判断。", 20),
        )
        out = synthesizer_node(
            _base_state(results=self._res(), llm_error="Planner 失败原因")
        )
        assert out["llm_error"] == "Planner 失败原因"


# ============================================================
# 4. api._probe_llm 启动探测（只看鉴权结论，不等模型生成）
# ============================================================

class _FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class TestStartupProbe:
    """探测返回三态：ok / auth_error / unknown。

    设计要点（实测依据）：所用模型多为 reasoning 模型，单次生成耗时 4.8~38.4s
    剧烈波动；若用「固定超时等一次对话完成」做探测，会把"模型只是慢"误判为
    "依赖不可用"，导致启动即 503。故改为 GET /models，只看鉴权结论。
    """

    def test_ok_when_200(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeResp(200, '{"data":[]}'))
        status, reason = api._probe_llm()
        assert status == "ok"
        assert "通过" in reason

    def test_auth_error_on_401(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeResp(401, '{"error":"Invalid token"}'))
        status, reason = api._probe_llm()
        assert status == "auth_error"
        assert "401" in reason

    def test_auth_error_on_403(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeResp(403, "forbidden"))
        status, _ = api._probe_llm()
        assert status == "auth_error"

    def test_unknown_on_other_status(self, monkeypatch):
        """端点不支持 /models（404）等 → 未获结论，不得误判为凭据错误"""
        monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeResp(404, "not found"))
        status, _ = api._probe_llm()
        assert status == "unknown"

    def test_unknown_on_network_exception(self, monkeypatch):
        def boom(*a, **kw):
            raise ConnectionError("network down")
        monkeypatch.setattr("requests.get", boom)
        status, reason = api._probe_llm()
        assert status == "unknown"
        assert "ConnectionError" in reason

    def test_request_uses_bearer_auth_and_timeout(self, monkeypatch):
        seen = {}

        def spy(url, headers=None, timeout=None):
            seen["url"] = url
            seen["auth"] = (headers or {}).get("Authorization", "")
            seen["timeout"] = timeout
            return _FakeResp(200, "{}")

        monkeypatch.setattr("requests.get", spy)
        api._probe_llm()
        assert seen["url"].endswith("/models")
        assert seen["auth"].startswith("Bearer ")
        assert seen["timeout"] == api.LLM_PROBE_TIMEOUT_S


class TestStartupProbeMapping:
    """探测结论 → 启动自检状态的映射（决定 /run_task 是否 fail-fast）"""

    def test_ok_is_configured(self):
        ok, reason = api._resolve_startup_from_probe("ok", "凭据有效")
        assert ok is True
        assert "未获结论" not in reason

    def test_auth_error_is_not_configured(self):
        ok, _ = api._resolve_startup_from_probe("auth_error", "401")
        assert ok is False

    def test_unknown_is_permissive(self):
        """未获结论时宁可放行：503 会把服务整体摘流，代价高于"先放行"。
        若依赖真的不可用，任务失败会由 llm_error 显式上报。"""
        ok, reason = api._resolve_startup_from_probe("unknown", "超时")
        assert ok is True
        assert "未获结论" in reason


# ============================================================
# 5. /run_task 依赖故障显式化
# ============================================================

class TestRunTaskFailsOnEmptyLlmRun:
    def setup_method(self):
        self.client = TestClient(api.app)

    def test_zero_steps_with_llm_error_returns_failure(self, monkeypatch):
        monkeypatch.setitem(api._STARTUP, "llm_configured", True)
        monkeypatch.setattr(
            api,
            "_execute_graph",
            lambda *a, **kw: {
                "final_answer": "无法生成答案：没有执行任何步骤。",
                "token_used": 0,
                "step_count": 0,
                "llm_error": "LLMInvocationError: [LLM调用失败] 401",
                "cost_report": None,
            },
        )
        resp = self.client.post("/run_task", json={"query": "测试依赖故障"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is False
        assert "LLM 依赖失败" in (body["error"] or "")
        assert body["steps"] == 0

    def test_normal_run_still_succeeds(self, monkeypatch):
        monkeypatch.setitem(api._STARTUP, "llm_configured", True)
        monkeypatch.setattr(
            api,
            "_execute_graph",
            lambda *a, **kw: {
                "final_answer": "42",
                "token_used": 120,
                "step_count": 2,
                "llm_error": None,
                "cost_report": None,
            },
        )
        resp = self.client.post("/run_task", json={"query": "1+1=?"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["final_answer"] == "42"

    def test_llm_error_but_steps_present_is_not_fatal(self, monkeypatch):
        """LLM 失败但启发式兜底产出了步骤 → 不算失败（依赖降级而非空跑）"""
        monkeypatch.setitem(api._STARTUP, "llm_configured", True)
        monkeypatch.setattr(
            api,
            "_execute_graph",
            lambda *a, **kw: {
                "final_answer": "启发式兜底答案",
                "token_used": 0,
                "step_count": 3,
                "llm_error": "Planner 失败但启发式已兜底",
                "cost_report": None,
            },
        )
        resp = self.client.post("/run_task", json={"query": "计算 2+2"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True


class TestHealthExposesReason:
    def test_health_has_llm_reason_key(self):
        client = TestClient(api.app)  # 不触发 lifespan
        body = client.get("/health").json()
        assert "llm_reason" in body
        assert "llm_configured" in body
