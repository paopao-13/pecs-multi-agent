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
from tools.webshop import parse_webshop_reward


def _extract_webshop_reward(state: dict, predicted: str) -> float:
    """公平提取 WebShop reward：从工具原始返回和 final_answer 多个来源取最大值。

    之前只从 final_answer 解析，但 ReAct 的 LLM 总结可能省略 reward 值（如
    "已找到符合要求的商品"不含数字），导致 ReAct 被误判 0%。本函数从 state 的
    steps_log / logs / final_answer 所有文本里提取 reward，取最大值（代表最佳表现）。
    """
    best_reward = 0.0
    # 1. 从 steps_log 的 result 字段提取（ReAct 和 PECS 都有）
    for step in state.get("steps_log", []):
        if isinstance(step, dict):
            result = step.get("result", "") or step.get("observation", "")
            if result:
                r = parse_webshop_reward(str(result))
                best_reward = max(best_reward, r)
    # 2. 从 logs 提取（PECS 的 Executor 日志含 "奖励=X.XXX"）
    for log in state.get("logs", []):
        r = parse_webshop_reward(str(log))
        best_reward = max(best_reward, r)
    # 3. fallback: 从 final_answer 提取
    r = parse_webshop_reward(predicted)
    best_reward = max(best_reward, r)
    return best_reward
from graph.builder import run_task
from graph.token_budget import estimate_tokens
from tools.webshop import DEFAULT_CATALOG, use_real_env, parse_webshop_reward


WEBSHOP_SAMPLES = [
    # 从 WebShop-small 数据集 6910 个 goals 中随机采样 12 个真实 instruction_text
    # （random.seed(42), 服装类, 确保能匹配到真实 goal 和商品）
    {"task_id": "webshop_001", "instruction": "Find me machine wash women's swimsuits & cover ups with drawstring closure, elastic waistband, tummy control with color: black, and size: medium, and price lower than 30.00 dollars", "target_id": "ws_001"},
    {"task_id": "webshop_002", "instruction": "Find me men's t-shirts & tanks with short sleeve, fashion design, long sleeve, button closure with color: e-white, and size: 5x-large, and price lower than 50.00 dollars", "target_id": "ws_002"},
    {"task_id": "webshop_003", "instruction": "Find me quick drying, moisture wicking women's activewear with long sleeve with color: b-peach-thumbhole, and size: small, and price lower than 40.00 dollars", "target_id": "ws_003"},
    {"task_id": "webshop_004", "instruction": "Find me men's loafers & slip-ons with rubber outsole, rubber sole with color: blue-a, and size: 10, and price lower than 40.00 dollars", "target_id": "ws_004"},
    {"task_id": "webshop_005", "instruction": "Find me machine wash men's dress shirts with cotton spandex, classic fit, short sleeve with color: monaco blue, and size: 2x, and price lower than 60.00 dollars", "target_id": "ws_005"},
    {"task_id": "webshop_006", "instruction": "Find me men's shorts with drawstring closure, elastic waist for gym workout with color: #1 black green, and size: 38, and price lower than 50.00 dollars", "target_id": "ws_006"},
    {"task_id": "webshop_007", "instruction": "Find me butt lifting, light weight women's shorts with high waist, tummy control with color: black, and size: xx-large, and price lower than 50.00 dollars", "target_id": "ws_007"},
    {"task_id": "webshop_008", "instruction": "Find me machine wash men's tuxedo shirts with polyester heathers, heathers cotton, cotton heather, needle sleeve, classic fit with color: heather blue, and fit type: men, and size: large, and price lower than 30.00 dollars", "target_id": "ws_008"},
    {"task_id": "webshop_009", "instruction": "Find me men's sleep & lounge with long sleeve, elastic waistband for daily wear with color: multi 4, and size: small, and price lower than 80.00 dollars", "target_id": "ws_009"},
    {"task_id": "webshop_010", "instruction": "Find me slim fit, machine wash men's casual button-down shirts with button closure, long sleeve with color: aqua blue, and size: x-large, and price lower than 40.00 dollars", "target_id": "ws_010"},
    {"task_id": "webshop_011", "instruction": "Find me machine wash, wash cold women's fashion hoodies & sweatshirts for dry clean, tumble dry with color: white, and size: 4x-large, and price lower than 70.00 dollars", "target_id": "ws_011"},
    {"task_id": "webshop_012", "instruction": "Find me hand wash women's sweaters with long sleeve, stretch fabric, polyester spandex for teen girls, daily wear with color: xnj-tshirt338-white, and size: x-large, and price lower than 40.00 dollars", "target_id": "ws_012"},
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


def run_react_webshop_task_light(instruction: str, token_budget: int = DEFAULT_TOKEN_BUDGET) -> dict:
    """
    ReAct 单 Agent 基线（轻量规则层版）—— 消融实验中间档。

    与 run_react_webshop_task（纯 LLM）的区别：
    - webshop 工具用 webshop_interact_react_light（有 Buy 就买的购物常识）
    - 不用 PECS 的完整规则层（不强制 click[ASIN] 打破 search 循环）

    三组对比：PECS 完整规则层 / ReAct-light 轻量规则层 / ReAct 纯 LLM
    证明 PECS 优势来自"打破 search 循环"的规则2，而非"有规则层"本身。
    """
    from benchmarks.react_baseline import run_react_task
    from tools.webshop import webshop_interact_react_light
    query = f"WebShop任务：{instruction}"
    return run_react_task(query, token_budget, max_steps=5, webshop_fn=webshop_interact_react_light)


def evaluate_webshop(
    num_samples: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    mode: Optional[str] = None,
) -> dict:
    """Evaluate the multi-agent framework on WebShop-style samples.

    mode="real" 使用真实 AgentBench 环境的奖励分判定成功；
    mode="local"（默认）使用本地 mock 的 target_id 子串匹配。
    不传 mode 时自动根据 WEBSHOP_SERVER_URL 环境变量判断。
    """
    samples = WEBSHOP_SAMPLES[:num_samples] if num_samples else WEBSHOP_SAMPLES
    return _evaluate(samples, token_budget, agent_type="multi_agent", mode=mode)


def evaluate_react_webshop(
    num_samples: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    mode: Optional[str] = None,
) -> dict:
    """Evaluate the ReAct-style local baseline on WebShop-style samples."""
    samples = WEBSHOP_SAMPLES[:num_samples] if num_samples else WEBSHOP_SAMPLES
    return _evaluate(samples, token_budget, agent_type="react_baseline", mode=mode)


def evaluate_react_webshop_light(
    num_samples: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    mode: Optional[str] = None,
) -> dict:
    """Evaluate ReAct with light rule layer (Buy-button rule only, no search-loop breaking)."""
    samples = WEBSHOP_SAMPLES[:num_samples] if num_samples else WEBSHOP_SAMPLES
    return _evaluate(samples, token_budget, agent_type="react_light", mode=mode)


def _evaluate(samples: List[Dict[str, Any]], token_budget: int, agent_type: str, mode: Optional[str] = None) -> dict:
    details = []
    success_count = 0
    total_tokens = 0

    # 自动判定评测模式：真实环境 vs 本地 mock
    mode = mode or ("real" if use_real_env() else "local")
    # 真实环境下，奖励分 ≥ 该阈值视为"选对"（1.0=完全匹配，可酌情下调）
    REAL_REWARD_THRESHOLD = 0.5

    for sample in samples:
        if agent_type == "react_baseline":
            state = run_react_webshop_task(sample["instruction"], token_budget)
        elif agent_type == "react_light":
            state = run_react_webshop_task_light(sample["instruction"], token_budget)
        else:
            state = run_webshop_task(sample["instruction"], token_budget)

        predicted = state.get("final_answer", "")
        if mode == "real":
            # 公平提取 reward：不能只靠 final_answer（ReAct 的 LLM 总结可能省略 reward 值）
            # 优先从工具原始返回（steps_log/results）提取，fallback 到 final_answer 解析
            reward = _extract_webshop_reward(state, predicted)
            success = reward >= REAL_REWARD_THRESHOLD
        else:
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

    _filenames = {
        "multi_agent": "webshop_multi_agent.json",
        "react_baseline": "webshop_react_baseline.json",
        "react_light": "webshop_react_light.json",
    }
    filename = _filenames.get(agent_type, "webshop_react_baseline.json")
    save_results(result, filename)
    return result
