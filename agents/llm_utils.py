"""
LLM 调用封装

统一的 LLM 调用接口，封装 LLM API（兼容 OpenAI 格式，支持 DeepSeek/GLM/Qwen 等）。
所有 Agent 角色都通过这个模块调用 LLM，方便统一管理 Token 消耗。

按角色区分 temperature：
  不同角色对 LLM 输出的确定性要求不同：
  - Executor 生成代码 → temperature=0.0，要求精确无随机性
  - Critic 评分 → temperature=0.1，评分要稳定一致
  - Planner 规划 → temperature=0.3，需要一点创造性来拆分任务
  - Synthesizer 综合 → temperature=0.5，表达需要灵活性
"""
import json
import os
from typing import Optional
from langchain_openai import ChatOpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_TOKENS

# ========== LLM 失败信号（统一约定）==========
# call_llm 在重试耗尽后返回的文本统一以此为前缀。
# 该前缀本就是项目既有约定（agents/heuristics.py 的 _FAIL_MARKERS、
# agents/synthesizer.py 的 startswith 判定、run_resumable.py 的失败特征词
# 均已按此前缀识别失败）；此处将其正式化为模块级常量，供下游机读，
# 避免"文字约定"散落在各处、易漏。
LLM_FAILURE_PREFIX = "[LLM调用失败]"


class LLMInvocationError(RuntimeError):
    """LLM 调用失败（重试耗尽后仍未成功）。

    为兼容既有调用方，call_llm 仍返回 (失败文本, 0)；但需要结构化输出的下游
    （call_llm_json → Planner/Critic）会在检测到失败前缀时显式抛出本异常，
    从而走各自的 fallback 分支，而不是拿一段无法解析的文本后抛出
    JSONDecodeError 这类**误导性**异常、把真正的依赖故障掩盖掉。
    """


def is_llm_failure(text) -> bool:
    """判断 call_llm 返回的文本是否为失败信号。"""
    return isinstance(text, str) and text.startswith(LLM_FAILURE_PREFIX)

# ========== 按角色配置 temperature ==========
ROLE_TEMPERATURES = {
    "planner":     0.3,   # 规划：需要一点创造性
    "executor":    0.0,   # 执行：生成代码/参数要精确
    "critic":      0.1,   # 评审：评分要稳定
    "synthesizer": 0.5,   # 综合：表达需要灵活性
    "default":     0.1,   # 默认
}


def set_deterministic_mode() -> None:
    """强制所有角色 temperature=0，消除 Planner/Synthesizer 的随机性，使评测结果可复现。

    须在首次 LLM 调用前调用（评测入口 main() 最开头调用即可）。
    ReAct 基线复用同一模块，故同样受益。开启后宣称的准确率数字可 defense、
    可复现，避免"非确定性导致重跑分数漂移"引发质疑。

    等效环境变量：PEC_DETERMINISTIC=1（模块导入时即生效）。
    """
    for _role in ROLE_TEMPERATURES:
        ROLE_TEMPERATURES[_role] = 0.0


# 评测/基准跑批时通过环境变量预设确定性模式（run_gaia_official.py 也会显式调用）
if os.environ.get("PEC_DETERMINISTIC") == "1":
    set_deterministic_mode()


# 按角色缓存 LLM 实例（每个角色一个独立实例，temperature 不同）
_llm_instances: dict = {}


def get_llm(role: str = "default") -> ChatOpenAI:
    """
    获取指定角色的 LLM 实例

    每个角色有独立的 temperature 配置：
    - planner: 0.3（规划需要创造性）
    - executor: 0.0（代码生成要精确）
    - critic: 0.1（评分要稳定）
    - synthesizer: 0.5（综合表达要灵活）

    参数:
        role: 角色名称（planner / executor / critic / synthesizer / default）
    """
    temp = ROLE_TEMPERATURES.get(role, ROLE_TEMPERATURES["default"])

    if role not in _llm_instances:
        _llm_instances[role] = ChatOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            temperature=temp,
            max_tokens=LLM_MAX_TOKENS,
            # 客户端级超时：让底层 httpx socket 读有上限，
            # 从而使 api.py 的 asyncio.wait_for 能真正中断被阻塞的 LLM 调用
            # （否则同步 HTTP 读会卡住线程，链路级超时失效）。默认 60s，可经 env 覆盖。
            timeout=float(os.environ.get("LLM_CALL_TIMEOUT", "60")),
            max_retries=2,
        )
    return _llm_instances[role]


def call_llm(prompt: str, system_prompt: str = "", role: str = "default") -> tuple:
    """
    调用 LLM 并返回结果和 Token 消耗

    参数:
        prompt: 用户提示词
        system_prompt: 系统提示词（角色设定）
        role: 调用角色（决定 temperature）

    返回:
        (response_text, token_used)
        - response_text: LLM 返回的文本；**调用失败时为 f"{LLM_FAILURE_PREFIX} <原因>"**，
          调用方可经 is_llm_failure(response_text) 判定，切勿把失败文本当正常答案使用
        - token_used: 本次调用消耗的总 Token 数（失败时为 0）

    包含自动重试机制（3次，指数退避），应对 API 限流和临时网络错误。
    """
    import time as _time

    if not LLM_API_KEY:
        # 无 API Key 时返回模拟响应，保证系统可运行
        return _mock_llm_response(prompt, system_prompt), 100

    max_retries = 3
    last_error = ""

    # 整体墙钟预算：单次 call_llm（含全部重试）的总耗时上限，LLM_CALL_DEADLINE 秒。
    #
    # 为什么必须有：get_llm() 里的 timeout=60 只是「单次 HTTP 请求」的上限，而
    #   一次 llm.invoke() 内部还有 openai SDK 自己的 max_retries=2（最多 3 次请求）
    #   ⇒ 单次 invoke 最长 3×60=180s；外层再重试 3 次 + 8/16/32s 退避
    #   ⇒ 最坏约 9 分钟，**且没有任何整体上界**。
    # 实测后果：网关「只连不发」时（faulthandler 栈定位于此 → ssl.py read），
    #   单道 GAIA 题空转 15 分钟，53 题串行评测被彻底拖死且无提示。
    #
    # 设为 0 可关闭（保留旧行为）。注意它只能阻止「发起新尝试」，无法中断
    # 已在飞行中的那一次 —— 真正的硬边界由调用方提供：
    #   benchmarks/gaia_official.py 的 _run_with_deadline()（守护线程 + join）。
    # 两者构成纵深防御：这里收敛耗时，那里保证评测不卡死。
    deadline_sec = float(os.getenv("LLM_CALL_DEADLINE", "120"))
    deadline = (_time.time() + deadline_sec) if deadline_sec > 0 else None

    # 限流保护：保证两次 API 调用之间至少间隔 _GAP 秒，避免触发 RPM/模型容量限制。
    # 通过环境变量 LLM_MIN_GAP 可调整（默认 3s，足以规避绝大多数基础 RPM 限制，
    # 又不至于像之前的 20s 那样在换用不限流 API 时严重拖慢评测）。
    # 设计为「仅在间隔不足时才 sleep」，间隔已满足则零等待。
    _GAP = float(os.environ.get("LLM_MIN_GAP", "3.0"))
    if _GAP > 0:
        _now = _time.time()
        _since = _now - getattr(call_llm, "_last_ts", 0)
        if _since < _GAP:
            _time.sleep(_GAP - _since)
        call_llm._last_ts = _time.time()

    for attempt in range(max_retries):
        # 到点后不再发起新的尝试（已飞行中的那次由客户端 timeout 兜底）
        if deadline is not None and attempt > 0 and _time.time() >= deadline:
            last_error = f"deadline exceeded: {deadline_sec:.0f}s"
            break
        try:
            llm = get_llm(role)
            messages = []
            if system_prompt:
                messages.append(("system", system_prompt))
            messages.append(("human", prompt))

            response = llm.invoke(messages)

            # 提取 Token 使用量
            token_used = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                token_used = response.usage_metadata.get("total_tokens", 0)
            elif hasattr(response, "response_metadata"):
                meta = response.response_metadata
                if "token_usage" in meta:
                    token_used = meta["token_usage"].get("total_tokens", 0)

            # 估算 Token（如果 API 没返回）
            if token_used == 0:
                token_used = (len(system_prompt) + len(prompt) + len(response.content)) // 3

            return response.content, token_used

        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)}"
            error_str = str(e).lower()
            # 限流/速率限制/服务暂不可用 → 等待后重试
            is_rate_limit = any(kw in error_str for kw in [
                "rate", "429", "quota", "too many", "throttl", "limit",
                "timeout", "connection", "temporarily", "unavailable"
            ])
            if is_rate_limit and attempt < max_retries - 1:
                import random as _random

                base = 8 * (2 ** attempt)  # 8s, 16s, 32s（比原 15/30/60 更温和）
                # 等额抖动（equal jitter）：实际等待 ∈ [base/2, base]。
                # 为什么必须抖动：固定退避会让所有被限流的请求**在同一时刻**重试，
                # 形成同步重试风暴（等于自我 DDoS），限流反而更难恢复。
                # 为什么不用全抖动 random(0, base)：它可能取到接近 0 的值，
                # 退避形同虚设；保留 base/2 的下限才真正拉开重试间隔。
                wait = base / 2 + _random.uniform(0, base / 2)
                # 退避会越过 deadline → 直接放弃，不在明知无用的等待上浪费墙钟
                if deadline is not None and _time.time() + wait >= deadline:
                    last_error = f"{last_error} (deadline {deadline_sec:.0f}s)"
                    break
                _time.sleep(wait)
                continue
            # 非限流错误或重试耗尽，直接返回失败
            break

    return f"{LLM_FAILURE_PREFIX} {last_error}", 0


def call_llm_json(prompt: str, system_prompt: str = "", role: str = "default") -> tuple:
    """
    调用 LLM 并解析 JSON 响应

    参数:
        prompt: 用户提示词
        system_prompt: 系统提示词
        role: 调用角色（决定 temperature）

    返回:
        (parsed_dict, token_used)

    抛出:
        LLMInvocationError: LLM 调用本身失败（重试耗尽）。显式抛出而非返回
            失败文本，避免下游误把失败文本当"格式错误的 JSON"处理。
    """
    response_text, token_used = call_llm(prompt, system_prompt, role)

    # 依赖故障（key 失效 / 服务不可达 / 限流耗尽）→ 显式抛出，
    # 让 Planner/Critic 的 fallback 分支按"LLM 不可用"处理，
    # 而不是在一段非 JSON 文本上抛 JSONDecodeError 掩盖真实原因。
    if is_llm_failure(response_text):
        raise LLMInvocationError(response_text)

    try:
        # 尝试直接解析
        result = json.loads(response_text)
        return result, token_used
    except json.JSONDecodeError:
        # 尝试从 Markdown 代码块中提取 JSON
        if "```json" in response_text:
            start = response_text.index("```json") + 7
            end = response_text.index("```", start)
            json_str = response_text[start:end].strip()
            return json.loads(json_str), token_used
        elif "```" in response_text:
            start = response_text.index("```") + 3
            end = response_text.index("```", start)
            json_str = response_text[start:end].strip()
            return json.loads(json_str), token_used
        else:
            # 尝试找到第一个 { 和最后一个 }
            first = response_text.find("{")
            last = response_text.rfind("}")
            if first != -1 and last != -1:
                return json.loads(response_text[first:last + 1]), token_used
            raise


def _mock_llm_response(prompt: str, system_prompt: str) -> str:
    """
    模拟 LLM 响应（无 API Key 时的后备方案）

    根据不同的 system_prompt 返回不同的模拟结果，
    让系统在没有 API 的情况下也能跑通流程。
    """
    import re

    if "Planner" in system_prompt or "规划" in system_prompt:
        task_match = re.search(r"用户任务:\s*(.+)", prompt)
        task = task_match.group(1).strip() if task_match else "未知任务"

        return f"""```json
{{
    "steps": [
        {{"id": 1, "action": "search", "description": "搜索与问题相关的信息: {task[:30]}", "args": {{"query": "{task[:30]}"}}, "status": "pending", "result": null, "retry_count": 0}},
        {{"id": 2, "action": "python", "description": "整理搜索结果并生成答案", "args": {{"code": "result = '根据搜索结果，关于{task[:20]}的分析已完成'\\nprint(result)"}}, "status": "pending", "result": null, "retry_count": 0}}
    ]
}}
```"""
    elif "Critic" in system_prompt or "评审" in system_prompt:
        return """```json
{
    "accuracy": 4,
    "consistency": 4,
    "completeness": 4,
    "overall": 4.0,
    "feedback": "结果质量良好，可以进入下一环节。",
    "step_id": 1
}
```"""
    elif "Synthesizer" in system_prompt or "综合" in system_prompt:
        q_match = re.search(r"用户原始问题:\s*(.+)", prompt)
        question = q_match.group(1).strip() if q_match else "用户问题"
        return f"根据各步骤的执行结果综合分析，针对问题「{question}」，系统通过搜索和数据处理工具获取了相关信息并完成了分析。最终结论：任务已成功完成，所有步骤执行正常，结果质量通过评审。"
    else:
        return f"[模拟LLM响应] 收到提示词: {prompt[:100]}..."
