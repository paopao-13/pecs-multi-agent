"""
ReAct 单 Agent 基线

ReAct = Reasoning + Acting，是最常见的单 Agent 范式。
单个 Agent 同时负责思考、规划、执行、检查，没有角色分工。

实现这个基线的目的：
  对比多智能体框架 vs 单 Agent 的准确率和 Token 消耗，
  证明四角色分工 + Token 预算感知可以提升准确率并降低成本。
"""
import json
from agents.llm_utils import call_llm
from tools import execute_tool, TOOL_DESCRIPTIONS
from graph.token_budget import estimate_tokens
from config import DEFAULT_TOKEN_BUDGET

REACT_SYSTEM_PROMPT = """你是一个 ReAct 智能体，需要通过推理和行动来完成任务。

请按以下格式工作（重复直到得出答案）：

Thought: 思考当前应该做什么
Action: 工具名称（search/python/file_read/api_call/webshop）
Action Input: 工具参数（JSON格式）
Observation: [系统返回工具执行结果]

... (重复 Thought/Action/Observation)

Thought: 我现在知道答案了
Final Answer: 最终答案

可用工具及参数格式：
- search: {"query": "搜索关键词", "num_results": 3}
- python: {"code": "print(2**10)"}  （math/json/re/datetime已预导入，禁止写import语句）
- file_read: {"path": "文件路径"}
- api_call: {"url": "API地址", "method": "GET"}
- webshop: {"instruction": "购物需求描述"}

注意：Action 必须是上述工具名之一，Action Input 必须是合法 JSON。
"""


def run_react_task(query: str, token_budget: int = DEFAULT_TOKEN_BUDGET, max_steps: int = 5) -> dict:
    """
    运行 ReAct 单 Agent 任务

    和多智能体框架形成对比：
    - 单 Agent 一个人做所有事（规划+执行+检查+综合）
    - 没有 Token 预算感知调度
    - 没有专门的评审角色

    返回与多智能体框架相同格式的状态，方便对比
    """
    token_used = 0
    logs = []
    steps_log = []

    logs.append(f"[ReAct] 开始处理任务: {query}")

    # ReAct 循环
    conversation = f"任务: {query}\n"
    final_answer = ""

    for step in range(max_steps):
        # 调用 LLM 进行推理
        prompt = f"{conversation}\n\n请继续（Thought/Action/Final Answer）。"
        response, tokens = call_llm(prompt, REACT_SYSTEM_PROMPT, role="default")
        token_used += tokens
        logs.append(f"[ReAct] 步骤{step+1} - LLM推理 (消耗 {tokens} tokens)")

        # 解析 LLM 响应
        if "Final Answer:" in response:
            # 提取最终答案
            idx = response.index("Final Answer:")
            final_answer = response[idx + len("Final Answer:"):].strip()
            logs.append(f"[ReAct] 得到最终答案: {final_answer[:80]}...")
            break

        # 解析 Action 和 Action Input
        action, action_input, observation = _parse_react_response(response)

        if action is None:
            # 无法解析，让 LLM 继续尝试
            conversation += f"\n{response}\n"
            continue

        # 执行工具
        result = execute_tool(action, action_input)
        token_used += estimate_tokens(result)

        logs.append(f"[ReAct] 执行 {action}: {result[:80]}...")
        steps_log.append({
            "step": step + 1,
            "action": action,
            "result": result,
        })

        # 添加到对话
        conversation += f"\n{response}\nObservation: {result}\n"

        # 预算检查
        if token_used > token_budget * 0.95:
            logs.append(f"[ReAct] 预算耗尽，强制输出答案")
            # 让 LLM 用已有信息生成答案
            prompt = f"{conversation}\n\n请根据以上信息直接给出 Final Answer。"
            response, tokens = call_llm(prompt, REACT_SYSTEM_PROMPT, role="default")
            token_used += tokens
            if "Final Answer:" in response:
                idx = response.index("Final Answer:")
                final_answer = response[idx + len("Final Answer:"):].strip()
            else:
                final_answer = response
            break

    if not final_answer:
        final_answer = "未能完成任务。"

    return {
        "query": query,
        "final_answer": final_answer,
        "token_used": token_used,
        "token_budget": token_budget,
        "logs": logs,
        "steps": steps_log,
    }


def _parse_react_response(response: str):
    """解析 ReAct 格式的响应，提取 Action 和 Action Input"""
    action = None
    action_input = {}
    observation = None

    lines = response.split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("Action:") and not line.startswith("Action Input:"):
            action = line[len("Action:"):].strip().lower()
        elif line.startswith("Action Input:"):
            input_str = line[len("Action Input:"):].strip()
            try:
                action_input = json.loads(input_str)
            except json.JSONDecodeError:
                # 尝试解析简单的 key: value 格式
                action_input = {"raw": input_str}

    return action, action_input, observation


def evaluate_react_gaia(num_samples: int = None, token_budget: int = DEFAULT_TOKEN_BUDGET) -> dict:
    """
    在 GAIA Level 1 上评估 ReAct 基线

    返回与 evaluate_gaia() 相同格式的结果，方便对比
    """
    import time
    from benchmarks.gaia_eval import GAIA_L1_SAMPLES, evaluate_answer, save_results

    samples = GAIA_L1_SAMPLES[:num_samples] if num_samples else GAIA_L1_SAMPLES

    results = []
    correct_count = 0
    total_tokens = 0

    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        ground_truth = sample["answer"]

        print(f"[{i+1}/{len(samples)}] ReAct 评估任务 {task_id}: {question}", flush=True)

        state = run_react_task(question, token_budget)

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
        })

        print(f"  → 预测: {predicted[:80]}... | 正确: {is_correct}", flush=True)

        # 任务间延迟，避免 API 限流
        if i < len(samples) - 1:
            time.sleep(3)

    accuracy = correct_count / len(samples) if samples else 0
    avg_tokens = total_tokens / len(samples) if samples else 0

    eval_result = {
        "agent_type": "react_baseline",
        "total_samples": len(samples),
        "correct_count": correct_count,
        "accuracy": round(accuracy, 4),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(avg_tokens),
        "details": results,
    }

    save_results(eval_result, "gaia_react_baseline.json")

    return eval_result
