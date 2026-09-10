"""
链路回放测试（Day5）

覆盖：
  1. load_task_state：缺失库 / 未知 thread_id / 真实往返读取
  2. /api/replay/{thread_id}：检查点不存在 404、thread 不存在 404、
     命中时返回 state + 成本归因 + 完整链路 markdown
  3. /run_task 的 thread_id 字段可接收（持久化入口）

写检查点用「最小 StateGraph + SqliteSaver」，读取走生产用的
graph.builder.load_task_state —— 两者共用 AgentState，通道对齐。
"""
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

import scripts.api as api
from graph.builder import load_task_state
from graph.state import AgentState


def _persist_state(db_path: str, thread_id: str, **fields) -> None:
    """用最小图把一份状态写进 SQLite 检查点。"""
    def node(_state):
        return dict(fields)

    graph = StateGraph(AgentState)
    graph.add_node("n", node)
    graph.set_entry_point("n")
    graph.add_edge("n", END)
    with SqliteSaver.from_conn_string(db_path) as saver:
        compiled = graph.compile(checkpointer=saver)
        compiled.invoke(
            AgentState(query=fields.get("query", "q")),
            {"configurable": {"thread_id": thread_id}},
        )


def _empty_db(path: str) -> str:
    """建一个合法的空 SQLite 库（用于「有库但无该 thread」的场景）。"""
    sqlite3.connect(path).close()
    return path


# ============================================================
# 0. 检查点目录处理（边界：裸文件名）
# ============================================================

class TestEnsureDbDir:
    def test_bare_filename_does_not_raise(self):
        """PEC_CHECKPOINT_DB=cp.sqlite 时 dirname 为空串，不能因此抛 FileNotFoundError"""
        api._ensure_db_dir("cp.sqlite")  # 不应抛异常

    def test_nested_path_is_created(self, tmp_path):
        target = tmp_path / "sub" / "deeper" / "cp.sqlite"
        api._ensure_db_dir(str(target))
        assert target.parent.is_dir()

    def test_existing_dir_is_idempotent(self, tmp_path):
        api._ensure_db_dir(str(tmp_path / "cp.sqlite"))
        api._ensure_db_dir(str(tmp_path / "cp.sqlite"))
        assert tmp_path.is_dir()


# ============================================================
# 1. load_task_state
# ============================================================

class TestLoadTaskState:
    def test_missing_db_returns_empty(self, tmp_path):
        assert load_task_state("nope", str(tmp_path / "missing.sqlite")) == {}

    def test_unknown_thread_returns_empty(self, tmp_path):
        db = _empty_db(str(tmp_path / "cp.sqlite"))
        assert load_task_state("ghost", db) == {}

    def test_round_trip_reads_persisted_state(self, tmp_path):
        db = str(tmp_path / "cp.sqlite")
        _persist_state(
            db, "t1",
            query="列出前三个质数", final_answer="2, 3, 5",
            token_used=321, token_budget=50000, step_count=2,
            role_token_used={"planner": 60, "executor": 200, "critic": 41, "synthesizer": 20},
        )
        state = load_task_state("t1", db)
        assert state["query"] == "列出前三个质数"
        assert state["final_answer"] == "2, 3, 5"
        assert state["token_used"] == 321
        assert state["step_count"] == 2


# ============================================================
# 2. /api/replay/{thread_id}
# ============================================================

class TestReplayEndpoint:
    def setup_method(self):
        self.client = TestClient(api.app)

    def test_404_when_checkpoint_file_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(api, "CHECKPOINT_DB", str(tmp_path / "nope.sqlite"))
        resp = self.client.get("/api/replay/anything")
        assert resp.status_code == 404
        assert "检查点" in resp.json()["detail"]

    def test_404_when_thread_unknown(self, monkeypatch, tmp_path):
        db = _empty_db(str(tmp_path / "cp.sqlite"))
        monkeypatch.setattr(api, "CHECKPOINT_DB", db)
        resp = self.client.get("/api/replay/ghost")
        assert resp.status_code == 404
        assert "ghost" in resp.json()["detail"]

    def test_200_returns_state_cost_report_and_trace(self, monkeypatch, tmp_path):
        db = str(tmp_path / "cp.sqlite")
        _persist_state(
            db, "t9",
            query="列出前三个质数", final_answer="2, 3, 5",
            token_used=500, token_budget=50000, step_count=2, iteration=0,
            role_token_used={"planner": 100, "executor": 250, "critic": 100, "synthesizer": 50},
            budget_events=[
                {"role": "planner", "tokens": 100, "iteration": 0, "degrade_level": 0},
                {"role": "executor", "tokens": 250, "iteration": 0, "degrade_level": 0},
                {"role": "critic", "tokens": 100, "iteration": 0, "degrade_level": 0},
                {"role": "synthesizer", "tokens": 50, "iteration": 0, "degrade_level": 0},
            ],
            results=[{"action": "python", "result": "2, 3, 5", "step_id": 1, "success": True}],
        )
        monkeypatch.setattr(api, "CHECKPOINT_DB", db)

        resp = self.client.get("/api/replay/t9")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["thread_id"] == "t9"
        assert body["state"]["final_answer"] == "2, 3, 5"
        assert body["state"]["token_used"] == 500

        # 成本归因必须自洽
        report = body["cost_report"]
        assert report["total_tokens"] == 500
        assert report["attribution"]["consistent"] is True
        assert report["by_role"]["executor"] == 250

        # 完整链路 markdown（复用 GraphTraceLogger）
        assert "执行链路日志" in body["trace_markdown"]
        assert "2, 3, 5" in body["trace_markdown"]


# ============================================================
# 3. /run_task 的持久化入口
# ============================================================

class TestRunTaskThreadId:
    def test_request_model_accepts_thread_id(self):
        req = api.RunTaskRequest(query="q", thread_id="t-abc")
        assert req.thread_id == "t-abc"

    def test_thread_id_is_optional(self):
        assert api.RunTaskRequest(query="q").thread_id is None

    def test_over_long_query_still_rejected_with_thread_id(self):
        """长度校验优先于执行，带 thread_id 也不放行超长输入"""
        client = TestClient(api.app)
        resp = client.post(
            "/run_task",
            json={"query": "x" * (10 ** 5), "thread_id": "t-xyz"},
        )
        assert resp.status_code == 413
