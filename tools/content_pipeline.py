"""
AI 内容生成 Pipeline 工具集（Mock，Day5）

业务背景（对齐真实实习场景：AI 内容生成后端）
  给定主题 → 批量生成多风格文案 → LLM 自动评测打分 → A/B 选优 → 交付。
  这四步正是「大模型应用」团队最常见的落地形态，也是成本最集中的环节
  （批量生成 + 自动评测都会成规模调用 LLM）。

为什么是 Mock
  本模块**不发起任何真实 LLM 调用**：全部为确定性纯 Python 实现，
  保证 Demo 在无 API Key、无网络的环境下可完整跑通（对齐 demos/quickstart_no_api.py）。
  真实接入时只需把 `_simulate_llm` 换成 LLM 网关调用，工具签名与返回契约不变。

【约束 K4】这四个工具只在 RUN_MODE=business 时注册进 TOOL_REGISTRY，
绝不污染 eval 模式（GAIA / WebShop 评测）的可用工具集。

【约束 K1】所有函数返回 str；失败以「错误」前缀开头，与 is_tool_success 一致。
"""
import hashlib
import json
from typing import Any, Dict, List

from config import (
    DEGRADE_THRESHOLD_1,
    DEGRADE_THRESHOLD_2,
    DEGRADE_THRESHOLD_3,
)
from logger.token_counter import count_tokens

# ---------------- 内部：确定性「伪 LLM」 ----------------

_STYLE_TEMPLATES = {
    "专业": "{topic}：从行业标准与实测数据出发，给出可落地的方案与边界条件。",
    "活泼": "Hey！关于{topic}，这条内容帮你 3 分钟抓住重点，看完就能上手～",
    "种草": "真的会谢！{topic} 用下来太香了，优点我都列在下面了，闭眼入不踩雷。",
    "严谨": "针对{topic}，本文按「问题定义—方法—验证—局限」四段式展开，结论附置信区间。",
}

_TOP_PRIORITY = [
    "痛点前置：首句必须点明读者要解决的问题",
    "卖点具象：用可验证的数字替代形容词",
    "行动收口：结尾给一个明确、低门槛的下一步",
]

# 单次生成的固定开销（系统提示 + 参数 + 返回包装的模拟量）
_PROMPT_OVERHEAD_TOKENS = 64


def _stable_score(text: str, low: int = 60, high: int = 98) -> int:
    """由文本内容派生的稳定分数（同一输入永远同一分数，便于测试与复现）。"""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    span = high - low
    return low + int(digest[:8], 16) % (span + 1)


def _simulate_llm(prompt: str) -> str:
    """占位：真实接入时替换为 LLM 网关调用（带超时/重试/节流）。"""
    return f"[simulated-llm] {prompt[:40]}"


def _get(args: Any, key: str, default=None):
    return (args or {}).get(key, default)


# ---------------- 工具 1：单条文案生成 ----------------

def generate_content(args: Dict[str, Any]) -> str:
    """生成一条营销文案。

    args: {"topic": "主题", "style": "专业|活泼|种草|严谨"}
    """
    topic = str(_get(args, "topic", "") or "").strip()
    if not topic:
        return "错误：generate_content 缺少参数 topic"
    style = str(_get(args, "style", "专业") or "专业")
    template = _STYLE_TEMPLATES.get(style, _STYLE_TEMPLATES["专业"])
    body = template.format(topic=topic)
    _simulate_llm(f"为「{topic}」写一段{style}文案")
    lines = [
        f"【文案 · {style} · {topic}】",
        body,
        "",
        "结构建议：",
    ]
    lines += [f"  {i+1}. {p}" for i, p in enumerate(_TOP_PRIORITY)]
    lines.append(f"预估质量分：{_stable_score(body)}/100")
    return "\n".join(lines)


# ---------------- 工具 2：批量生成 + 预算降级 ----------------

def batch_generate(args: Dict[str, Any]) -> str:
    """批量生成文案，并展示 token 预算分配与三级降级。

    args: {"items": [{"topic": "...", "style": "..."}, ...], "budget": 50000}

    成本模型：单条成本 = 生成内容的实际 token 数 + 固定开销（系统提示 + 参数）。
    用真实分词器计数，因此预算大小会自然地把任务推到不同降级级别。
    """
    items: List[Dict[str, Any]] = list(_get(args, "items", []) or [])
    if not items:
        return "错误：batch_generate 缺少参数 items（非空列表）"
    budget = int(_get(args, "budget", 50000) or 50000)

    out = [f"批量生成 {len(items)} 条（任务预算 {budget} tokens）", ""]
    used = 0
    for i, item in enumerate(items, 1):
        topic = str(item.get("topic", "") or "").strip() or f"主题{i}"
        style = str(item.get("style", "专业") or "专业")
        body = _STYLE_TEMPLATES.get(style, _STYLE_TEMPLATES["专业"]).format(topic=topic)
        cost = count_tokens(body) + _PROMPT_OVERHEAD_TOKENS
        used += cost
        ratio = used / budget if budget else 0
        out.append(f"{i:>2}. [{style}] {topic}")
        out.append(f"    {body}")
        out.append(
            f"    本条 {cost} tokens，累计 {used}/{budget}"
            f"（{ratio:.1%}，降级级别 L{_degrade_level(ratio)}）"
        )

    out.append("")
    out.append(_degrade_note(used, budget))
    return "\n".join(out)


def _degrade_level(ratio: float) -> int:
    if ratio >= DEGRADE_THRESHOLD_3:
        return 3
    if ratio >= DEGRADE_THRESHOLD_2:
        return 2
    if ratio >= DEGRADE_THRESHOLD_1:
        return 1
    return 0


def _degrade_note(used: int, budget: int) -> str:
    ratio = used / budget if budget else 0
    level = _degrade_level(ratio)
    actions = {
        0: "正常模式：全量生成 + 全量评审",
        1: "L1：跳过低风险条目的 Critic 评审（阈值 {:.0%}）".format(DEGRADE_THRESHOLD_1),
        2: "L2：合并剩余条目，减少 LLM 往返（阈值 {:.0%}）".format(DEGRADE_THRESHOLD_2),
        3: "L3：停止新增生成，直接用已有结果拼装交付（阈值 {:.0%}）".format(DEGRADE_THRESHOLD_3),
    }
    return f"降级级别 L{level} —— {actions[level]}"


# ---------------- 工具 3：LLM 自动评测（LLM-as-judge） ----------------

_JUDGE_DIMENSIONS = ("准确性", "一致性", "完整性", "可读性")


def llm_judge(args: Dict[str, Any]) -> str:
    """对文案做多维自动评测（模拟 LLM-as-judge）。

    args: {"content": "待评测文案"}
    返回 JSON 字符串，维度与 agents/critic.py 的 CriticScore 对齐。
    """
    content = str(_get(args, "content", "") or "")
    if not content.strip():
        return "错误：llm_judge 缺少参数 content"
    scores = {}
    for dim in _JUDGE_DIMENSIONS:
        scores[dim] = _stable_score(f"{dim}:{content}", low=3, high=5)
    overall = round(sum(scores.values()) / len(scores), 2)
    payload = {
        "overall": overall,
        "dimensions": scores,
        "passed": overall >= 4.0,
        "feedback": (
            "达标，可进入 A/B 选优"
            if overall >= 4.0
            else "未达标：建议补强痛点的具体数据支撑后重生成"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


# ---------------- 工具 4：A/B 选优 ----------------

def ab_select(args: Dict[str, Any]) -> str:
    """在多个候选文案中选优（按 llm_judge 的 overall 分数）。

    args: {"variants": ["文案A", "文案B", ...]}
    """
    variants = list(_get(args, "variants", []) or [])
    if len(variants) < 2:
        return "错误：ab_select 至少需要 2 个候选 variants"
    scored = []
    for i, v in enumerate(variants):
        content = str(v or "")
        dims = {d: _stable_score(f"{d}:{content}", low=3, high=5) for d in _JUDGE_DIMENSIONS}
        scored.append((round(sum(dims.values()) / len(dims), 2), i, content))
    scored.sort(key=lambda x: (-x[0], x[1]))
    best_score, best_idx, best = scored[0]
    runner = scored[1][0]
    lines = [
        f"A/B 选优：{len(variants)} 个候选",
        f"胜出：候选 #{best_idx + 1}（综合 {best_score}，次优 {runner}，领先 {round(best_score - runner, 2)}）",
        "",
        "胜出文案：",
        best[:300],
        "",
        "全部得分：",
    ]
    lines += [f"  候选 #{i + 1}: {s}" for s, i, _ in scored]
    return "\n".join(lines)


# ---------------- 注册信息（由 tools/__init__.py 在 business 模式合并） ----------------

CONTENT_PIPELINE_TOOLS = {
    "generate_content": generate_content,
    "batch_generate": batch_generate,
    "llm_judge": llm_judge,
    "ab_select": ab_select,
}

CONTENT_PIPELINE_DESCRIPTIONS = {
    "generate_content": "内容生成工具。输入主题与风格，生成一条营销文案。适用于AI内容生成任务。",
    "batch_generate": "批量内容生成工具。输入条目列表与预算，批量生成并展示预算分配与降级。适用于规模化内容生产。",
    "llm_judge": "LLM自动评测工具。输入文案，返回多维评分（准确性/一致性/完整性/可读性）。适用于内容质量把关。",
    "ab_select": "A/B选优工具。输入多个候选文案，按评测分数选优。适用于文案迭代择优。",
}
