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

# ========== 按角色配置 temperature ==========
ROLE_TEMPERATURES = {
    "planner":     0.3,   # 规划：需要一点创造性
    "executor":    0.0,   # 执行：生成代码/参数要精确
    "critic":      0.1,   # 评审：评分要稳定
    "synthesizer": 0.5,   # 综合：表达需要灵活性
    "default":     0.1,   # 默认
}

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
        - response_text: LLM 返回的文本
        - token_used: 本次调用消耗的总 Token 数

    包含自动重试机制（3次，指数退避），应对 API 限流和临时网络错误。
    """
    import time as _time

    if not LLM_API_KEY:
        # 无 API Key 时返回模拟响应，保证系统可运行
        return _mock_llm_response(prompt, system_prompt), 100

    max_retries = 3
    last_error = ""

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
                wait = 8 * (2 ** attempt)  # 8s, 16s, 32s（比原 15/30/60 更温和）
                _time.sleep(wait)
                continue
            # 非限流错误或重试耗尽，直接返回失败
            break

    return f"[LLM调用失败] {last_error}", 0


def call_llm_json(prompt: str, system_prompt: str = "", role: str = "default") -> tuple:
    """
    调用 LLM 并解析 JSON 响应

    参数:
        prompt: 用户提示词
        system_prompt: 系统提示词
        role: 调用角色（决定 temperature）

    返回:
        (parsed_dict, token_used)
    """
    response_text, token_used = call_llm(prompt, system_prompt, role)

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
