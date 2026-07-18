"""
Executor Agent —— 执行者

职责：
  按 Planner 制定的计划，逐步调用工具执行任务。
  类比：就像工程师拿到任务清单，逐个完成并记录结果。

输入：AgentState 中的 plan（步骤列表）
输出：更新 AgentState 中的 results（执行结果）

工作流程：
  1. 取出当前步骤（current_step_idx 指向的步骤）
  2. 根据 action 类型调用对应工具
  3. 对于需要 LLM 推理的步骤（如"分析搜索结果"），先调 LLM 生成工具参数
  4. 记录执行结果到 results
  5. 更新 current_step_idx
"""
from agents.heuristics import build_heuristic_args
from agents.llm_utils import call_llm
from graph.token_budget import estimate_tokens, record_token_usage
from tools import execute_tool
from config import MAX_RETRIES

# Executor 的系统提示词
EXECUTOR_SYSTEM_PROMPT = """你是一个任务执行专家（Executor），负责执行计划中的单个步骤。

你的职责：
1. 理解当前步骤的描述和目标
2. 如果步骤参数不完整，根据步骤描述生成合适的工具参数
3. 确保工具调用的参数格式正确

注意：
- search工具的args需要: {"query": "搜索关键词"}
- python工具的args需要: {"code": "Python代码"}
  ⚠️ Python代码安全规则：
  - math、json、re、datetime 模块已预导入，直接使用即可
  - 禁止写 import 语句（会被安全沙箱拦截导致执行失败）
  - 禁止使用 __import__、exec、eval、open 等危险函数
  - 正确写法：print(math.factorial(100))
  - 错误写法：import math; print(math.factorial(100))
- file_read工具的args需要: {"path": "文件路径"}
- api_call工具的args需要: {"url": "API地址", "method": "GET/POST"}
- webshop工具的args需要: {"instruction": "购物需求", "catalog": [...可选商品列表...]}
"""


def executor_node(state: dict) -> dict:
    """
    Executor 节点函数

    执行当前步骤：
    1. 从 plan 中取出 current_step_idx 指向的步骤
    2. 如果步骤参数不完整，调用 LLM 生成参数
    3. 调用工具执行
    4. 记录结果
    """
    plan = state.get("plan", [])
    current_idx = state.get("current_step_idx", 0)
    results = state.get("results", [])
    retry_feedback = state.get("retry_feedback", "")
    logs = state.get("logs", [])
    query = state.get("query", "")
    executor_tokens = 0
    use_heuristics = state.get("use_heuristics", True)

    # 如果没有步骤或已完成所有步骤，直接返回
    if not plan or current_idx >= len(plan):
        logs.append("[Executor] 所有步骤已执行完成")
        return {"results": results, "logs": logs}

    # 获取当前步骤
    step = plan[current_idx]
    action = step.get("action", "")
    description = step.get("description", "")
    args = step.get("args", {})
    retry_count = step.get("retry_count", 0)

    logs.append(f"[Executor] 执行步骤 {step.get('id', current_idx + 1)}: {description} (工具: {action})")

    # 如果有重试反馈，让 LLM 根据反馈调整参数
    if retry_feedback and retry_count > 0:
        prompt = f"""
用户原始任务: {query}
步骤描述: {description}
当前参数: {args}
重试反馈: {retry_feedback}
已有执行结果:
{_format_prior_results(results)}

请根据反馈修正工具参数，返回修正后的参数（JSON格式）。
"""
        adjusted_args_str, token_consumed = call_llm(prompt, EXECUTOR_SYSTEM_PROMPT, role="executor")
        executor_tokens += token_consumed

        # 尝试解析修正后的参数
        try:
            import json
            adjusted = json.loads(adjusted_args_str)
            if isinstance(adjusted, dict) and "args" in adjusted:
                args = adjusted["args"]
            elif isinstance(adjusted, dict):
                args.update(adjusted)
        except (json.JSONDecodeError, ValueError):
            if use_heuristics:
                heuristic_args = build_heuristic_args(action, description, query, results)
                if heuristic_args:
                    args = heuristic_args

    # 如果参数为空或不完整，让 LLM 根据描述生成参数
    if _args_incomplete(action, args):
        prompt = f"""
用户原始任务: {query}
步骤描述: {description}
工具类型: {action}
已有执行结果:
{_format_prior_results(results)}

请生成该工具的调用参数（JSON格式）。
"""
        generated_args_str, token_consumed = call_llm(prompt, EXECUTOR_SYSTEM_PROMPT, role="executor")
        executor_tokens += token_consumed

        try:
            import json
            generated = json.loads(generated_args_str)
            if isinstance(generated, dict):
                if "args" in generated:
                    args = generated["args"]
                else:
                    args.update(generated)
        except (json.JSONDecodeError, ValueError):
            pass

    # 启发式参数兜底：即使 use_heuristics=False 也允许生成工具参数
    # 因为 build_heuristic_args 只生成工具调用参数（如 python code），
    # 不直接给出答案，答案是工具执行后得到的真实结果，这是合法的
    if _args_incomplete(action, args):
        heuristic_args = build_heuristic_args(action, description, query, results)
        if heuristic_args:
            args = heuristic_args
            if not use_heuristics:
                logs.append("[Executor] LLM参数生成失败，使用启发式参数模板兜底（仅生成工具参数，答案由工具执行得出）")

    # Python 代码安全净化：如果 LLM 生成的代码包含 import，自动剥离
    if action == "python" and "code" in args:
        args["code"] = _sanitize_python_code(args["code"], logs)

    # 调用工具执行
    result = execute_tool(action, args)
    executor_tokens += estimate_tokens(result)

    # 记录执行结果
    result_entry = {
        "step_id": step.get("id", current_idx + 1),
        "action": action,
        "description": description,
        "result": result,
        "success": not result.startswith("错误") and not result.startswith("执行错误") and "失败" not in result[:20],
    }
    results.append(result_entry)

    logs.append(f"[Executor] 步骤 {step.get('id', current_idx + 1)} 完成: {result[:100]}...")

    # 更新步骤状态
    step["status"] = "done" if result_entry["success"] else "failed"
    step["result"] = result
    step["retry_count"] = retry_count

    # 移动到下一步
    current_idx += 1
    token_used, role_token_used, budget_events = record_token_usage(state, "executor", executor_tokens)

    return {
        "plan": plan,
        "results": results,
        "current_step_idx": current_idx,
        "token_used": token_used,
        "role_token_used": role_token_used,
        "budget_events": budget_events,
        "retry_feedback": "",  # 清除重试反馈
        "logs": logs,
    }


def executor_retry_node(state: dict) -> dict:
    """
    Executor 重试节点

    当 Critic 判定结果不合格时，Executor 根据反馈重新执行当前步骤。
    """
    plan = state.get("plan", [])
    current_idx = state.get("current_step_idx", 0)
    retry_feedback = state.get("retry_feedback", "")
    logs = state.get("logs", [])

    # 回退到上一步（因为 executor_node 执行完后 current_idx 已经前进了）
    retry_idx = max(0, current_idx - 1)
    if retry_idx < len(plan):
        step = plan[retry_idx]
        step["retry_count"] = step.get("retry_count", 0) + 1
        step["status"] = "pending"

        logs.append(f"[Executor] 重试步骤 {step.get('id', retry_idx + 1)} (第 {step['retry_count']} 次重试)")

        return {
            "plan": plan,
            "current_step_idx": retry_idx,
            "retry_feedback": retry_feedback,
            "logs": logs,
        }

    return {"logs": logs}


def _sanitize_python_code(code: str, logs: list) -> str:
    """净化 Python 代码：自动移除 import 语句，提示使用预导入模块。"""
    import re as _re
    lines = code.split("\n")
    cleaned = []
    removed = []
    for line in lines:
        stripped = line.strip()
        # 匹配 "import xxx" 或 "from xxx import yyy"
        if _re.match(r"^(import\s|from\s+\w+\s+import\b)", stripped):
            removed.append(stripped)
        else:
            cleaned.append(line)
    if removed:
        logs.append(f"[Executor] 自动净化代码：移除了 {len(removed)} 条 import 语句 "
                     f"({'; '.join(removed)}), math/json/re/datetime 已预导入可直接使用")
    return "\n".join(cleaned)


def _args_incomplete(action: str, args: dict) -> bool:
    if not args:
        return True
    if action == "search":
        return "query" not in args
    if action == "python":
        return "code" not in args
    if action == "file_read":
        return "path" not in args
    if action == "file_parse":
        return "path" not in args
    if action == "web_browse":
        return "url" not in args
    if action == "api_call":
        return "url" not in args
    if action == "webshop":
        return "instruction" not in args
    return False


def _format_prior_results(results: list) -> str:
    if not results:
        return "无"
    parts = []
    for item in results[-5:]:
        parts.append(
            f"步骤{item.get('step_id')}: {item.get('description', '')}\n"
            f"结果: {str(item.get('result', ''))[:1200]}"
        )
    return "\n\n".join(parts)
