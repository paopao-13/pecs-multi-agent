"""
AgentBench WebShop-style benchmark adapter.

Real AgentBench WebShop requires the external interactive environment. This
module provides a local, deterministic adapter with the same success criterion:
given a shopping instruction, select the target product that satisfies all
constraints. It is useful for CI and architecture regression tests; results are
reported as "sample/mock" unless wired to the real AgentBench environment.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from benchmarks.gaia_eval import save_results
from config import DEFAULT_TOKEN_BUDGET, LLM_API_KEY
from graph.builder import run_task
from graph.token_budget import estimate_tokens
from tools.webshop import DEFAULT_CATALOG


WEBSHOP_SAMPLES = [
    {
        "task_id": "webshop_001",
        "instruction": "Find an organic jasmine green tea with at least 100 bags under $20.",
        "target_id": "ws_tea_001",
    },
    {
        "task_id": "webshop_002",
        "instruction": "Find a decaf chamomile herbal tea under $16.",
        "target_id": "ws_tea_002",
    },
    {
        "task_id": "webshop_003",
        "instruction": "Need a USB-C 65W GaN dual port charger under $35.",
        "target_id": "ws_usb_001",
    },
    {
        "task_id": "webshop_004",
        "instruction": "Buy an insulated stainless steel 24 oz water bottle under $25.",
        "target_id": "ws_bottle_001",
    },
    {
        "task_id": "webshop_005",
        "instruction": "I want a silent wireless ergonomic mouse under $30.",
        "target_id": "ws_mouse_001",
    },
    {
        "task_id": "webshop_006",
        "instruction": "Find a compact USB-C charger under $18.",
        "target_id": "ws_usb_002",
    },
]


def run_webshop_task(
    instruction: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict:
    """Run the LangGraph multi-agent framework on one WebShop-style task."""
    query = f"WebShop任务：{instruction}"
    return run_task(query, token_budget)


def run_react_webshop_task(instruction: str, token_budget: int = DEFAULT_TOKEN_BUDGET) -> dict:
    """
    ReAct 单 Agent 基线 —— 使用同一 LLM 模型进行 WebShop 任务。

    与多智能体框架使用相同的 GLM 模型和 webshop 工具，
    保证对比公平性。ReAct 单 Agent 负责推理、调用工具、输出答案。
    """
    from benchmarks.react_baseline import run_react_task
    query = f"WebShop任务：{instruction}"
    return run_react_task(query, token_budget, max_steps=5)


def evaluate_webshop(
    num_samples: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict:
    """Evaluate the multi-agent framework on local WebShop-style samples."""
    samples = WEBSHOP_SAMPLES[:num_samples] if num_samples else WEBSHOP_SAMPLES
    return _evaluate(samples, token_budget, agent_type="multi_agent")


def evaluate_react_webshop(
    num_samples: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict:
    """Evaluate the ReAct-style local baseline on WebShop-style samples."""
    samples = WEBSHOP_SAMPLES[:num_samples] if num_samples else WEBSHOP_SAMPLES
    return _evaluate(samples, token_budget, agent_type="react_baseline")


def _evaluate(samples: List[Dict[str, Any]], token_budget: int, agent_type: str) -> dict:
    details = []
    success_count = 0
    total_tokens = 0

    for sample in samples:
        if agent_type == "react_baseline":
            state = run_react_webshop_task(sample["instruction"], token_budget)
        else:
            state = run_webshop_task(sample["instruction"], token_budget)

        predicted = state.get("final_answer", "")
        success = sample["target_id"] in predicted
        success_count += int(success)
        tokens = state.get("token_used", 0)
        total_tokens += tokens

        details.append({
            "task_id": sample["task_id"],
            "instruction": sample["instruction"],
            "target_id": sample["target_id"],
            "predicted": predicted,
            "success": success,
            "tokens_used": tokens,
            "logs": state.get("logs", []),
        })

    success_rate = success_count / len(samples) if samples else 0
    result = {
        "benchmark": "webshop_local_adapter",
        "mode": "real_api" if LLM_API_KEY else "sample/mock",
        "agent_type": agent_type,
        "total_samples": len(samples),
        "success_count": success_count,
        "success_rate": round(success_rate, 4),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(total_tokens / len(samples)) if samples else 0,
        "details": details,
    }

    filename = "webshop_multi_agent.json" if agent_type == "multi_agent" else "webshop_react_baseline.json"
    save_results(result, filename)
    return result
