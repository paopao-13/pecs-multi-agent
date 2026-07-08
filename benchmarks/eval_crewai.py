"""
CrewAI 框架对照评测脚本

CrewAI 是一个基于角色的多智能体编排框架，核心模式是
Agent + Task + Crew 的组合协作。

本脚本复用项目内置的 28 道 Mock GAIA Level 1 样例，
统一使用 DeepSeek-V3 模型，与 PECS 多智能体框架和 ReAct
单 Agent 基线进行公平对比。

依赖安装：
    pip install crewai

CLI 用法：
    python -m benchmarks.eval_crewai                # 评测全部28道
    python -m benchmarks.eval_crewai --num-samples 10   # 评测前10道
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
    LLM_MAX_TOKENS,
)

# ---------- CrewAI 可用性检查 ----------
try:
    from crewai import Agent, Task, Crew, Process

    _CREWAI_AVAILABLE = True
except ImportError:
    _CREWAI_AVAILABLE = False


def _build_llm(api_key: str):
    """
    构建 CrewAI 可用的 LLM 实例，指向 DeepSeek API。

    CrewAI 的 Agent.llm 参数支持两种类型：
    1. crewai.LLM（CrewAI 原生 LLM 包装器）
    2. langchain_openai.ChatOpenAI（LangChain LLM 实例）

    DeepSeek 兼容 OpenAI 接口格式，两种方式都可以直接配置。
    优先使用 crewai.LLM，如果不可用则回退到 LangChain。
    """
    # 方式1：尝试使用 crewai.LLM（CrewAI 原生方式）
    try:
        from crewai import LLM

        return LLM(
            model=f"openai/{DEEPSEEK_MODEL}",
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
    except (ImportError, Exception):
        pass

    # 方式2：使用 langchain_openai.ChatOpenAI（项目已有依赖）
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    )


def _create_agent(llm):
    """
    创建 CrewAI Agent。

    CrewAI 的 Agent 采用角色化设计：
    - role：角色名称（如"研究分析师"）
    - goal：角色目标
    - backstory：角色背景故事（影响 LLM 的行为风格）
    - llm：使用的语言模型
    """
    return Agent(
        role="通用AI助手",
        goal="准确回答用户的问题，给出简洁、正确的最终答案",
        backstory=(
            "你是一个经验丰富的 AI 助手，擅长处理各种知识问答和计算任务。"
            "你会仔细分析问题，运用你的知识给出准确的答案。"
        ),
        llm=llm,
        verbose=False,
        allow_delegation=False,
        max_iter=5,
    )


def _create_task(question: str, agent):
    """
    创建 CrewAI Task。

    Task 定义了具体要完成的工作：
    - description：任务描述（用户问题）
    - expected_output：期望输出格式
    - agent：负责执行此任务的 Agent
    """
    return Task(
        description=(
            f"请回答以下问题：\n\n{question}\n\n"
            f"请直接给出简洁准确的答案。如果涉及计算，请仔细计算。"
            f"最终答案请用 'Final Answer: <答案>' 的格式输出。"
        ),
        expected_output="一个简洁准确的答案，格式为 'Final Answer: <答案>'",
        agent=agent,
    )


def _extract_final_answer(raw_output: str) -> str:
    """
    从 CrewAI 输出中提取最终答案。

    策略（优先级从高到低）：
    1. 找到 "Final Answer:" 标记
    2. 直接使用原始输出（去除多余空白）
    """
    if not raw_output:
        return ""

    raw_output = raw_output.strip()

    # 策略1：找 "Final Answer:" 标记
    if "Final Answer:" in raw_output:
        idx = raw_output.index("Final Answer:")
        answer = raw_output[idx + len("Final Answer:"):].strip()
        # 去掉可能的换行和多余文本
        answer = answer.split("\n")[0].strip()
        return answer

    # 策略2：直接使用原始输出
    return raw_output


def _extract_token_usage(result) -> int:
    """
    从 CrewAI 运行结果中提取 Token 使用量。

    CrewAI 的 CrewOutput 可能包含 token_usage 属性（取决于版本）。
    如果无法获取，则基于输出文本长度进行粗略估算。
    """
    total_tokens = 0

    # 方式1：尝试从 result.token_usage 获取
    try:
        token_usage = getattr(result, "token_usage", None)
        if token_usage:
            if isinstance(token_usage, dict):
                total_tokens = token_usage.get("total_tokens", 0)
            elif isinstance(token_usage, (int, float)):
                total_tokens = int(token_usage)
    except Exception:
        pass

    # 方式2：尝试从 result.usage_metrics 获取
    if total_tokens == 0:
        try:
            usage_metrics = getattr(result, "usage_metrics", None)
            if usage_metrics:
                if isinstance(usage_metrics, dict):
                    total_tokens = usage_metrics.get("total_tokens", 0)
                elif isinstance(usage_metrics, (int, float)):
                    total_tokens = int(usage_metrics)
        except Exception:
            pass

    return total_tokens


def run_crewai_task(question: str, api_key: str = None) -> dict:
    """
    使用 CrewAI 的 Agent + Task + Crew 模式运行单个任务。

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
    if not _CREWAI_AVAILABLE:
        raise ImportError(
            "crewai 库未安装。请运行以下命令安装：\n"
            "    pip install crewai\n"
            "CrewAI 是一个基于角色的多智能体编排框架，"
            "详见 https://github.com/crewAIInc/crewAI"
        )

    api_key = api_key or DEEPSEEK_API_KEY
    if not api_key:
        raise ValueError(
            "未配置 DEEPSEEK_API_KEY。请在 .env 文件中设置，"
            "或通过环境变量传入。"
        )

    logs = []
    logs.append(f"[CrewAI] 开始处理任务: {question}")

    # 构建 LLM、Agent、Task、Crew
    llm = _build_llm(api_key)
    agent = _create_agent(llm)
    task = _create_task(question, agent)

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    )

    # 执行 Crew
    try:
        result = crew.kickoff()
    except Exception as exc:
        logs.append(f"[CrewAI] 执行出错: {type(exc).__name__}: {exc}")
        return {
            "query": question,
            "final_answer": f"[CrewAI 执行失败] {exc}",
            "token_used": 0,
            "token_budget": DEFAULT_TOKEN_BUDGET,
            "logs": logs,
        }

    # 提取原始输出
    raw_output = ""
    if hasattr(result, "raw"):
        raw_output = result.raw or ""
    elif hasattr(result, "result"):
        raw_output = str(result.result or "")
    else:
        raw_output = str(result)

    logs.append(f"[CrewAI] 任务执行完成，输出长度: {len(raw_output)} 字符")

    # 提取最终答案
    final_answer = _extract_final_answer(raw_output)
    if not final_answer:
        final_answer = "未能从 CrewAI 输出中提取答案。"
        logs.append(f"[CrewAI] 未能提取最终答案")
    else:
        logs.append(f"[CrewAI] 最终答案: {final_answer[:80]}...")

    # 提取 Token 使用量
    token_used = _extract_token_usage(result)

    # 如果无法获取精确 Token，基于输出长度估算
    if token_used == 0:
        token_used = max(len(raw_output) // 3, 100)
        logs.append(f"[CrewAI] 无法获取精确 Token，估算: {token_used}")
    else:
        logs.append(f"[CrewAI] Token 消耗: {token_used}")

    return {
        "query": question,
        "final_answer": final_answer,
        "token_used": token_used,
        "token_budget": DEFAULT_TOKEN_BUDGET,
        "logs": logs,
    }


def evaluate_crewai_gaia(num_samples: int = None) -> dict:
    """
    在 GAIA Level 1 上批量评测 CrewAI 框架。

    参数:
        num_samples: 评测样本数（None=全部28道）

    返回:
        与 gaia_eval.evaluate_gaia() 相同格式的结果字典：
        {
            "agent_type": "crewai",
            "total_samples": int,
            "correct_count": int,
            "accuracy": float,
            "total_tokens": int,
            "avg_tokens_per_task": int,
            "details": list,
        }
    """
    if not _CREWAI_AVAILABLE:
        raise ImportError(
            "crewai 库未安装。请运行以下命令安装：\n"
            "    pip install crewai"
        )

    samples = GAIA_L1_SAMPLES[:num_samples] if num_samples else GAIA_L1_SAMPLES

    results = []
    correct_count = 0
    total_tokens = 0

    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        ground_truth = sample["answer"]

        print(f"[{i+1}/{len(samples)}] CrewAI 评估任务 {task_id}: {question}")

        state = run_crewai_task(question)

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
        "agent_type": "crewai",
        "total_samples": len(samples),
        "correct_count": correct_count,
        "accuracy": round(accuracy, 4),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(avg_tokens),
        "details": results,
    }

    save_results(eval_result, "gaia_crewai.json")

    return eval_result


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(
        description="CrewAI 框架 GAIA 评测脚本"
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
    print("  CrewAI 框架 GAIA Level 1 评测")
    print(f"  模型: {DEEPSEEK_MODEL}")
    print(f"  样本数: {num_samples or '全部'}")
    print("=" * 60)

    result = evaluate_crewai_gaia(num_samples)

    print("\n" + "=" * 60)
    print("  评测结果汇总")
    print("=" * 60)
    print(f"  准确率: {result['accuracy']:.2%} ({result['correct_count']}/{result['total_samples']})")
    print(f"  总 Token: {result['total_tokens']}")
    print(f"  平均 Token/任务: {result['avg_tokens_per_task']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
