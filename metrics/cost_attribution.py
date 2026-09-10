"""
成本归因（Cost Attribution）—— 把一次任务的 token 消耗拆到「角色 / 工具 / 轮次」

为什么需要它：
  只报一个总 token 数，回答不了「钱花在哪」。运维/面试场景真正的问题是：
    - 哪个角色最贵？（planner 规划太多轮？critic 反复评审？）
    - 哪个工具在烧钱？（工具返回内容越长，回填进 executor 上下文的 token 越多）
    - 第几轮反思把预算吃掉了？（哪一轮触发了 70/85/95% 降级）

数据来源（全部取自既有的 AgentState，不新增埋点）：
  role_token_used : 四角色累计消耗（graph/token_budget.py:record_token_usage）
  budget_events   : 逐条消耗事件（含 role / tokens / iteration / degrade_level）
  results         : 每步执行结果（供按工具归因）
  token_used / token_budget : 总量与预算

口径说明（避免误读）：
  - by_role 之和按构造等于总消耗（record_token_usage 同时累加两者），
    本模块显式校验这一点并给出 delta，用于发现埋点被绕过的情况。
  - by_tool 统计的是「工具结果回填进上下文所计的 token」（executor 里的
    estimate_tokens(result)），归属角色仍是 executor；它不是工具本身的开销，
    而是工具输出长度对成本的贡献。这点必须在报告里说清，否则会被误读。
  - by_iteration 依赖 budget_events 的 iteration 字段；缺失时归入轮次 0。
"""
from typing import Any, Dict

from graph.token_budget import ROLE_NAMES, estimate_tokens


def _get(state: Any, key: str, default=None):
    """同时兼容 AgentState（有 .get）与普通 dict。"""
    if state is None:
        return default
    if hasattr(state, "get"):
        return state.get(key, default)
    return default


def attribute_cost(state: Any) -> Dict[str, Any]:
    """把一次任务的 token 消耗拆分为角色 / 工具 / 轮次三个维度。

    参数:
        state: 任务结束后的 AgentState（或等价的 dict）

    返回:
        结构化归因报告（可直接 json.dumps）
    """
    token_used = int(_get(state, "token_used", 0) or 0)
    token_budget = int(_get(state, "token_budget", 0) or 0)
    role_token_used = dict(_get(state, "role_token_used", {}) or {})
    budget_events = list(_get(state, "budget_events", []) or [])
    results = list(_get(state, "results", []) or [])

    # ---- 维度 1：按角色 ----
    by_role = {name: int(role_token_used.get(name, 0) or 0) for name in ROLE_NAMES}
    role_sum = sum(by_role.values())
    by_role_ratio = {
        name: round(v / token_used, 4) if token_used else 0.0 for name, v in by_role.items()
    }
    top_role = max(by_role, key=by_role.get) if by_role else None

    # ---- 维度 2：按工具（工具输出回填的 token）----
    by_tool: Dict[str, Dict[str, int]] = {}
    for entry in results:
        action = (entry.get("action") if isinstance(entry, dict) else None) or "unknown"
        content = entry.get("result", "") if isinstance(entry, dict) else ""
        tokens = estimate_tokens(content or "")
        item = by_tool.setdefault(action, {"tokens": 0, "calls": 0})
        item["tokens"] += tokens
        item["calls"] += 1
    tool_sum = sum(v["tokens"] for v in by_tool.values())

    # ---- 维度 3：按反思轮次 ----
    by_iteration: Dict[str, Dict[str, Any]] = {}
    for event in budget_events:
        if not isinstance(event, dict):
            continue
        it = str(event.get("iteration", 0))
        bucket = by_iteration.setdefault(it, {"tokens": 0, "by_role": {}})
        tokens = int(event.get("tokens", 0) or 0)
        bucket["tokens"] += tokens
        role = event.get("role", "unknown")
        bucket["by_role"][role] = bucket["by_role"].get(role, 0) + tokens

    # ---- 一致性校验：角色之和应等于总消耗 ----
    delta = token_used - role_sum
    delta_ratio = round(delta / token_used, 4) if token_used else 0.0
    consistent = abs(delta_ratio) < 0.01  # 允许 <1% 误差

    degrade_level = 0
    if budget_events:
        degrade_level = int(budget_events[-1].get("degrade_level", 0) or 0)

    report: Dict[str, Any] = {
        "total_tokens": token_used,
        "token_budget": token_budget,
        "usage_ratio": round(token_used / token_budget, 4) if token_budget else 0.0,
        "degrade_level": degrade_level,
        "steps": int(_get(state, "step_count", len(results)) or 0),
        "iterations": int(_get(state, "iteration", 0) or 0),
        "by_role": by_role,
        "by_role_ratio": by_role_ratio,
        "top_role": top_role,
        "by_tool": by_tool,
        "tool_tokens_total": tool_sum,
        "by_iteration": by_iteration,
        "attribution": {
            "role_sum": role_sum,
            "delta": delta,
            "delta_ratio": delta_ratio,
            "consistent": consistent,
        },
    }
    report["headline"] = _headline(report)
    return report


def _headline(report: Dict[str, Any]) -> str:
    """一句话结论，便于直接贴进日志/看板。"""
    total = report["total_tokens"]
    if not total:
        return "本次任务未消耗 token"
    top_role = report.get("top_role")
    top_ratio = report.get("by_role_ratio", {}).get(top_role, 0.0) if top_role else 0.0
    parts = [f"总 {total} tokens（预算占用 {report['usage_ratio'] * 100:.1f}%）"]
    if top_role and top_role in report.get("by_role", {}):
        parts.append(f"最贵角色 {top_role} 占 {top_ratio * 100:.1f}%")
    if report.get("by_tool"):
        tool = max(report["by_tool"], key=lambda k: report["by_tool"][k]["tokens"])
        parts.append(f"最贵工具 {tool}（回填 {report['by_tool'][tool]['tokens']} tokens）")
    if report.get("degrade_level"):
        parts.append(f"已触发 {report['degrade_level']} 级降级")
    return "；".join(parts)


def render_report(state_or_report: Any) -> str:
    """把归因结果渲染成人类可读的多行文本（供 CLI / 日志使用）。"""
    report = (
        state_or_report
        if isinstance(state_or_report, dict) and "by_role" in state_or_report
        else attribute_cost(state_or_report)
    )

    lines = ["=== 成本归因报告 ===", report["headline"], ""]
    lines.append(f"总消耗 : {report['total_tokens']} tokens / 预算 {report['token_budget']}"
                 f"（{report['usage_ratio'] * 100:.1f}%）")
    lines.append(f"步数   : {report['steps']}    轮次: {report['iterations']}"
                 f"    降级级别: {report['degrade_level']}")
    lines.append("")
    lines.append("[按角色]")
    for role, tokens in report["by_role"].items():
        pct = report["by_role_ratio"].get(role, 0.0) * 100
        lines.append(f"  {role:<12} {tokens:>8} tokens  ({pct:5.1f}%)")
    if report["by_tool"]:
        lines.append("")
        lines.append("[按工具 · 结果回填 token]")
        for tool, info in sorted(report["by_tool"].items(), key=lambda kv: -kv[1]["tokens"]):
            lines.append(f"  {tool:<12} {info['tokens']:>8} tokens  ({info['calls']} 次调用)")
    if report["by_iteration"]:
        lines.append("")
        lines.append("[按轮次]")
        for it, info in sorted(report["by_iteration"].items(), key=lambda kv: kv[0]):
            detail = "  ".join(f"{r}={t}" for r, t in info["by_role"].items())
            lines.append(f"  轮次 {it:<3} {info['tokens']:>8} tokens   {detail}")

    att = report["attribution"]
    lines.append("")
    lines.append(
        f"[一致性] 角色之和 {att['role_sum']} vs 总消耗 {report['total_tokens']}"
        f"  delta={att['delta']} ({att['delta_ratio'] * 100:.2f}%)  "
        f"{'OK' if att['consistent'] else '不一致，需检查埋点'}"
    )
    return "\n".join(lines)
