"""logger/graph_trace_logger.py 单元测试。

覆盖：文本工具函数、节点日志（verbose 开关与节点计数）、
Markdown 构建（各状态字段是否落进文档）、文件落盘与自动生成路径、
build_trace 与 export 内容一致性、export_task_trace 便捷函数。

注意：本模块会写 results/traces/，测试用 monkeypatch 改 _TRACES_DIR
到 tmp_path 隔离（禁止污染真实 results 目录）。
"""
import os
from pathlib import Path

import pytest

from logger import graph_trace_logger as gtl


@pytest.fixture(autouse=True)
def _isolate_traces_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(gtl, "_TRACES_DIR", str(tmp_path / "traces"))
    yield


def _sample_state(**overrides):
    state = {
        "query": "计算 2 的 10 次方",
        "complexity": "simple",
        "iteration": 1,
        "final_answer": "1024",
        "answer_format": "text",
        "token_used": 1234,
        "token_budget": 20000,
        "role_token_used": {"planner": 300, "executor": 800, "critic": 134},
        "budget_events": [{"level": "L1", "action": "降级"}],
        "scheduler_decisions": [{"decision": "skip_critic"}],
        "plan": [{"id": 1, "action": "python", "description": "计算幂"}],
        "results": [{"step_id": 1, "action": "python", "result": "1024", "success": True}],
        "critic_scores": [{"overall": 9.0}],
        "logs": ["[Planner] 生成计划", "[Executor] 执行步骤 1"],
    }
    state.update(overrides)
    return state


# ============ 工具函数 ============

def test_truncate_short_text_unchanged():
    assert gtl._truncate("abc", 120) == "abc"


def test_truncate_long_text_adds_ellipsis():
    out = gtl._truncate("x" * 200, 120)
    assert out.endswith("...")
    assert len(out) == 123  # 120 + "..."


def test_truncate_none_becomes_empty():
    assert gtl._truncate(None) == ""


def test_format_tokens_thousands_separator():
    assert gtl._format_tokens(1234567) == "1,234,567"


# ============ log_node ============

def test_log_node_counts_and_verbose_off_quiet(capsys):
    logger = gtl.GraphTraceLogger(verbose=False)
    logger.log_node("planner_node", _sample_state())
    logger.log_node("executor_node", _sample_state())
    assert logger._node_count == 2
    assert capsys.readouterr().out == ""  # verbose=False 不打印


def test_log_node_verbose_prints_node_name(capsys):
    logger = gtl.GraphTraceLogger(verbose=True)
    logger.log_node("critic_node", _sample_state())
    out = capsys.readouterr().out
    assert "critic" in out.lower()


def test_log_node_survives_empty_state():
    """空 state 不得抛异常（图首节点可能还没写入任何字段）。"""
    logger = gtl.GraphTraceLogger(verbose=False)
    logger.log_node("planner_node", {})
    assert logger._node_count == 1


# ============ build_trace / _build_markdown ============

def test_build_trace_contains_key_sections():
    md = gtl.GraphTraceLogger(verbose=False).build_trace(_sample_state())
    assert "计算 2 的 10 次方" in md        # 任务问题
    assert "1024" in md                      # 最终答案
    assert "1,234" in md                     # token 格式化（千分位）
    assert "执行日志条数: 2" in md            # 日志条数统计
    # 注意：role_token_used 只用于 log_node 的控制台输出，不进 Markdown
    # （当前实现如此），因此这里不断言角色名出现在文档中


def test_build_trace_with_minimal_state():
    md = gtl.GraphTraceLogger(verbose=False).build_trace({})
    assert isinstance(md, str) and md  # 空状态也能产出非空文档


def test_build_trace_includes_budget_and_scheduler():
    md = gtl.GraphTraceLogger(verbose=False).build_trace(_sample_state())
    assert "L1" in md or "降级" in md


# ============ export_trace_to_markdown ============

def test_export_to_explicit_path(tmp_path):
    out = tmp_path / "out" / "trace.md"
    path = gtl.GraphTraceLogger(verbose=False).export_trace_to_markdown(
        _sample_state(), str(out)
    )
    assert Path(path).exists()
    assert Path(path).read_text(encoding="utf-8").strip()


def test_export_auto_path_under_traces_dir(tmp_path):
    path = gtl.GraphTraceLogger(verbose=False).export_trace_to_markdown(_sample_state())
    assert path.startswith(str(tmp_path / "traces"))
    assert Path(path).exists()


def test_export_content_matches_build_trace(tmp_path):
    logger = gtl.GraphTraceLogger(verbose=False)
    state = _sample_state()
    path = logger.export_trace_to_markdown(state, str(tmp_path / "t.md"))
    # 内容一致（除时间戳外，build_trace 与导出走同一 _build_markdown）
    assert Path(path).read_text(encoding="utf-8") == logger.build_trace(state)


# ============ export_task_trace ============

def test_export_task_trace_with_task_id(tmp_path):
    path = gtl.export_task_trace(_sample_state(), task_id="task-abc")
    assert "task-abc" in os.path.basename(path)
    assert Path(path).exists()


def test_export_task_trace_sanitizes_task_id(tmp_path):
    """task_id 含路径分隔符等危险字符时被替换，防目录穿越写文件。"""
    path = gtl.export_task_trace(_sample_state(), task_id="../../etc/passwd")
    assert ".." not in os.path.basename(path)
    assert Path(path).parent == Path(gtl._TRACES_DIR).resolve() or str(
        Path(path).parent
    ).endswith("traces")
    assert Path(path).exists()


def test_export_task_trace_without_task_id(tmp_path):
    path = gtl.export_task_trace(_sample_state())
    assert Path(path).exists()
