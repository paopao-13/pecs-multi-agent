"""
消融实验评测脚本

读取 ablation_configs/ 下的 YAML 配置，对每组配置运行 GAIA 样例集评测，
记录准确率、Token 消耗、平均执行时间，输出对比结果到 results/ablation_report.json。

用法:
    # 运行全部6组消融配置
    python -m benchmarks.ablation_eval

    # 运行单个配置
    python -m benchmarks.ablation_eval --config full_pecs

    # 指定样本数量（默认全部）
    python -m benchmarks.ablation_eval --num-samples 10

    # 组合使用
    python -m benchmarks.ablation_eval --config no_critic --num-samples 5

可用配置:
    full_pecs              完整 PECS 四角色架构（对照组）
    no_critic              移除 Critic，Executor 直连 Synthesizer
    no_synthesizer         移除 Synthesizer，Executor 直接输出结果
    single_agent           退化为纯 ReAct 单智能体（仅 Executor）
    critic_no_reflect      保留 Critic 但关闭反思闭环
    synthesizer_no_replan  保留 Synthesizer 但关闭重规划，仅做结果拼接
"""
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---------- YAML 加载（优先用 PyYAML，不可用时回退内置解析器） ----------
try:
    import yaml  # type: ignore

    def _load_yaml(text: str) -> dict:
        return yaml.safe_load(text) or {}
except ImportError:
    def _load_yaml(text: str) -> dict:
        """内置简易 YAML 解析器，支持本项目消融配置的嵌套字典结构。"""
        root: Dict[str, Any] = {}
        # 栈元素: (缩进层级, 所属字典引用)
        stack = [(-1, root)]

        for raw_line in text.splitlines():
            line = raw_line.split("#")[0]  # 去除注释
            stripped = line.strip()
            if not stripped:
                continue

            indent = len(line) - len(line.lstrip())

            # 弹栈直到找到缩进更小的父级
            while len(stack) > 1 and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1][1]

            key, sep, value = stripped.partition(":")
            if not sep:
                continue
            key = key.strip()
            value = value.strip()

            if value == "":
                # 嵌套字典
                new_dict: Dict[str, Any] = {}
                parent[key] = new_dict
                stack.append((indent, new_dict))
            else:
                # 叶子值
                low = value.lower()
                if low in ("true", "yes"):
                    parent[key] = True
                elif low in ("false", "no"):
                    parent[key] = False
                elif len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                    parent[key] = value[1:-1]
                else:
                    try:
                        parent[key] = int(value)
                    except ValueError:
                        try:
                            parent[key] = float(value)
                        except ValueError:
                            parent[key] = value
        return root


# ---------- 导入项目模块 ----------
from langgraph.graph import StateGraph, END  # noqa: E402

from graph.state import AgentState  # noqa: E402
from graph.builder import (  # noqa: E402
    build_graph,
    create_initial_state,
    route_after_executor,
    route_after_critic,
    route_after_synthesizer,
)
from graph.token_budget import get_budget_policy  # noqa: E402
from agents.planner import planner_node  # noqa: E402
from agents.executor import executor_node, executor_retry_node  # noqa: E402
from agents.critic import critic_node  # noqa: E402
from agents.synthesizer import synthesizer_node  # noqa: E402
from benchmarks.gaia_eval import GAIA_L1_SAMPLES, evaluate_answer, save_results  # noqa: E402
from benchmarks.react_baseline import run_react_task  # noqa: E402
from config import DEFAULT_TOKEN_BUDGET, MAX_ITERATIONS  # noqa: E402


# ====================================================================
# 自定义节点 & 路由函数（用于消融变体图）
# ====================================================================

def direct_output_node(state: dict) -> dict:
    """
    无 Synthesizer 时的替代节点：直接从执行结果中提取最终答案。

    策略：
    - Python 结果 → 提取最后一行输出
    - 搜索结果 → 提取第一条实质性内容
    - 其他 → 返回原始结果前 300 字符
    """
    results = state.get("results", [])
    logs = state.get("logs", [])

    logs.append("[DirectOutput] 无 Synthesizer，直接从执行结果提取答案")

    if not results:
        return {"final_answer": "无执行结果", "logs": logs}

    last_result = results[-1]
    raw_result = str(last_result.get("result", ""))
    action = last_result.get("action", "")

    if action == "python":
        lines = raw_result.strip().split("\n")
        output_lines = [
            l for l in lines
            if not l.strip().startswith("输出:") and l.strip()
        ]
        final_answer = output_lines[-1] if output_lines else raw_result.strip()
    else:
        lines = raw_result.split("\n")
        candidates = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("来源:") or line.startswith("---"):
                continue
            cleaned = (
                line.replace("[搜索] ", "")
                    .replace("[DuckDuckGo] ", "")
                    .replace("[模拟搜索] ", "")
            )
            if len(cleaned) > 15:
                candidates.append(cleaned)
        final_answer = candidates[0] if candidates else raw_result[:300]

    logs.append(f"[DirectOutput] 最终答案: {final_answer[:80]}...")

    return {
        "final_answer": final_answer,
        "reflection": "",  # 无反思
        "logs": logs,
    }


def _route_executor_no_critic(state: dict) -> str:
    """
    无 Critic 时 Executor 的路由：
    - 所有步骤完成 → Synthesizer
    - 还有步骤 → 继续执行下一步
    - 预算耗尽 → Synthesizer
    """
    plan = state.get("plan", [])
    current_idx = state.get("current_step_idx", 0)

    policy = get_budget_policy(state)
    if policy["force_synthesize"]:
        return "synthesizer"

    if current_idx >= len(plan):
        return "synthesizer"

    return "executor"


def _route_executor_no_synth(state: dict) -> str:
    """
    无 Synthesizer 时 Executor 的路由：
    - 所有步骤完成 → direct_output
    - 还有步骤 → Critic
    - 预算耗尽 → direct_output
    """
    plan = state.get("plan", [])
    current_idx = state.get("current_step_idx", 0)

    policy = get_budget_policy(state)
    if policy["force_synthesize"]:
        return "direct_output"

    if current_idx >= len(plan):
        return "direct_output"

    return "critic"


def _route_critic_no_synth(state: dict) -> str:
    """
    无 Synthesizer 时 Critic 的路由：
    - 质量达标 + 有剩余步骤 → Executor（下一步）
    - 质量达标 + 无剩余步骤 → direct_output
    - 质量不达标 + 可重试 → executor_retry
    - 质量不达标 + 重试上限 → direct_output 或 executor
    - 预算耗尽 → direct_output
    """
    critic_scores = state.get("critic_scores", [])
    results = state.get("results", [])
    plan = state.get("plan", [])
    current_idx = state.get("current_step_idx", 0)
    retry_feedback = state.get("retry_feedback", "")

    policy = get_budget_policy(state)
    if policy["force_synthesize"]:
        return "direct_output"

    if not critic_scores:
        return "direct_output"

    latest_score = critic_scores[-1]
    overall = latest_score.get("overall", 0)

    if overall >= 4.0:
        if current_idx < len(plan):
            return "executor"
        return "direct_output"

    # 质量不达标 — 检查重试次数
    if results:
        latest_result = results[-1]
        step_id = latest_result.get("step_id", len(results))
        step = None
        for s in plan:
            if s.get("id") == step_id:
                step = s
                break
        if step and step.get("retry_count", 0) >= 3:
            return "executor" if current_idx < len(plan) else "direct_output"

    if retry_feedback:
        return "executor_retry"

    return "executor" if current_idx < len(plan) else "direct_output"


def _route_synthesizer_no_reflect(state: dict) -> str:
    """Synthesizer 不触发反思：始终结束。"""
    return END


def synthesizer_no_replan_node(state: dict) -> dict:
    """
    Synthesizer 无重规划节点：仅做结果拼接，不触发反思/重规划。

    与原生 synthesizer_node 的区别：
    - 不调用 LLM 综合分析，直接拼接执行结果
    - reflection 始终为空字符串（不触发反思循环）
    - iteration 不增加（不进入重规划）

    用于 synthesizer_no_replan 消融实验，验证重规划机制的价值。
    """
    query = state.get("query", "")
    results = state.get("results", [])
    logs = state.get("logs", [])
    iteration = state.get("iteration", 0)

    logs.append(
        f"[Synthesizer-NoReplan] 开始拼接 {len(results)} 个步骤的结果（重规划已关闭）"
    )

    if not results:
        return {
            "final_answer": "无法生成答案：没有执行任何步骤。",
            "reflection": "",
            "iteration": iteration,
            "logs": logs,
        }

    # 仅做结果拼接，不调用 LLM
    parts = [f"根据已有信息回答问题「{query}」:"]
    for r in results:
        desc = r.get("description", "")
        result_text = r.get("result", "")
        parts.append(f"- {desc}: {result_text}")

    final_answer = "\n".join(parts)
    logs.append("[Synthesizer-NoReplan] 结果拼接完成（未触发重规划）")

    return {
        "final_answer": final_answer,
        "reflection": "",  # 始终为空，不触发反思
        "iteration": iteration,  # 不增加 iteration
        "logs": logs,
    }


# ====================================================================
# 图构建器（根据消融配置动态构建）
# ====================================================================

def build_no_critic_graph(token_budget: int = DEFAULT_TOKEN_BUDGET):
    """
    构建「无 Critic」消融图：

        Planner → Executor → (有剩余步骤? Executor : Synthesizer)
        Synthesizer → (需反思? Planner : END)
    """
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge("planner", "executor")
    graph.add_conditional_edges("executor", _route_executor_no_critic)
    graph.add_conditional_edges("synthesizer", route_after_synthesizer)

    graph.set_entry_point("planner")
    return graph.compile()


def build_no_synthesizer_graph(token_budget: int = DEFAULT_TOKEN_BUDGET):
    """
    构建「无 Synthesizer」消融图：

        Planner → Executor → (有剩余步骤? Critic : direct_output)
        Critic → (达标+有步骤? Executor : 达标+无步骤? direct_output
                  : 可重试? executor_retry : direct_output)
        executor_retry → Executor
        direct_output → END
    """
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("executor_retry", executor_retry_node)
    graph.add_node("critic", critic_node)
    graph.add_node("direct_output", direct_output_node)

    graph.add_edge("planner", "executor")
    graph.add_edge("executor_retry", "executor")
    graph.add_edge("direct_output", END)

    graph.add_conditional_edges("executor", _route_executor_no_synth)
    graph.add_conditional_edges("critic", _route_critic_no_synth)

    graph.set_entry_point("planner")
    return graph.compile()


def build_no_reflect_graph(token_budget: int = DEFAULT_TOKEN_BUDGET):
    """
    构建「有 Synthesizer 但禁用反思」消融图：
    Executor/Critic 路由保持不变，但 Synthesizer 始终直接结束，不回到 Planner。

        Planner → Executor → Critic → Synthesizer → END（不反思）
    """
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("executor_retry", executor_retry_node)
    graph.add_node("critic", critic_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge("planner", "executor")
    graph.add_edge("executor_retry", "executor")

    # Executor 和 Critic 使用原始路由（保留完整质量评审流程）
    graph.add_conditional_edges("executor", route_after_executor)
    graph.add_conditional_edges("critic", route_after_critic)
    # Synthesizer 不触发反思，始终结束
    graph.add_conditional_edges("synthesizer", _route_synthesizer_no_reflect)

    graph.set_entry_point("planner")
    return graph.compile()


def build_synthesizer_no_replan_graph(token_budget: int = DEFAULT_TOKEN_BUDGET):
    """
    构建「保留 Synthesizer 但关闭重规划」消融图：

    四个角色全部保留，Critic 评审和重试正常运作，
    但 Synthesizer 使用 synthesizer_no_replan_node（仅做结果拼接），
    不触发反思、不回传 Planner 重规划。

        Planner → Executor → Critic → Synthesizer(no_replan) → END
    """
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("executor_retry", executor_retry_node)
    graph.add_node("critic", critic_node)
    graph.add_node("synthesizer", synthesizer_no_replan_node)

    graph.add_edge("planner", "executor")
    graph.add_edge("executor_retry", "executor")

    # Executor 和 Critic 使用原始路由（保留完整质量评审流程）
    graph.add_conditional_edges("executor", route_after_executor)
    graph.add_conditional_edges("critic", route_after_critic)
    # Synthesizer 不触发重规划，始终结束
    graph.add_conditional_edges("synthesizer", _route_synthesizer_no_reflect)

    graph.set_entry_point("planner")
    return graph.compile()


def build_graph_for_config(config: dict):
    """
    根据消融配置选择对应的图构建策略。

    返回:
        - 编译后的 LangGraph 实例，或
        - 字符串 "react" 表示使用 ReAct 单智能体基线
    """
    arch = config.get("architecture", {})
    planner_on = arch.get("planner", True)
    executor_on = arch.get("executor", True)
    critic_on = arch.get("critic", True)
    synthesizer_on = arch.get("synthesizer", True)

    token_budget_cfg = config.get("token_budget", {})
    budget = token_budget_cfg.get("total", DEFAULT_TOKEN_BUDGET) if token_budget_cfg.get("enabled", True) else DEFAULT_TOKEN_BUDGET

    routing = config.get("routing", {})
    ablation_mode = config.get("ablation_mode", None)

    # 单智能体：仅 Executor → ReAct
    if not planner_on and executor_on and not critic_on and not synthesizer_on:
        return "react"

    # ---- function_disable 消融模式：四角色全保留但关闭特定功能 ----
    if ablation_mode == "function_disable":
        # synthesizer_no_replan: 保留 Synthesizer 但关闭重规划（仅做结果拼接）
        if not config.get("synthesizer_replan", True) and routing.get("synthesizer_reflect", True):
            return build_synthesizer_no_replan_graph(budget)
        # critic_no_reflect: 保留 Critic 但关闭反思闭环
        if not routing.get("synthesizer_reflect", True):
            return build_no_reflect_graph(budget)

    # 完整 PECS
    if planner_on and executor_on and critic_on and synthesizer_on:
        return build_graph(budget)

    # 无 Critic
    if planner_on and executor_on and not critic_on and synthesizer_on:
        return build_no_critic_graph(budget)

    # 无 Synthesizer
    if planner_on and executor_on and critic_on and not synthesizer_on:
        return build_no_synthesizer_graph(budget)

    # 有 Synthesizer 但禁用反思（兜底：无 ablation_mode 的旧配置）
    if (
        planner_on
        and executor_on
        and (critic_on or not routing.get("executor_to_critic", True))
        and synthesizer_on
        and not routing.get("synthesizer_reflect", True)
    ):
        return build_no_reflect_graph(budget)

    # 兜底：完整图
    return build_graph(budget)


# ====================================================================
# 配置加载
# ====================================================================

CONFIGS_DIR = os.path.join(PROJECT_ROOT, "ablation_configs")

# 全部消融配置名称（按运行顺序）
ALL_CONFIG_NAMES = [
    "full_pecs",
    "no_critic",
    "no_synthesizer",
    "single_agent",
    "critic_no_reflect",
    "synthesizer_no_replan",
]


def load_config(config_name: str) -> dict:
    """
    从 ablation_configs/ 目录加载指定名称的 YAML 配置。

    参数:
        config_name: 配置名（不含 .yaml 后缀），如 "full_pecs"
    """
    config_path = os.path.join(CONFIGS_DIR, f"{config_name}.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"消融配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = _load_yaml(f.read())

    return config


# ====================================================================
# 单配置评测
# ====================================================================

def run_ablation_config(
    config_name: str,
    num_samples: Optional[int] = None,
) -> dict:
    """
    运行单组消融配置的 GAIA 评测。

    参数:
        config_name: 配置名称
        num_samples: 评测样本数（None=全部）

    返回:
        评测结果字典（准确率、Token消耗、执行时间、详细结果）
    """
    config = load_config(config_name)
    description = config.get("description", config_name)
    arch = config.get("architecture", {})

    samples = GAIA_L1_SAMPLES[:num_samples] if num_samples else GAIA_L1_SAMPLES

    print(f"\n{'=' * 70}")
    print(f"  消融配置: {config_name}")
    print(f"  描述: {description}")
    print(f"  样本数: {len(samples)}")
    print(f"  架构: {arch}")
    print(f"{'=' * 70}")

    # 构建图或选择 ReAct
    graph_or_react = build_graph_for_config(config)
    is_react = graph_or_react == "react"

    # 确定 Token 预算
    token_budget_cfg = config.get("token_budget", {})
    if token_budget_cfg.get("enabled", True):
        token_budget = token_budget_cfg.get("total", DEFAULT_TOKEN_BUDGET)
    else:
        # 预算禁用时给一个很大的值，避免触发降级
        token_budget = 10 ** 9

    results = []
    correct_count = 0
    total_tokens = 0
    total_time = 0.0

    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        ground_truth = sample["answer"]

        print(f"\n  [{i + 1}/{len(samples)}] 任务 {task_id}: {question[:60]}...")

        start_time = time.time()

        try:
            if is_react:
                # ReAct 单智能体
                state = run_react_task(question, token_budget=token_budget)
            else:
                # 多智能体图
                initial_state = create_initial_state(question, token_budget)
                state = graph_or_react.invoke(initial_state)
        except Exception as e:
            print(f"    [错误] 执行失败: {type(e).__name__}: {e}")
            state = {
                "final_answer": f"执行错误: {e}",
                "token_used": 0,
                "logs": [f"[ERROR] {type(e).__name__}: {e}"],
            }

        elapsed = time.time() - start_time
        total_time += elapsed

        predicted = state.get("final_answer", "")
        tokens_used = state.get("token_used", 0)
        total_tokens += tokens_used

        is_correct = evaluate_answer(predicted, ground_truth)
        if is_correct:
            correct_count += 1

        results.append({
            "task_id": task_id,
            "question": question,
            "ground_truth": ground_truth,
            "predicted": predicted,
            "correct": is_correct,
            "tokens_used": tokens_used,
            "execution_time": round(elapsed, 2),
        })

        status = "正确" if is_correct else "错误"
        print(f"    -> 预测: {predicted[:80]}... | {status} | tokens: {tokens_used} | {elapsed:.1f}s")

    accuracy = correct_count / len(samples) if samples else 0
    avg_tokens = total_tokens / len(samples) if samples else 0
    avg_time = total_time / len(samples) if samples else 0

    result = {
        "config_name": config_name,
        "description": description,
        "architecture": arch,
        "routing": config.get("routing", {}),
        "token_budget": config.get("token_budget", {}),
        "total_samples": len(samples),
        "correct_count": correct_count,
        "accuracy": round(accuracy, 4),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(avg_tokens),
        "avg_execution_time": round(avg_time, 2),
        "details": results,
    }

    print(f"\n  --- {config_name} 汇总 ---")
    print(f"  准确率: {correct_count}/{len(samples)} = {accuracy:.2%}")
    print(f"  平均Token: {avg_tokens:.0f}")
    print(f"  平均时间: {avg_time:.2f}s")

    return result


# ====================================================================
# 全部配置评测 + 对比报告
# ====================================================================

def run_all_ablations(num_samples: Optional[int] = None) -> dict:
    """
    运行全部消融配置并生成对比报告。

    参数:
        num_samples: 每组配置的评测样本数（None=全部）

    返回:
        包含所有配置结果和对比摘要的字典
    """
    all_results = []

    for config_name in ALL_CONFIG_NAMES:
        result = run_ablation_config(config_name, num_samples)
        all_results.append(result)

    # 生成对比摘要
    comparison = {}
    for r in all_results:
        comparison[r["config_name"]] = {
            "accuracy": r["accuracy"],
            "correct_count": r["correct_count"],
            "total_samples": r["total_samples"],
            "avg_tokens_per_task": r["avg_tokens_per_task"],
            "avg_execution_time": r["avg_execution_time"],
            "total_tokens": r["total_tokens"],
        }

    # 计算相对对照组的差异
    baseline = all_results[0] if all_results else None  # full_pecs 作为对照组
    deltas = {}
    if baseline:
        base_acc = baseline["accuracy"]
        base_tokens = baseline["avg_tokens_per_task"]
        base_time = baseline["avg_execution_time"]

        for r in all_results[1:]:
            name = r["config_name"]
            acc_delta = round(r["accuracy"] - base_acc, 4)
            token_delta_pct = (
                round((r["avg_tokens_per_task"] - base_tokens) / base_tokens * 100, 1)
                if base_tokens > 0 else 0
            )
            time_delta_pct = (
                round((r["avg_execution_time"] - base_time) / base_time * 100, 1)
                if base_time > 0 else 0
            )
            deltas[name] = {
                "accuracy_delta": acc_delta,
                "token_delta_pct": token_delta_pct,
                "time_delta_pct": time_delta_pct,
            }

    report = {
        "experiment_type": "ablation_study",
        "experiment_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_configs": len(all_results),
        "baseline_config": baseline["config_name"] if baseline else None,
        "configs": all_results,
        "comparison_summary": comparison,
        "deltas_vs_baseline": deltas,
    }

    # 保存到 results/ablation_report.json
    save_results(report, "ablation_report.json")

    # 打印对比表
    print(f"\n{'=' * 70}")
    print("  消融实验对比报告")
    print(f"{'=' * 70}")
    print(f"  {'配置':<20} {'准确率':<12} {'平均Token':<12} {'平均时间(s)':<12} {'准确率变化':<12}")
    print(f"  {'-' * 68}")

    for r in all_results:
        name = r["config_name"]
        acc = f"{r['accuracy']:.2%}"
        tokens = str(r["avg_tokens_per_task"])
        t = f"{r['avg_execution_time']:.2f}"

        if baseline and name == baseline["config_name"]:
            delta_str = "(baseline)"
        elif name in deltas:
            d = deltas[name]
            delta_str = f"{d['accuracy_delta']:+.2%}"
        else:
            delta_str = "-"

        print(f"  {name:<20} {acc:<12} {tokens:<12} {t:<12} {delta_str:<12}")

    print(f"{'=' * 70}")
    print(f"  报告已保存到 results/ablation_report.json")

    return report


# ====================================================================
# CLI 入口
# ====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="PECS 多智能体框架消融实验评测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m benchmarks.ablation_eval                            # 运行全部6组配置
  python -m benchmarks.ablation_eval --config full_pecs         # 仅运行指定配置
  python -m benchmarks.ablation_eval --num-samples 10           # 每组10个样本
  python -m benchmarks.ablation_eval --config no_critic --num-samples 5
  python -m benchmarks.ablation_eval --config critic_no_reflect
  python -m benchmarks.ablation_eval --config synthesizer_no_replan
        """,
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="指定单个消融配置名称（full_pecs / no_critic / no_synthesizer / "
             "single_agent / critic_no_reflect / synthesizer_no_replan），"
             "不指定则运行全部",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=None,
        help="每组配置评测的样本数（默认全部）",
    )

    args = parser.parse_args()

    if args.config:
        # 运行单个配置
        result = run_ablation_config(args.config, args.num_samples)
        # 单配置也保存结果
        save_results(result, f"ablation_{args.config}.json")
    else:
        # 运行全部配置
        run_all_ablations(args.num_samples)


if __name__ == "__main__":
    main()
