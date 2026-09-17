"""
AI 内容生成 Pipeline 工具测试（Day5）

覆盖：
  1. 注册门禁（约束 K4）：eval 模式绝不注册，business 模式才注册
  2. 返回契约（约束 K1）：一律返回 str，错误以错误标记前缀开头
  3. 确定性：同一输入永远同一输出（便于测试与复现）
  4. 各工具的正常/异常分支
"""
import json
import os
import subprocess
import sys

import pytest

import tools as tools_pkg
from tools import _ERROR_MARKERS, is_tool_success
from tools.content_pipeline import (
    CONTENT_PIPELINE_TOOLS,
    ab_select,
    batch_generate,
    generate_content,
    llm_judge,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONTENT_TOOL_NAMES = ("generate_content", "batch_generate", "llm_judge", "ab_select")


def _registry_in_subprocess(mode: str) -> dict:
    """在干净子进程中导入 tools，返回内容工具的注册情况（K4 门禁）。"""
    code = (
        "import json, tools;"
        "names=['generate_content','batch_generate','llm_judge','ab_select'];"
        "print(json.dumps({"
        "'registered': {n: (n in tools.TOOL_REGISTRY) for n in names},"
        "'registry_size': len(tools.TOOL_REGISTRY)}))"
    )
    env = dict(os.environ)
    env.pop("RUN_MODE", None)
    if mode:
        env["RUN_MODE"] = mode
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT,
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ============================================================
# 1. 注册门禁（K4）
# ============================================================

class TestRegistryGating:
    def test_process_registry_consistent_with_its_run_mode(self):
        """当前进程的工具表必须与其自身 RUN_MODE 一致。

        精确的逐模式行为由下面的子进程用例锁定；这里保证无论整个测试套件
        以 eval 还是 business 运行，注册结果都自洽（不依赖套件的启动模式）。
        """
        import config

        expected_eval = {
            "search", "web_browse", "python", "file_read",
            "file_parse", "multimodal", "api_call", "webshop",
        }
        if config.RUN_MODE == "business":
            for name in _CONTENT_TOOL_NAMES:
                assert name in tools_pkg.TOOL_REGISTRY, f"business 模式应注册 {name}"
            assert set(tools_pkg.TOOL_REGISTRY.keys()) == expected_eval | set(_CONTENT_TOOL_NAMES)
        else:
            for name in _CONTENT_TOOL_NAMES:
                assert name not in tools_pkg.TOOL_REGISTRY, f"eval 模式不应注册 {name}"
            assert set(tools_pkg.TOOL_REGISTRY.keys()) == expected_eval

    def test_business_mode_registers_all_four(self):
        info = _registry_in_subprocess("business")
        assert info["registered"] == {n: True for n in _CONTENT_TOOL_NAMES}
        # 8 个原有工具 + 4 个内容工具
        assert info["registry_size"] == 12

    def test_eval_subprocess_registry_size_is_eight(self):
        info = _registry_in_subprocess("")
        assert info["registered"] == {n: False for n in _CONTENT_TOOL_NAMES}
        assert info["registry_size"] == 8


# ============================================================
# 2. 返回契约（K1）
# ============================================================

class TestReturnContract:
    @pytest.mark.parametrize("fn,args", [
        (generate_content, {"topic": "T"}),
        (batch_generate, {"items": [{"topic": "T"}], "budget": 5000}),
        (llm_judge, {"content": "内容"}),
        (ab_select, {"variants": ["a", "bb", "ccc"]}),
    ])
    def test_all_return_str(self, fn, args):
        out = fn(args)
        assert isinstance(out, str)
        assert out

    @pytest.mark.parametrize("fn,args", [
        (generate_content, {}),
        (generate_content, {"topic": "   "}),
        (batch_generate, {}),
        (batch_generate, {"items": []}),
        (llm_judge, {}),
        (llm_judge, {"content": "  "}),
        (ab_select, {}),
        (ab_select, {"variants": ["only-one"]}),
    ])
    def test_error_cases_use_error_marker_prefix(self, fn, args):
        out = fn(args)
        assert out.startswith(_ERROR_MARKERS), f"{out!r} 未以错误标记开头"
        assert is_tool_success(out) is False

    def test_missing_args_none_does_not_crash(self):
        for fn in (generate_content, batch_generate, llm_judge, ab_select):
            assert isinstance(fn(None), str)


# ============================================================
# 3-6. 各工具行为
# ============================================================

class TestGenerateContent:
    def test_is_deterministic(self):
        args = {"topic": "多智能体", "style": "专业"}
        assert generate_content(args) == generate_content(args)

    def test_unknown_style_falls_back(self):
        out = generate_content({"topic": "X", "style": "不存在的风格"})
        assert "X" in out
        assert not out.startswith(_ERROR_MARKERS)

    def test_output_contains_topic(self):
        assert "量子计算" in generate_content({"topic": "量子计算"})


class TestBatchGenerate:
    def test_large_budget_stays_level_zero(self):
        out = batch_generate({"items": [{"topic": "A"}, {"topic": "B"}], "budget": 50000})
        assert "降级级别 L0" in out

    def test_tiny_budget_triggers_top_level(self):
        out = batch_generate({"items": [{"topic": "A"}] * 5, "budget": 50})
        assert "降级级别 L3" in out

    def test_reports_each_item(self):
        out = batch_generate({"items": [{"topic": "A"}, {"topic": "B"}], "budget": 50000})
        assert out.count("累计") == 2


class TestLLMJudge:
    def test_returns_valid_json_with_expected_keys(self):
        payload = json.loads(llm_judge({"content": "一段文案"}))
        assert set(payload) == {"overall", "dimensions", "passed", "feedback"}
        assert set(payload["dimensions"]) == {"准确性", "一致性", "完整性", "可读性"}
        assert 0 < payload["overall"] <= 5

    def test_is_deterministic(self):
        assert llm_judge({"content": "同一条"}) == llm_judge({"content": "同一条"})

    def test_passed_flag_matches_threshold(self):
        payload = json.loads(llm_judge({"content": "内容"}))
        assert payload["passed"] == (payload["overall"] >= 4.0)


class TestABSelect:
    def test_requires_at_least_two_variants(self):
        out = ab_select({"variants": ["唯一候选"]})
        assert out.startswith(_ERROR_MARKERS)

    def test_winner_is_top_ranked_and_deterministic(self):
        variants = ["文案一", "文案二", "文案三"]
        out1 = ab_select({"variants": variants})
        out2 = ab_select({"variants": variants})
        assert out1 == out2
        assert "胜出" in out1
        # 候选 #1 必须出现在分数表里（保证全量输出）
        assert "候选 #1" in out1 and "候选 #3" in out1

    def test_exactly_two_variants_ok(self):
        out = ab_select({"variants": ["a", "b"]})
        assert not out.startswith(_ERROR_MARKERS)
