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

重要规则：
1. 你必须使用工具来完成任务，不要直接从记忆中给出答案
2. 对于计算问题，必须使用 python 工具执行计算，不要心算
3. 对于购物问题，必须使用 webshop 工具选择商品，不要编造商品名
4. 对于信息查询，必须使用 search 工具搜索，不要凭记忆回答
5. 只有在获得足够的 Observation 后，才能给出 Final Answer

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

注意：
- Action 必须是上述工具名之一，Action Input 必须是合法 JSON
- 每次只能执行一个工具
- 不要在 Thought 中直接给出最终答案，必须通过工具验证
- Final Answer 应该简短精确，直接回答问题
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
    no_tool_count = 0  # 连续不调用工具的次数

    for step in range(max_steps):
        # 调用 LLM 进行推理
        prompt = f"{conversation}\n\n请继续（Thought/Action/Final Answer）。"
        
        # 如果连续2步没调用工具，强制要求使用工具
        if no_tool_count >= 2:
            prompt += "\n\n警告：你已经连续多步没有使用工具。请必须使用工具来完成任务，不要直接给出答案。"
        
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
            no_tool_count += 1
            conversation += f"\n{response}\n"
            continue
        else:
            no_tool_count = 0  # 重置计数

        # 执行工具
        # 公平对比：webshop 动作用纯 LLM 决策（webshop_interact_react），
        # 不用 PECS 的规则层（webshop_interact）。这是 ReAct vs PECS 的核心差异：
        # PECS 的 Executor 启发式优化打破 search 循环，ReAct 纯 LLM 自己决策。
        if action == "webshop":
            from tools.webshop import webshop_interact_react
            result = webshop_interact_react(action_input)
        else:
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

        # 限制上下文长度，保留最近2轮交互，防止 token 雪崩
        if len(conversation) > 4000:
            lines = conversation.split("\n")
            # 保留前2行（任务描述）和最后12行（最近2轮完整交互）
            conversation = "\n".join(lines[:2]) + "\n...（省略早期交互）...\n" + "\n".join(lines[-12:])

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
