"""测试 benchmarks/gaia_official.py 的答案判定逻辑

GAIA 官方 benchmark 的核心纯逻辑函数：
- _normalize_official: 答案归一化（去冠词/标点/转小写/合并空格）
- _parse_number: 数字解析（整数/小数/千分位/科学计数/分数）
- _numbers_match: 数字匹配（允许 1% 相对误差）
- _list_match: 列表匹配（逗号分隔，顺序无关）
- _string_match: 字符串匹配（精确/包含/前缀）
- check_data_leakage: 数据泄露检查（学术诚信防护）
- mcnemar_test: McNemar 统计显著性检验
"""
import pytest

from benchmarks.gaia_official import (
    _normalize_official,
    _parse_number,
    _numbers_match,
    _list_match,
    _string_match,
    check_data_leakage,
    mcnemar_test,
    evaluate_answer_official,
)


# ============================================================
# 答案归一化
# ============================================================

class TestNormalizeOfficial:
    """测试 _normalize_official: 去冠词/标点/转小写/合并空格"""

    @pytest.mark.parametrize("input_text,expected", [
        ("The MIT", "mit"),                    # 去冠词 the + 转小写
        ("Hello, World!", "hello world"),      # 去标点逗号感叹号
        ("  Multiple   Spaces  ", "multiple spaces"),  # 合并多余空格
    ])
    def test_normalize(self, input_text, expected):
        assert _normalize_official(input_text) == expected

    def test_normalize_empty(self):
        assert _normalize_official("") == ""
        assert _normalize_official(None) == ""


# ============================================================
# 数字解析
# ============================================================

class TestParseNumber:
    """测试 _parse_number: 整数/小数/千分位/科学计数/分数"""

    @pytest.mark.parametrize("input_text,expected", [
        ("17", 17.0),                          # 整数
        ("3.14", 3.14),                        # 小数
        ("1,072,693,248", 1072693248.0),       # 千分位
        ("1.5e6", 1500000.0),                  # 科学计数小写 e
        ("1.5E6", 1500000.0),                  # 科学计数大写 E (bug #3 复现)
    ])
    def test_parse_number(self, input_text, expected):
        assert _parse_number(input_text) == expected

    def test_parse_number_fraction(self):
        """分数解析: 3/4 → 0.75"""
        assert _parse_number("3/4") == 0.75

    def test_parse_number_with_unit(self):
        """带单位: '17 hours' → 17.0 (提取首个数字)"""
        assert _parse_number("17 hours") == 17.0

    def test_parse_number_empty(self):
        assert _parse_number("") is None
        assert _parse_number(None) is None

    def test_parse_number_non_numeric(self):
        """纯文本无法解析为数字"""
        assert _parse_number("hello world") is None


# ============================================================
# 数字匹配
# ============================================================

class TestNumbersMatch:
    """测试 _numbers_match: 精确匹配/容差匹配/越界不匹配"""

    @pytest.mark.parametrize("pred,truth,expected", [
        ("17", "17", True),               # 精确匹配
        ("17.001", "17", True),           # 容差内 (相对误差 < 1%)
        ("18", "17", False),              # 越界 (相对误差 > 1%)
    ])
    def test_numbers_match(self, pred, truth, expected):
        assert _numbers_match(pred, truth) == expected

    def test_numbers_match_none_input(self):
        """非数字输入不匹配"""
        assert _numbers_match("hello", "17") is False
        assert _numbers_match("17", "hello") is False


# ============================================================
# 列表匹配
# ============================================================

class TestListMatch:
    """测试 _list_match: 逗号分隔列表，顺序无关"""

    @pytest.mark.parametrize("pred,truth,expected", [
        ("Alice, Bob, Charlie", "Charlie, Alice, Bob", True),  # 顺序无关
        ("Alice, Bob", "Alice, Bob, Charlie", False),          # 数量不同
        ("The MIT, Stanford", "mit, stanford", True),          # 归一化后匹配
    ])
    def test_list_match(self, pred, truth, expected):
        assert _list_match(pred, truth) == expected

    def test_list_match_empty(self):
        assert _list_match("", "Alice") is False
        assert _list_match("Alice", "") is False


# ============================================================
# 字符串匹配
# ============================================================

class TestStringMatch:
    """测试 _string_match: 精确/包含/前缀匹配"""

    @pytest.mark.parametrize("pred,truth,expected", [
        ("Paris", "Paris", True),            # 精确匹配
        ("The answer is Paris", "Paris", True),  # 包含匹配
        ("Paris is the capital", "Paris", True), # 前缀匹配
    ])
    def test_string_match(self, pred, truth, expected):
        assert _string_match(pred, truth) == expected

    def test_string_match_empty(self):
        assert _string_match("", "Paris") is False
        assert _string_match("Paris", "") is False


# ============================================================
# 数据泄露检查（学术诚信防护 - 故事性测试）
# ============================================================

class TestCheckDataLeakage:
    """测试 check_data_leakage: 检测 ground_truth 是否泄露在 question 中

    返回 True = 有泄露（危险），False = 安全
    这是学术诚信红线：GAIA validation set 答案是公开的，
    如果答案出现在 LLM 的 prompt 里会导致评测结果无效。
    """

    def test_leakage_truth_in_question(self):
        """故事性测试：标准答案直接出现在题目中 → 检测到泄露

        背景：GAIA validation set 答案公开，
        如果题目文本包含答案，LLM 可能直接复述而非真正推理。
        check_data_leakage 在评测前拦截这类题目。
        """
        question = "请计算并回答：答案是 Paris 对吗？"
        ground_truth = "Paris"
        assert check_data_leakage(question, ground_truth) is True

    def test_leakage_normalized_match(self):
        """归一化后泄露：The MIT → mit 出现在题目中"""
        question = "which university is mit known for?"
        ground_truth = "The MIT"
        assert check_data_leakage(question, ground_truth) is True

    def test_leakage_number_boundary_safe(self):
        """数字边界安全：答案 17 不应误报为出现在 2017 中

        word boundary \\b 确保数字作为独立 token 出现才算泄露。
        2017 中的 17 前面是 0（非 word boundary），不匹配。
        """
        question = "2017年发生了什么事件？"
        ground_truth = "17"
        assert check_data_leakage(question, ground_truth) is False

    def test_leakage_short_answer_skipped(self):
        """短答案（长度<2）跳过包含检查，避免误报"""
        # ground_truth="a" 归一化后 len < 2，不做包含检查
        question = "what is a pen?"
        ground_truth = "a"
        assert check_data_leakage(question, ground_truth) is False

    def test_leakage_empty_input(self):
        """空输入返回安全（无泄露）"""
        assert check_data_leakage("", "Paris") is False
        assert check_data_leakage("some question", "") is False
        assert check_data_leakage("", "") is False


# ============================================================
# McNemar 检验
# ============================================================

class TestMcNemarTest:
    """测试 mcnemar_test: 配对统计显著性检验"""

    def test_mcnemar_length_mismatch(self):
        """两个列表长度不一致 → 抛 ValueError"""
        with pytest.raises(ValueError, match="长度必须一致"):
            mcnemar_test([True, False], [True])

    def test_mcnemar_all_agree(self):
        """b=c=0（两者答案完全一致）→ 不显著"""
        pecs = [True, True, True, False, False]
        react = [True, True, True, False, False]
        result = mcnemar_test(pecs, react)
        assert result["b"] == 0
        assert result["c"] == 0
        assert result["statistic"] == 0.0
        assert result["p_value"] == 1.0
        assert result["significant"] is False

    def test_mcnemar_significant(self):
        """差异显著：b=20, c=5 → p < 0.05"""
        # PECS 对 ReAct 错：20 个；PECS 错 ReAct 对：5 个
        pecs = [True] * 20 + [False] * 5
        react = [False] * 20 + [True] * 5
        result = mcnemar_test(pecs, react)
        assert result["b"] == 20
        assert result["c"] == 5
        assert result["significant"] is True
        assert result["p_value"] < 0.05

    def test_mcnemar_not_significant(self):
        """差异不显著：b=8, c=2 → p≈0.11 > 0.05（实际评测结果）"""
        pecs = [True] * 8 + [False] * 2
        react = [False] * 8 + [True] * 2
        result = mcnemar_test(pecs, react)
        assert result["b"] == 8
        assert result["c"] == 2
        assert result["significant"] is False
        assert result["p_value"] > 0.05


# ============================================================
# LLM 兜底判定（mock call_llm）— bug #2 修复
# ============================================================

class TestEvaluateAnswerLLMFallback:
    """测试 evaluate_answer_official 的 LLM 兜底判定

    bug #2: "是" in result 误匹配 "不是"；"yes" in result.lower() 误匹配 "yesterday"
    修复: 改用 startswith 精确匹配

    输入构造: predicted="xxx", ground_truth="yyy"
    → 归一化不匹配 / 不含逗号 / 不是数字 / 字符串不包含
    → 进入 LLM 兜底分支
    """

    def test_llm_fallback_yes(self, monkeypatch):
        """mock 返回 '是' → True（正常路径）"""
        monkeypatch.setattr(
            "benchmarks.gaia_official.call_llm",
            lambda *a, **kw: ("是", 0),
        )
        assert evaluate_answer_official("xxx", "yyy") is True

    def test_llm_fallback_no(self, monkeypatch):
        """bug #2: mock 返回 '不是' → 应为 False（'是' in '不是' 误匹配为 True）"""
        monkeypatch.setattr(
            "benchmarks.gaia_official.call_llm",
            lambda *a, **kw: ("不是", 0),
        )
        assert evaluate_answer_official("xxx", "yyy") is False

    def test_llm_fallback_yesterday(self, monkeypatch):
        """bug #2 扩展: mock 返回 'yesterday' → 应为 False（'yes' in 'yesterday' 误匹配）"""
        monkeypatch.setattr(
            "benchmarks.gaia_official.call_llm",
            lambda *a, **kw: ("yesterday", 0),
        )
        assert evaluate_answer_official("xxx", "yyy") is False

    def test_llm_fallback_yes_english(self, monkeypatch):
        """mock 返回 'yes' → True（英文正常路径）"""
        monkeypatch.setattr(
            "benchmarks.gaia_official.call_llm",
            lambda *a, **kw: ("yes", 0),
        )
        assert evaluate_answer_official("xxx", "yyy") is True

    def test_llm_fallback_exception(self, monkeypatch):
        """mock 抛异常 → False（异常兜底）"""
        def mock_error(*a, **kw):
            raise Exception("API error")
        monkeypatch.setattr("benchmarks.gaia_official.call_llm", mock_error)
        assert evaluate_answer_official("xxx", "yyy") is False
