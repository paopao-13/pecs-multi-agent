"""
Graph Trace Logger —— 多智能体执行链路日志工具

功能：
1. 实时打印每轮节点名称、角色输出摘要、token 消耗、反思内容
2. 提供单任务全链路导出函数 export_trace_to_markdown(state, output_path)，
   将完整 Plan-Execute-Reflect 流程保存为 markdown 日志至 results/traces/

日志格式包含：任务问题、每步的角色/输入/输出/token/时间、最终答案、总token消耗
"""
import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional


# 结果目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULTS_DIR = os.path.join(_PROJECT_ROOT, "results")
_TRACES_DIR = os.path.join(_RESULTS_DIR, "traces")


# 角色中文名映射
_ROLE_LABELS = {
    "planner": "Planner（规划者）",
    "executor": "Executor（执行者）",
    "critic": "Critic（评审者）",
    "synthesizer": "Synthesizer（综合者）",
    "executor_retry": "Executor-Retry（重试执行）",
}

# 节点到角色的映射
_NODE_ROLES = {
    "planner": "planner",
    "executor": "executor",
    "executor_retry": "executor",
    "critic": "critic",
    "synthesizer": "synthesizer",
}


def _truncate(text: Any, max_len: int = 120) -> str:
    """截断文本到指定长度，末尾加省略号"""
    s = str(text) if text is not None else ""
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


def _format_tokens(n: int) -> str:
    """格式化 token 数量显示"""
    return f"{n:,}"


class GraphTraceLogger:
    """多智能体执行链路日志记录器"""

    def __init__(self, verbose: bool = True):
        """
        初始化日志记录器

        参数:
            verbose: 是否实时打印节点信息到控制台
        """
        self.verbose = verbose
        self._node_count = 0

    def log_node(self, node_name: str, state: dict):
        """
        实时打印节点信息

        在每个节点执行完毕后调用，打印：
        - 节点序号与名称
        - 角色输出摘要（plan / results / critic_scores / final_answer）
        - token 消耗（总量 + 各角色明细）
        - 反思内容（如有）

        参数:
            node_name: 节点名称（planner / executor / critic / synthesizer 等）
            state: 当前 AgentState 字典
        """
        self._node_count += 1
        role = _NODE_ROLES.get(node_name, node_name)
        role_label = _ROLE_LABELS.get(role, node_name)

        token_used = state.get("token_used", 0)
        token_budget = state.get("token_budget", 0)
        role_tokens = state.get("role_token_used", {})
        iteration = state.get("iteration", 0)
        reflection = state.get("reflection", "")

        if not self.verbose:
            return

        # 分隔线
        print(f"\n{'='*60}")
        print(f"[Step {self._node_count}] 节点: {node_name} | {role_label} | 迭代轮次: {iteration}")
        print(f"{'-'*60}")

        # 角色输出摘要
        summary = self._get_node_summary(node_name, state)
        if summary:
            print(f"输出摘要: {summary}")

        # token 消耗
        ratio = (token_used / token_budget * 100) if token_budget > 0 else 0
        print(f"Token 消耗: {_format_tokens(token_used)} / {_format_tokens(token_budget)} ({ratio:.1f}%)")
        for r, t in role_tokens.items():
            if t > 0:
                label = _ROLE_LABELS.get(r, r)
                print(f"  - {label}: {_format_tokens(t)}")

        # 反思内容
        if reflection:
            print(f"反思内容: {_truncate(reflection, 200)}")

        print(f"{'='*60}")

    def _get_node_summary(self, node_name: str, state: dict) -> str:
        """根据节点类型提取输出摘要"""
        if node_name == "planner":
            plan = state.get("plan", [])
            complexity = state.get("complexity", "unknown")
            return f"复杂度={complexity}, 规划了 {len(plan)} 个步骤"

        elif node_name in ("executor", "executor_retry"):
            results = state.get("results", [])
            plan = state.get("plan", [])
            current_idx = state.get("current_step_idx", 0)
            if results:
                latest = results[-1]
                success = latest.get("success", False)
                step_id = latest.get("step_id", "?")
                result_preview = _truncate(latest.get("result", ""), 80)
                return f"步骤{step_id}({'完成' if success else '失败'}): {result_preview}"
            return f"当前步骤索引: {current_idx}, 已执行结果数: {len(results)}"

        elif node_name == "critic":
            scores = state.get("critic_scores", [])
            if scores:
                latest = scores[-1]
                overall = latest.get("overall", 0)
                feedback = _truncate(latest.get("feedback", ""), 80)
                return f"综合评分={overall:.1f}, 反馈: {feedback}"
            return "无评分"

        elif node_name == "synthesizer":
            final_answer = state.get("final_answer", "")
            answer_format = state.get("answer_format", "text")
            return f"格式={answer_format}, 答案预览: {_truncate(final_answer, 80)}"

        return ""

    def export_trace_to_markdown(self, state: dict, output_path: str = None) -> str:
        """
        导出完整链路日志为 Markdown 文件

        将完整的 Plan-Execute-Reflect 流程保存为 markdown 日志，
        包含：任务问题、每步的角色/输入/输出/token/时间、最终答案、总token消耗

        参数:
            state: 最终的 AgentState 字典
            output_path: 输出文件路径。如果为 None，自动保存到 results/traces/

        返回:
            实际保存的文件路径
        """
        if output_path is None:
            os.makedirs(_TRACES_DIR, exist_ok=True)
            query = state.get("query", "task")
            # 用 query 前几个字符 + 时间戳生成文件名
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in query[:20])
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(_TRACES_DIR, f"trace_{safe_name}_{timestamp}.md")

        # 确保目录存在
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        md = self._build_markdown(state)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md)

        if self.verbose:
            print(f"\n[Trace Logger] 全链路日志已导出: {output_path}")

        return output_path

    def _build_markdown(self, state: dict) -> str:
        """构建完整的 Markdown 日志内容"""
        lines: List[str] = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        query = state.get("query", "")
        complexity = state.get("complexity", "unknown")
        iteration = state.get("iteration", 0)
        final_answer = state.get("final_answer", "")
        answer_format = state.get("answer_format", "text")
        token_used = state.get("token_used", 0)
        token_budget = state.get("token_budget", 0)
        role_tokens = state.get("role_token_used", {}) or {}
        budget_events = state.get("budget_events", []) or []
        scheduler_decisions = state.get("scheduler_decisions", []) or []
        plan = state.get("plan", []) or []
        results = state.get("results", []) or []
        critic_scores = state.get("critic_scores", []) or []
        logs = state.get("logs", []) or []
        reflection = state.get("reflection", "")
        budget_degraded = state.get("budget_degraded", False)
        use_heuristics = state.get("use_heuristics", True)

        # ===== 标题与元信息 =====
        lines.append(f"# PECS 多智能体执行链路日志")
        lines.append("")
        lines.append(f"| 项目 | 内容 |")
        lines.append(f"|------|------|")
        lines.append(f"| 导出时间 | {now} |")
        lines.append(f"| 任务问题 | {_truncate(query, 200)} |")
        lines.append(f"| 任务复杂度 | {complexity} |")
        lines.append(f"| 反思迭代轮次 | {iteration} |")
        lines.append(f"| 启发式模式 | {'是' if use_heuristics else '否'} |")
        lines.append(f"| 预算降级 | {'是' if budget_degraded else '否'} |")
        lines.append(f"| Token 预算 | {_format_tokens(token_budget)} |")
        lines.append(f"| Token 实际消耗 | {_format_tokens(token_used)} |")
        usage_pct = (token_used / token_budget * 100) if token_budget > 0 else 0
        lines.append(f"| 预算使用率 | {usage_pct:.1f}% |")
        lines.append("")

        # ===== 1. 任务问题 =====
        lines.append("## 1. 任务问题")
        lines.append("")
        lines.append(f"> {query}")
        lines.append("")

        # ===== 2. 执行计划 =====
        lines.append("## 2. 执行计划（Planner 输出）")
        lines.append("")
        if plan:
            lines.append(f"| 步骤ID | 动作 | 描述 | 风险 | 状态 | 重试次数 | 依赖 |")
            lines.append(f"|--------|------|------|------|------|----------|------|")
            for step in plan:
                step_id = step.get("id", "")
                action = step.get("action", "")
                desc = _truncate(step.get("description", ""), 80)
                risk = step.get("risk", "")
                status = step.get("status", "")
                retry = step.get("retry_count", 0)
                deps = step.get("depends_on", [])
                deps_str = ", ".join(str(d) for d in deps) if deps else "-"
                lines.append(f"| {step_id} | {action} | {desc} | {risk} | {status} | {retry} | {deps_str} |")
            lines.append("")
        else:
            lines.append("*Planner 未生成执行计划*")
            lines.append("")

        # ===== 3. 执行结果（Executor 输出）=====
        lines.append("## 3. 执行结果（Executor 输出）")
        lines.append("")
        if results:
            for i, result in enumerate(results):
                step_id = result.get("step_id", i + 1)
                action = result.get("action", "")
                success = result.get("success", False)
                result_text = result.get("result", "")
                lines.append(f"### 步骤 {step_id}")
                lines.append("")
                lines.append(f"- **动作**: {action}")
                lines.append(f"- **执行状态**: {'成功' if success else '失败'}")
                lines.append(f"- **执行结果**:")
                lines.append("```")
                lines.append(_truncate(result_text, 2000) if result_text else "(无结果)")
                lines.append("```")
                lines.append("")
        else:
            lines.append("*Executor 未产生执行结果*")
            lines.append("")

        # ===== 4. 评审评分（Critic 输出）=====
        lines.append("## 4. 评审评分（Critic 输出）")
        lines.append("")
        if critic_scores:
            lines.append(f"| 步骤ID | 准确性 | 一致性 | 完整性 | 综合评分 | 反馈 |")
            lines.append(f"|--------|--------|--------|--------|----------|------|")
            for score in critic_scores:
                step_id = score.get("step_id", "")
                acc = score.get("accuracy", 0)
                cons = score.get("consistency", 0)
                comp = score.get("completeness", 0)
                overall = score.get("overall", 0)
                feedback = _truncate(score.get("feedback", ""), 100)
                lines.append(f"| {step_id} | {acc} | {cons} | {comp} | {overall:.1f} | {feedback} |")
            lines.append("")
        else:
            lines.append("*Critic 未产生评审评分*")
            lines.append("")

        # ===== 5. Token 消耗明细 =====
        lines.append("## 5. Token 消耗明细")
        lines.append("")
        lines.append(f"### 5.1 各角色消耗")
        lines.append("")
        lines.append(f"| 角色 | Token 消耗 | 占比 |")
        lines.append(f"|------|-----------|------|")
        for role, tokens in role_tokens.items():
            label = _ROLE_LABELS.get(role, role)
            pct = (tokens / token_used * 100) if token_used > 0 else 0
            lines.append(f"| {label} | {_format_tokens(tokens)} | {pct:.1f}% |")
        lines.append(f"| **总计** | **{_format_tokens(token_used)}** | **100%** |")
        lines.append("")

        # 5.2 Token 消耗事件时间线
        lines.append(f"### 5.2 Token 消耗事件时间线")
        lines.append("")
        if budget_events:
            lines.append(f"| 序号 | 角色 | 消耗Token | 累计Token | 使用率 | 降级级别 |")
            lines.append(f"|------|------|----------|----------|--------|----------|")
            for i, event in enumerate(budget_events):
                role = event.get("role", "")
                label = _ROLE_LABELS.get(role, role)
                tokens = event.get("tokens", 0)
                cumulative = event.get("token_used", 0)
                ratio = event.get("usage_ratio", 0)
                degrade = event.get("degrade_level", 0)
                lines.append(f"| {i+1} | {label} | {_format_tokens(tokens)} | {_format_tokens(cumulative)} | {ratio:.1%} | L{degrade} |")
            lines.append("")
        else:
            lines.append("*无 Token 消耗事件记录*")
            lines.append("")

        # 5.3 预算感知调度决策
        lines.append(f"### 5.3 预算感知调度决策")
        lines.append("")
        if scheduler_decisions:
            lines.append(f"| 序号 | 决策者 | 决策 | 原因 | 累计Token | 降级级别 |")
            lines.append(f"|------|--------|------|------|----------|----------|")
            for i, dec in enumerate(scheduler_decisions):
                actor = dec.get("actor", "")
                decision = _truncate(dec.get("decision", ""), 60)
                reason = _truncate(dec.get("reason", ""), 80)
                cumulative = dec.get("token_used", 0)
                degrade = dec.get("degrade_level", 0)
                lines.append(f"| {i+1} | {actor} | {decision} | {reason} | {_format_tokens(cumulative)} | L{degrade} |")
            lines.append("")
        else:
            lines.append("*无调度决策记录*")
            lines.append("")

        # ===== 6. 反思内容 =====
        lines.append("## 6. 反思内容（Reflection）")
        lines.append("")
        if reflection:
            lines.append(f"> {reflection}")
            lines.append("")
        else:
            lines.append("*未触发反思循环*")
            lines.append("")

        # ===== 7. 执行日志 =====
        lines.append("## 7. 执行日志")
        lines.append("")
        if logs:
            lines.append("```")
            for log_entry in logs:
                lines.append(str(log_entry))
            lines.append("```")
            lines.append("")
        else:
            lines.append("*无执行日志*")
            lines.append("")

        # ===== 8. 最终答案 =====
        lines.append("## 8. 最终答案（Synthesizer 输出）")
        lines.append("")
        lines.append(f"- **输出格式**: {answer_format}")
        lines.append(f"- **最终答案**:")
        lines.append("")
        lines.append("```")
        lines.append(final_answer if final_answer else "(无最终答案)")
        lines.append("```")
        lines.append("")

        # ===== 9. 总结 =====
        lines.append("## 9. 总结")
        lines.append("")
        lines.append(f"- 执行步骤数: {len(results)}")
        lines.append(f"- 评审评分数: {len(critic_scores)}")
        lines.append(f"- 反思迭代轮次: {iteration}")
        lines.append(f"- Token 预算: {_format_tokens(token_budget)}")
        lines.append(f"- Token 实际消耗: {_format_tokens(token_used)} ({usage_pct:.1f}%)")
        lines.append(f"- 预算是否降级: {'是' if budget_degraded else '否'}")
        lines.append(f"- 执行日志条数: {len(logs)}")
        lines.append(f"- 调度决策数: {len(scheduler_decisions)}")
        lines.append("")

        lines.append("---")
        lines.append(f"*日志由 GraphTraceLogger 于 {now} 自动生成*")

        return "\n".join(lines)


def export_task_trace(state: dict, task_id: str = None) -> str:
    """
    便捷函数：导出单任务全链路日志到 results/traces/

    参数:
        state: 最终的 AgentState 字典
        task_id: 任务ID，用于生成文件名。如果为 None，则从 query 推断。

    返回:
        保存的 markdown 文件路径
    """
    logger = GraphTraceLogger(verbose=False)

    if task_id:
        os.makedirs(_TRACES_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(task_id))
        output_path = os.path.join(_TRACES_DIR, f"trace_{safe_id}_{timestamp}.md")
    else:
        output_path = None  # 让 export_trace_to_markdown 自动生成

    return logger.export_trace_to_markdown(state, output_path)
