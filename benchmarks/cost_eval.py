"""
Token budget scheduling cost ablation.

A/B 对比法：禁用启发式后，分别用紧预算和宽预算运行同一任务，
测量预算感知调度的真实 Token 节省比例。

- 紧预算 (BUDGETED_TOKEN_BUDGET=1000)：触发预算调度（跳过 Critic、紧急综合等）
- 宽预算 (UNBUDGETED_TOKEN_BUDGET=50000)：不触发任何预算调度，作为基线
- 节省比例 = (宽预算 - 紧预算) / 宽预算

选用复杂任务（3步+）进行消融，确保 Token 消耗足够触发降级阈值。
"""
from __future__ import annotations

from typing import Optional

from benchmarks.gaia_eval import GAIA_L1_SAMPLES, save_results
from graph.builder import run_task

# 紧预算：触发 70%/85%/95% 三级降级
BUDGETED_TOKEN_BUDGET = 800
# 宽预算：不触发任何预算调度
UNBUDGETED_TOKEN_BUDGET = 50000

# 选用多步骤复杂任务进行消融（确保 Token 消耗足够触发调度）
# gaia_l1_012 (3步搜索+计算), gaia_l1_013 (2步搜索+计算), gaia_l1_015 (2步搜索+计算)
COST_ABLATION_INDICES = [11, 12, 14]


def evaluate_cost_ablation(
    num_samples: Optional[int] = 3,
    token_budget: int = 50000,
) -> dict:
    """
    成本消融评测

    对每个样本运行两次（禁用启发式）：
    1. 紧预算 (800 tokens)：预算调度激活，跳过低风险 Critic、紧急综合等
    2. 宽预算 (50000 tokens)：无预算调度，完整执行所有角色

    节省比例 = (宽预算消耗 - 紧预算消耗) / 宽预算消耗
    """
    if num_samples and num_samples <= len(COST_ABLATION_INDICES):
        indices = COST_ABLATION_INDICES[:num_samples]
        samples = [GAIA_L1_SAMPLES[i] for i in indices]
    else:
        samples = GAIA_L1_SAMPLES[:num_samples] if num_samples else GAIA_L1_SAMPLES
    details = []
    budgeted_total = 0
    unbudgeted_total = 0

    for sample in samples:
        task_id = sample["task_id"]
        question = sample["question"]

        # A: 紧预算运行（预算调度激活）
        budgeted_state = run_task(
            question, token_budget=BUDGETED_TOKEN_BUDGET, use_heuristics=False
        )
        budgeted_tokens = budgeted_state.get("token_used", 0)
        budgeted_decisions = budgeted_state.get("scheduler_decisions", [])

        # B: 宽预算运行（无预算调度，基线）
        unbudgeted_state = run_task(
            question, token_budget=UNBUDGETED_TOKEN_BUDGET, use_heuristics=False
        )
        unbudgeted_tokens = unbudgeted_state.get("token_used", 0)

        budgeted_total += budgeted_tokens
        unbudgeted_total += unbudgeted_tokens

        saved = unbudgeted_tokens - budgeted_tokens
        saved_pct = round(saved / unbudgeted_tokens * 100, 1) if unbudgeted_tokens else 0

        details.append({
            "task_id": task_id,
            "budgeted_tokens": budgeted_tokens,
            "unbudgeted_tokens": unbudgeted_tokens,
            "saved_tokens": saved,
            "saved_pct": saved_pct,
            "scheduler_decisions": [
                f"{d.get('actor','?')}:{d.get('decision','?')}" for d in budgeted_decisions
            ],
        })

    avg_budgeted = round(budgeted_total / len(samples)) if samples else 0
    avg_unbudgeted = round(unbudgeted_total / len(samples)) if samples else 0
    total_savings = (
        round((avg_unbudgeted - avg_budgeted) / avg_unbudgeted * 100, 1)
        if avg_unbudgeted
        else 0
    )

    result = {
        "benchmark": "token_budget_ablation",
        "mode": "A/B comparison (heuristics disabled)",
        "budgeted_token_budget": BUDGETED_TOKEN_BUDGET,
        "unbudgeted_token_budget": UNBUDGETED_TOKEN_BUDGET,
        "total_samples": len(samples),
        "budgeted_avg_tokens_per_task": avg_budgeted,
        "unbudgeted_avg_tokens_per_task": avg_unbudgeted,
        "token_savings_pct": total_savings,
        "details": details,
    }
    save_results(result, "cost_ablation.json")
    return result
