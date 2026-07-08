"""
AutoGen 框架对照评测脚本

AutoGen 是微软开源的多智能体对话框架，核心模式是
AssistantAgent + UserProxyAgent 的对话协作。

本脚本复用项目内置的 28 道 Mock GAIA Level 1 样例，
统一使用 DeepSeek-V3 模型，与 PECS 多智能体框架和 ReAct
单 Agent 基线进行公平对比。

依赖安装：
    pip install pyautogen

CLI 用法：
    python -m benchmarks.eval_autogen                # 评测全部28道
    python -m benchmarks.eval_autogen --num-samples 10   # 评测前10道
"""
import argparse
import os
import sys

from benchmarks.gaia_eval import GAIA_L1_SAMPLES, evaluate_answer, save_results
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEFAULT_TOKEN_BUDGET,
    LLM_TEMPERATURE,
)

# ---------- AutoGen 可用性检查 ----------
try:
    import autogen

    _AUTOGEN_AVAILABLE = True
except ImportError:
    _AUTOGEN_AVAILABLE = False


# AutoGen 助手的系统提示词
AUTOGEN_SYSTEM_PROMPT = """你是一个能干的 AI 助手。请直接回答用户的问题。

如果你需要计算，可以编写 Python 代码并在代码块中给出，系统会自动执行。
如果问题涉及事实知识，请基于你的知识给出简洁准确的答案。
最终请用 "Final Answer: <答案>" 的格式给出最终答案。"""


def _build_llm_config(api_key: str):
    """
    构建 AutoGen 的 llm_config，指向 DeepSeek API。

    DeepSeek 兼容 OpenAI 接口格式，因此可以直接配置为
    AutoGen 的 config_list 项。
    """
    config_list = [
        {
            "model": DEEPSEEK_MODEL,
            "api_key": api_key,
            "base_url": DEEPSEEK_BASE_URL,
        }
    ]
    return {
        "config_list": config_list,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": 2048,
    }


def _extract_final_answer(chat_history: list) -> str:
    """
    从 AutoGen 对话历史中提取最终答案。

    策略（优先级从高到低）：
    1. 找到最后一条包含 "Final Answer:" 的消息
    2. 取 Assistant 最后一条非空消息
    3. 兜底返回空字符串
    """
    # 策略1：找 "Final Answer:" 标记
    for msg in reversed(chat_history):
        content = msg.get("content", "")
        if content and "Final Answer:" in content:
            idx = content.index("Final Answer:")
            answer = content[idx + len("Final Answer:"):].strip()
            # 去掉可能的换行和多余文本
            answer = answer.split("\n")[0].strip()
            return answer

    # 策略2：取 Assistant 最后一条消息
    for msg in reversed(chat_history):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "assistant" and content and content.strip():
            return content.strip()

    return ""


def _extract_token_usage(assistant, user_proxy) -> int:
    """
    从 AutoGen agent 中提取 Token 使用量。

    AutoGen 的 ConversableAgent 提供 collect_usage_summary 方法
    （较新版本），返回各 agent 的 token 使用统计。如果版本较旧
    或无法获取，则回退到基于消息长度的粗略估算。
    """
    total_tokens = 0

    # 方式1：尝试通过 collect_usage_summary 获取精确 token
    try:
        usage_summary = assistant.collect_usage_summary()
        if usage_summary:
            # usage_summary 是一个 dict: {agent_name: {"total_cost": ..., "usage": {...}}}
            for agent_name, usage in usage_summary.items():
                if isinstance(usage, dict):
                    u = usage.get("usage", usage)
                    total_tokens += u.get("total_tokens", 0) or u.get("completion_tokens", 0)
    except Exception:
        pass

    # 方式2：尝试从 user_proxy 获取
    if total_tokens == 0:
        try:
            usage_summary = user_proxy.collect_usage_summary()
            if usage_summary:
                for agent_name, usage in usage_summary.items():
                    if isinstance(usage, dict):
                        u = usage.get("usage", usage)
                        total_tokens += u.get("total_tokens", 0) or u.get("completion_tokens", 0)
        except Exception:
            pass

    # 方式3：基于对话内容估算
    if total_tokens == 0:
        for msg in user_proxy.chat_messages.get(assistant, []):
            content = msg.get("content", "")
            if content:
                total_tokens += len(content) // 3

    return total_tokens


def run_autogen_task(question: str, api_key: str = None) -> dict:
    """
    使用 AutoGen 的 AssistantAgent + UserProxyAgent 模式运行单个任务。

    参数:
        question: 用户问题
        api_key: DeepSeek API Key（None 时使用 config.py 中的配置）

    返回:
        与多智能体框架相同格式的状态字典：
        {
            "query": str,
            "final_answer": str,
            "token_used": int,
            "token_budget": int,
            "logs": list,
        }
    """
    if not _AUTOGEN_AVAILABLE:
        raise ImportError(
            "autogen 库未安装。请运行以下命令安装：\n"
            "    pip install pyautogen\n"
            "AutoGen 是微软开源的多智能体对话框架，"
            "详见 https://github.com/microsoft/autogen"
        )

    api_key = api_key or DEEPSEEK_API_KEY
    if not api_key:
        raise ValueError(
            "未配置 DEEPSEEK_API_KEY。请在 .env 文件中设置，"
            "或通过环境变量传入。"
        )

    logs = []
    logs.append(f"[AutoGen] 开始处理任务: {question}")

    llm_config = _build_llm_config(api_key)

    # 创建 AssistantAgent：负责思考和回答
    assistant = autogen.AssistantAgent(
        name="assistant",
        system_message=AUTOGEN_SYSTEM_PROMPT,
        llm_config=llm_config,
    )

    # 创建 UserProxyAgent：代表用户发起对话
    # human_input_mode="NEVER" 表示不等待人工输入，全自动执行
    # code_execution_config 设置为 False，不执行代码（与项目其他基线保持一致）
    user_proxy = autogen.UserProxyAgent(
        name="user_proxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=5,
        is_termination_msg=lambda msg: "Final Answer:" in (msg.get("content", "") or ""),
        code_execution_config=False,
    )

    # 发起对话
    try:
        user_proxy.initiate_chat(
            assistant,
            message=question,
            clear_history=True,
        )
    except Exception as exc:
        logs.append(f"[AutoGen] 对话执行出错: {type(exc).__name__}: {exc}")
        return {
            "query": question,
            "final_answer": f"[AutoGen 执行失败] {exc}",
            "token_used": 0,
            "token_budget": DEFAULT_TOKEN_BUDGET,
            "logs": logs,
        }

    # 提取对话历史
    chat_history = user_proxy.chat_messages.get(assistant, [])
    logs.append(f"[AutoGen] 对话完成，共 {len(chat_history)} 条消息")

    # 提取最终答案
    final_answer = _extract_final_answer(chat_history)
    if not final_answer:
        final_answer = "未能从 AutoGen 对话中提取答案。"
        logs.append(f"[AutoGen] 未能提取最终答案")
    else:
        logs.append(f"[AutoGen] 最终答案: {final_answer[:80]}...")

    # 提取 Token 使用量
    token_used = _extract_token_usage(assistant, user_proxy)
    logs.append(f"[AutoGen] Token 消耗: {token_used}")

    return {
        "query": question,
        "final_answer": final_answer,
        "token_used": token_used,
        "token_budget": DEFAULT_TOKEN_BUDGET,
        "logs": logs,
    }


def evaluate_autogen_gaia(num_samples: int = None) -> dict:
    """
    在 GAIA Level 1 上批量评测 AutoGen 框架。

    参数:
        num_samples: 评测样本数（None=全部28道）

    返回:
        与 gaia_eval.evaluate_gaia() 相同格式的结果字典：
        {
            "agent_type": "autogen",
            "total_samples": int,
            "correct_count": int,
            "accuracy": float,
            "total_tokens": int,
            "avg_tokens_per_task": int,
            "details": list,
        }
    """
    if not _AUTOGEN_AVAILABLE:
        raise ImportError(
            "autogen 库未安装。请运行以下命令安装：\n"
            "    pip install pyautogen"
        )

    samples = GAIA_L1_SAMPLES[:num_samples] if num_samples else GAIA_L1_SAMPLES

    results = []
    correct_count = 0
    total_tokens = 0

    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        ground_truth = sample["answer"]

        print(f"[{i+1}/{len(samples)}] AutoGen 评估任务 {task_id}: {question}")

        state = run_autogen_task(question)

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

        print(f"  -> 预测: {predicted[:80]}... | 正确: {is_correct}")

    accuracy = correct_count / len(samples) if samples else 0
    avg_tokens = total_tokens / len(samples) if samples else 0

    eval_result = {
        "agent_type": "autogen",
        "total_samples": len(samples),
        "correct_count": correct_count,
        "accuracy": round(accuracy, 4),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(avg_tokens),
        "details": results,
    }

    save_results(eval_result, "gaia_autogen.json")

    return eval_result


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(
        description="AutoGen 框架 GAIA 评测脚本"
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=None,
        help="评测样本数（默认全部28道）",
    )
    args = parser.parse_args()

    # 将 --num-samples=0 视为全部
    num_samples = args.num_samples if args.num_samples and args.num_samples > 0 else None

    print("=" * 60)
    print("  AutoGen 框架 GAIA Level 1 评测")
    print(f"  模型: {DEEPSEEK_MODEL}")
    print(f"  样本数: {num_samples or '全部'}")
    print("=" * 60)

    result = evaluate_autogen_gaia(num_samples)

    print("\n" + "=" * 60)
    print("  评测结果汇总")
    print("=" * 60)
    print(f"  准确率: {result['accuracy']:.2%} ({result['correct_count']}/{result['total_samples']})")
    print(f"  总 Token: {result['total_tokens']}")
    print(f"  平均 Token/任务: {result['avg_tokens_per_task']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
