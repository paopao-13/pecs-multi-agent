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
from config import DEFAULT_TOKEN_BUDGET, LLM_API_KEY


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
        "role_token_stats": _extract_role_token_stats(
            gaia_multi, gaia_react
        ),
        "note": (
            f"mode={'real_api' if LLM_API_KEY else 'sample/mock'} — "
            + ("使用真实 LLM API (GLM-4.7-Flash) 运行评测。"
               if LLM_API_KEY
               else "使用项目内置可重复样例；配置 LLM_API_KEY 后可运行真实 API 评测。")
        ),
    }
    return report


def run_sample_report(
    num_gaia: Optional[int] = None,
    num_webshop: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict:
    """Run local sample/mock benchmarks and save an aggregated report.

    注意：此函数会重新运行所有评测（GAIA + WebShop + 成本消融）。
    如果已有真实环境评测数据（results/webshop_run.json 等），建议用
    build_report_from_files() 从已有数据构建报告，避免重跑覆盖真实数据。
    """
    gaia_multi = evaluate_gaia(num_gaia, token_budget)
    gaia_react = evaluate_react_gaia(num_gaia, token_budget)
    webshop_multi = evaluate_webshop(num_webshop, token_budget)
    webshop_react = evaluate_react_webshop(num_webshop, token_budget)
    cost_samples = min(num_gaia or 3, 3)
    cost_ablation = evaluate_cost_ablation(cost_samples, token_budget)

    mode = "real_api" if LLM_API_KEY else "sample/mock"
    report = build_report(
        gaia_multi=gaia_multi,
        gaia_react=gaia_react,
        webshop_multi=webshop_multi,
        webshop_react=webshop_react,
        cost_ablation=cost_ablation,
        mode=mode,
    )
    save_results(report, "target_report.json")
    return report


def _load_result(filename: str) -> Optional[dict]:
    """从 results/ 目录读取已有评测结果 JSON。"""
    import json
    path = os.path.join("results", filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_report_from_files() -> dict:
    """从已有结果 JSON 文件构建聚合报告，不重跑任何评测。

    解决数据不一致问题：run_sample_report() 在无 WEBSHOP_SERVER_URL 时会
    重跑 evaluate_webshop() 走本地 mock，覆盖真实环境的 12 题数据。
    本函数直接读取 results/ 下的已有 JSON，保证报告与原始数据一致。

    需要的文件（缺失的会跳过对应章节）：
    - gaia_multi_agent.json / gaia_react_baseline.json
    - webshop_multi_agent.json / webshop_react_baseline.json
    - cost_ablation.json
    可选：webshop_run.json（含三组消融对比，会附加到报告）
    """
    gaia_multi = _load_result("gaia_multi_agent.json")
    gaia_react = _load_result("gaia_react_baseline.json")
    webshop_multi = _load_result("webshop_multi_agent.json")
    webshop_react = _load_result("webshop_react_baseline.json")
    cost_ablation = _load_result("cost_ablation.json")

    if not gaia_multi or not gaia_react:
        raise FileNotFoundError(
            "缺少 GAIA 评测结果，请先运行 python run_resumable.py 生成 "
            "results/gaia_multi_agent.json 和 results/gaia_react_baseline.json"
        )
    if not webshop_multi or not webshop_react:
        raise FileNotFoundError(
            "缺少 WebShop 评测结果，请先运行 python run_webshop.py 生成 "
            "results/webshop_multi_agent.json 和 results/webshop_react_baseline.json"
        )

    mode = "real_api" if LLM_API_KEY else "sample/mock"
    report = build_report(
        gaia_multi=gaia_multi,
        gaia_react=gaia_react,
        webshop_multi=webshop_multi,
        webshop_react=webshop_react,
        cost_ablation=cost_ablation,
        mode=mode,
    )

    # 附加 WebShop 三组消融对比（如果有 webshop_run.json）
    webshop_run = _load_result("webshop_run.json")
    if webshop_run and "react_light" in webshop_run:
        report["webshop_ablation"] = {
            "description": "WebShop 规则层消融实验（证明 PECS 优势来源）",
            "pecs_full": webshop_run.get("multi_agent", {}),
            "react_light": webshop_run.get("react_light", {}),
            "react_pure_llm": webshop_run.get("react_baseline", {}),
            "diff": webshop_run.get("diff", {}),
        }

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


def _extract_role_token_stats(
    gaia_multi: Optional[dict],
    gaia_react: Optional[dict],
) -> dict:
    """
    从评测结果中提取分角色 Token 消耗统计。

    遍历 details 列表中每条任务的 role_token_used 字段，
    计算 planner/executor/critic/synthesizer 的平均 Token 消耗。
    """
    role_keys = ["planner", "executor", "critic", "synthesizer"]
    stats = {}

    for label, result in [("multi_agent", gaia_multi), ("react", gaia_react)]:
        if not result or not isinstance(result, dict):
            continue
        details = result.get("details", [])
        if not details:
            continue

        role_totals = {k: 0 for k in role_keys}
        count = 0
        for item in details:
            role_used = item.get("role_token_used") or item.get("tokens_breakdown")
            if isinstance(role_used, dict):
                for k in role_keys:
                    role_totals[k] += int(role_used.get(k, 0))
                count += 1

        if count > 0:
            stats[label] = {
                k: round(role_totals[k] / count) for k in role_keys
            }
            stats[label]["total_avg"] = sum(role_totals.values()) // count

    return stats


if __name__ == "__main__":
    import sys
    # 默认从已有结果文件构建报告（不重跑，避免覆盖真实环境数据）
    # 加 --rerun 参数才重跑所有评测
    if "--rerun" in sys.argv:
        gaia_n = int(os.getenv("GAIA_SAMPLES", "0"))  # 0 = 全部样本
        webshop_n = int(os.getenv("WEBSHOP_SAMPLES", "6"))
        if gaia_n == 0:
            gaia_n = None
        run_sample_report(gaia_n, webshop_n)
    else:
        build_report_from_files()
