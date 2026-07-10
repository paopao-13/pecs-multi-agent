"""
Benchmark report aggregation.

The report separates metric calculation from benchmark execution so the same
logic can be used with local sample/mock data or real GAIA/AgentBench outputs.
"""
from __future__ import annotations

import os
from typing import Optional

from benchmarks.cost_eval import evaluate_cost_ablation
from benchmarks.gaia_eval import evaluate_gaia, save_results
from benchmarks.react_baseline import evaluate_react_gaia
from benchmarks.webshop_eval import evaluate_react_webshop, evaluate_webshop
from config import DEFAULT_TOKEN_BUDGET


TARGETS = {
    "gaia_l1_accuracy": 0.75,
    "gaia_l1_improvement_pp": 15.0,
    "webshop_success_improvement_pp": 18.0,
    "token_savings_pct": 30.0,
}


def build_report(
    gaia_multi: dict,
    gaia_react: dict,
    webshop_multi: dict,
    webshop_react: dict,
    cost_ablation: Optional[dict] = None,
    budgeted_cost: Optional[dict] = None,
    unbudgeted_cost: Optional[dict] = None,
    mode: str = "sample/mock",
) -> dict:
    """Build a target-oriented comparison report from benchmark results."""
    gaia_improvement = _pp(
        gaia_multi.get("accuracy", 0),
        gaia_react.get("accuracy", 0),
    )
    webshop_improvement = _pp(
        webshop_multi.get("success_rate", 0),
        webshop_react.get("success_rate", 0),
    )
    if cost_ablation:
        budgeted_avg_tokens = cost_ablation.get("budgeted_avg_tokens_per_task")
        unbudgeted_avg_tokens = cost_ablation.get("unbudgeted_avg_tokens_per_task")
        ablation_savings = cost_ablation.get("token_savings_pct")
        cost_mode = cost_ablation.get("mode", mode)
    else:
        budgeted_avg_tokens = _avg_tokens(budgeted_cost)
        unbudgeted_avg_tokens = _avg_tokens(unbudgeted_cost)
        ablation_savings = _token_savings(budgeted_cost, unbudgeted_cost)
        cost_mode = mode

    # Token 节省：优先使用多智能体 vs ReAct 的端到端对比
    # 这包含了启发式规划（跳过 LLM 调用）和预算感知调度的综合效果
    multi_avg_tokens = gaia_multi.get("avg_tokens_per_task", 0)
    react_avg_tokens = gaia_react.get("avg_tokens_per_task", 0)
    if react_avg_tokens and react_avg_tokens > 0:
        token_savings = round(
            (react_avg_tokens - multi_avg_tokens) / react_avg_tokens * 100, 2
        )
    else:
        token_savings = ablation_savings or 0

    report = {
        "mode": mode,
        "targets": TARGETS,
        "gaia_l1": {
            "multi_agent_accuracy": gaia_multi.get("accuracy", 0),
            "react_accuracy": gaia_react.get("accuracy", 0),
            "improvement_pp": gaia_improvement,
            "target_met": (
                gaia_multi.get("accuracy", 0) >= TARGETS["gaia_l1_accuracy"]
                and gaia_improvement >= TARGETS["gaia_l1_improvement_pp"]
            ),
        },
        "webshop": {
            "multi_agent_success_rate": webshop_multi.get("success_rate", 0),
            "react_success_rate": webshop_react.get("success_rate", 0),
            "improvement_pp": webshop_improvement,
            "target_met": webshop_improvement >= TARGETS["webshop_success_improvement_pp"],
        },
        "cost": {
            "mode": "multi-agent vs ReAct (end-to-end)",
            "multi_agent_avg_tokens": multi_avg_tokens,
            "react_avg_tokens": react_avg_tokens,
            "ablation_budgeted_avg_tokens": budgeted_avg_tokens,
            "ablation_unbudgeted_avg_tokens": unbudgeted_avg_tokens,
            "ablation_token_savings_pct": ablation_savings,
            "token_savings_pct": token_savings,
            "target_met": (
                token_savings is not None
                and token_savings >= TARGETS["token_savings_pct"]
            ),
        },
        "raw": {
            "gaia_multi": gaia_multi,
            "gaia_react": gaia_react,
            "webshop_multi": webshop_multi,
            "webshop_react": webshop_react,
            "cost_ablation": cost_ablation,
        },
        "note": (
            "mode=sample/mock 表示使用项目内置可重复样例；真实 GAIA/AgentBench "
            "成绩需要接入授权数据集和真实 WebShop 环境后重新运行。"
        ),
    }
    return report


def run_sample_report(
    num_gaia: Optional[int] = None,
    num_webshop: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict:
    """Run local sample/mock benchmarks and save an aggregated report."""
    gaia_multi = evaluate_gaia(num_gaia, token_budget)
    gaia_react = evaluate_react_gaia(num_gaia, token_budget)
    webshop_multi = evaluate_webshop(num_webshop, token_budget)
    webshop_react = evaluate_react_webshop(num_webshop, token_budget)
    cost_samples = min(num_gaia or 3, 3)
    cost_ablation = evaluate_cost_ablation(cost_samples, token_budget)

    report = build_report(
        gaia_multi=gaia_multi,
        gaia_react=gaia_react,
        webshop_multi=webshop_multi,
        webshop_react=webshop_react,
        cost_ablation=cost_ablation,
        mode="sample/mock",
    )
    save_results(report, "target_report.json")
    return report


def _pp(value: float, baseline: float) -> float:
    return round((value - baseline) * 100, 2)


def _avg_tokens(result: Optional[dict]):
    if not result:
        return None
    return result.get("avg_tokens_per_task") or result.get("avg_tokens")


def _token_savings(budgeted: Optional[dict], unbudgeted: Optional[dict]):
    budgeted_avg = _avg_tokens(budgeted)
    unbudgeted_avg = _avg_tokens(unbudgeted)
    if not budgeted_avg or not unbudgeted_avg:
        return None
    return round((unbudgeted_avg - budgeted_avg) / unbudgeted_avg * 100, 2)


if __name__ == "__main__":
    gaia_n = int(os.getenv("GAIA_SAMPLES", "0"))  # 0 = 全部样本
    webshop_n = int(os.getenv("WEBSHOP_SAMPLES", "6"))
    if gaia_n == 0:
        gaia_n = None
    run_sample_report(gaia_n, webshop_n)
