"""metrics/error_stat.py 单元测试（纯函数模块，无网络依赖）。

覆盖：错误分类关键词映射、Critic 拦截日志提取（两种日志格式）、
details 数组递归查找、单任务详情分析、结果文件统计聚合。
"""
import json

import pytest

from metrics import error_stat as es


# ============ classify_error ============

@pytest.mark.parametrize(
    "text,expected",
    [
        ("搜索参数不正确，请修正", "tool_param_error"),
        ("tool_input 缺少必填字段", "tool_param_error"),
        ("计划缺少搜索步骤", "plan_logic_omission"),
        ("该结论未经搜索验证", "plan_logic_omission"),
        ("结果不完整，信息不足", "result_incomplete"),
        ("返回内容过少", "result_incomplete"),
        ("完全无关的一句话", "other"),
        ("", "other"),
    ],
)
def test_classify_error(text, expected):
    assert es.classify_error(text) == expected


def test_classify_error_priority_order():
    """命中顺序按 ERROR_PATTERNS 声明顺序（先参数错误，再计划遗漏）。"""
    # 同时含两类关键词 → 取先声明的类别
    text = "参数错误，且计划缺少步骤"
    assert es.classify_error(text) == "tool_param_error"


def test_classify_error_regex_patterns():
    """正则型关键词（未包含.*步骤 / 未经.*验证）也能命中。"""
    assert es.classify_error("计划中未包含必要的搜索步骤") == "plan_logic_omission"
    assert es.classify_error("该数值未经检索验证") == "plan_logic_omission"


# ============ extract_critic_interceptions ============

def test_extract_interception_feedback_format():
    logs = ["[Critic] 步骤 2 不合格，反馈: 搜索参数不准确，请修正关键词"]
    got = es.extract_critic_interceptions(logs)
    assert len(got) == 1
    assert got[0]["step_id"] == 2
    assert got[0]["error_type"] == "tool_param_error"
    assert "搜索参数不准确" in got[0]["feedback"]


def test_extract_score_log_without_reject_is_ignored():
    """仅"评分"日志不提取：前置过滤要求日志含"不合格"。

    这是当前实现的真实行为——RE_REJECT 先过滤，RE_SCORE_FEEDBACK 是
    "已判定不合格但没匹配到'反馈:'"时的兜底。单独一条评分日志（未判
    不合格）本就不该计入拦截。
    """
    logs = ["[Critic] 规则验证步骤 3: 评分 2.7 (结果不完整，信息不足)"]
    assert es.extract_critic_interceptions(logs) == []


def test_extract_score_fallback_when_rejected():
    """含"不合格"但无"反馈:"字样时，走评分支提取括号内的内容。"""
    logs = ["[Critic] 步骤 1 不合格 规则验证步骤 1: 评分 2.7 (结果不完整)"]
    got = es.extract_critic_interceptions(logs)
    assert len(got) == 1
    assert got[0]["error_type"] == "result_incomplete"
    assert "结果不完整" in got[0]["feedback"]


def test_extract_ignores_non_reject_logs():
    logs = ["[Executor] 步骤 1 执行完成", "[Critic] 步骤 1 合格，评分 9.0"]
    assert es.extract_critic_interceptions(logs) == []


def test_extract_skips_non_string_entries():
    """非字符串日志项不得抛异常（历史数据里混过 dict）。"""
    logs = [None, 123, {"a": 1}, "[Critic] 步骤 1 不合格，反馈: 参数错误"]
    got = es.extract_critic_interceptions(logs)
    assert len(got) == 1
    assert got[0]["error_type"] == "tool_param_error"


def test_extract_without_feedback_falls_back_to_other():
    logs = ["[Critic] 步骤 5 不合格"]
    got = es.extract_critic_interceptions(logs)
    assert got[0]["feedback"] == ""
    assert got[0]["error_type"] == "other"
    assert got[0]["step_id"] == 5


# ============ find_details_arrays ============

def test_find_details_arrays_nested():
    obj = {
        "details": [{"task_id": "a"}],
        "nested": {"list": [{"details": [{"task_id": "b"}]}]},
    }
    found = es.find_details_arrays(obj)
    paths = [p for p, _ in found]
    assert "details" in paths
    assert any("nested" in p for p in paths)
    assert sum(len(v) for _, v in found) == 2


def test_find_details_arrays_empty():
    assert es.find_details_arrays({"a": 1}) == []
    assert es.find_details_arrays([]) == []


# ============ analyze_detail ============

def test_analyze_detail_no_logs_returns_none():
    assert es.analyze_detail({"task_id": "x"}) is None


def test_analyze_detail_no_interception_returns_none():
    assert es.analyze_detail({"task_id": "x", "logs": ["正常执行"]}) is None


def test_analyze_detail_success_and_failure():
    detail = {
        "task_id": "t1",
        "question": "1+1?",
        "logs": ["[Critic] 步骤 1 不合格，反馈: 参数错误"],
        "correct": True,
    }
    got = es.analyze_detail(detail)
    assert got["interception_count"] == 1
    assert got["final_correct"] is True
    assert got["correction_successful"] is True

    detail["correct"] = False
    got2 = es.analyze_detail(detail)
    assert got2["correction_successful"] is False


def test_analyze_detail_uses_instruction_as_question_fallback():
    detail = {
        "task_id": "t2",
        "instruction": "买一个红色书包",
        "logs": ["[Critic] 步骤 1 不合格，反馈: 结果不完整"],
        "success": True,
    }
    got = es.analyze_detail(detail)
    assert got["question"] == "买一个红色书包"
    assert got["final_correct"] is True  # success 也能表征最终正确


# ============ run_statistics（端到端，用临时目录） ============

def _write_result(tmp_path, name: str, payload: dict):
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_run_statistics_aggregates(tmp_path, monkeypatch):
    _write_result(tmp_path, "r1.json", {
        "details": [
            {
                "task_id": "a", "question": "q1", "correct": True,
                "logs": ["[Critic] 步骤 1 不合格，反馈: 参数错误"],
            },
            {
                "task_id": "b", "question": "q2", "correct": False,
                "logs": ["[Critic] 步骤 2 不合格，反馈: 结果不完整"],
            },
            {"task_id": "c", "question": "q3", "correct": True, "logs": ["正常"]},
        ]
    })
    monkeypatch.setattr(es, "RESULTS_DIR", str(tmp_path))
    summary = es.run_statistics(str(tmp_path))

    assert summary["total_files_analyzed"] == 1
    assert summary["total_interceptions"] == 2
    assert summary["total_tasks_intercepted"] == 2
    # 一个修正成功（a），一个失败（b）
    assert summary["correction_success_count"] == 1
    assert summary["correction_success_rate"] == pytest.approx(0.5)
    # 错误类型分布
    by_type = summary["error_type_breakdown"]
    assert by_type["tool_param_error"] == 1
    assert by_type["result_incomplete"] == 1
    assert summary["statistics_type"] == "critic_error_interception"


def test_run_statistics_empty_dir(tmp_path):
    summary = es.run_statistics(str(tmp_path))
    assert summary["total_files_analyzed"] == 0
    assert summary["total_interceptions"] == 0
    assert summary["correction_success_rate"] == 0


def test_run_statistics_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        es.run_statistics(str(tmp_path / "no-such-dir"))
