"""
Critic Agent —— 评审者

职责：
  评估 Executor 每步执行结果的质量，检测错误和不完整信息。
  类比：就像质检员检查工程师的工作成果，不合格就打回重做。

输入：AgentState 中的 results（执行结果）
输出：更新 AgentState 中的 critic_scores（评分列表）

评估维度（三维打分，每维1-5分）：
  - 事实准确性（accuracy）：结果是否基于事实，有没有编造
  - 逻辑一致性（consistency）：结果是否自洽，有没有矛盾
  - 信息完整性（completeness）：是否完整回答了步骤要求

反馈机制：
  - 综合分 >= 4：合格，交给 Synthesizer
  - 综合分 < 4 且重试次数 < 3：不合格，反馈给 Executor 重试
  - 重试次数 >= 3：强制通过（不能无限重试）

Token 预算联动：
  当预算消耗 > 70% 时，跳过低风险步骤的详细检查（快速验证模式）
"""
from agents.llm_utils import call_llm_json
from graph.token_budget import get_budget_policy, record_token_usage

# Critic 的系统提示词
CRITIC_SYSTEM_PROMPT = """你是一个严格的质量评审专家（Critic），负责评估任务执行结果的质量。

评估维度（每项1-5分）：
1. accuracy（事实准确性）：结果是否基于事实，有没有编造信息
2. consistency（逻辑一致性）：结果是否自洽，有没有前后矛盾
3. completeness（信息完整性）：是否完整回答了步骤的要求

评分标准：
- 5分：完美，无任何问题
- 4分：良好，有微小瑕疵但不影响使用
- 3分：及格，有明显问题但不严重
- 2分：不及格，存在错误或遗漏
- 1分：完全错误或无关

输出格式（严格JSON）：
```json
{
    "accuracy": 4,
    "consistency": 4,
    "completeness": 3,
    "overall": 3.7,
    "feedback": "结果基本正确，但缺少具体日期信息，建议补充。",
    "step_id": 1
}
```

注意：overall = (accuracy + consistency + completeness) / 3，保留一位小数。
"""


def critic_node(state: dict) -> dict:
    """
    Critic 节点函数

    评估最近一步的执行结果：
    1. 从 results 中取出最新结果
    2. 调用 LLM 进行三维评分
    3. 记录评分到 critic_scores
    4. 如果不合格，生成重试反馈
    """
    results = state.get("results", [])
    critic_scores = state.get("critic_scores", [])
    logs = state.get("logs", [])

    # 如果没有结果，直接返回
    if not results:
        logs.append("[Critic] 无结果可评估")
        return {"critic_scores": critic_scores, "logs": logs}

    # 获取最新结果
    latest_result = results[-1]
    step_id = latest_result.get("step_id", len(results))

    # 检查是否已经评估过这个步骤的这次执行
    # 统计该 step_id 的结果数和评分数，如果评分数 < 结果数说明有新结果需要评估
    result_count_for_step = sum(1 for r in results if r.get("step_id") == step_id)
    score_count_for_step = sum(1 for s in critic_scores if s.get("step_id") == step_id)
    if score_count_for_step >= result_count_for_step:
        logs.append(f"[Critic] 步骤 {step_id} 已评估过（{score_count_for_step}/{result_count_for_step}），跳过")
        return {"critic_scores": critic_scores, "logs": logs}

    # Token 预算感知：预算紧张时快速验证
    policy = get_budget_policy(state)
    budget_ratio = policy["usage_ratio"]
    fast_mode = policy["fast_critic"]

    if fast_mode:
        # 快速模式：不调 LLM，用简单规则判断
        score = _fast_evaluate(latest_result)
        token_consumed = 0
        logs.append(f"[Critic] 快速验证模式 (预算 {budget_ratio:.0%})，步骤 {step_id} 评分: {score['overall']}")
    else:
        # 先尝试规则验证（不消耗 Token）
        rule_score = _rule_evaluate(latest_result)
        if rule_score:
            score = rule_score
            token_consumed = 0
            logs.append(f"[Critic] 规则验证步骤 {step_id}: 评分 {score['overall']} ({score.get('feedback', '')})")
        else:
            # 规则无法判断，调 LLM 评分
            prompt = f"""
步骤描述: {latest_result.get('description', '')}
执行结果: {latest_result.get('result', '')}
步骤ID: {step_id}

请评估以上执行结果的质量。
"""
            try:
                score, token_consumed = call_llm_json(prompt, CRITIC_SYSTEM_PROMPT, role="critic")
            except Exception as exc:
                score = _fast_evaluate(latest_result)
                token_consumed = 0
                logs.append(f"[Critic] LLM评分解析失败，回退快速验证: {type(exc).__name__}")
            score["step_id"] = step_id

            # 确保 overall 计算正确
            accuracy = score.get("accuracy", 3)
            consistency = score.get("consistency", 3)
            completeness = score.get("completeness", 3)
            score["overall"] = round((accuracy + consistency + completeness) / 3, 1)

            logs.append(f"[Critic] LLM评分步骤 {step_id}: 准确={accuracy} 一致={consistency} 完整={completeness} 综合={score['overall']}")

    score.setdefault("step_id", step_id)
    critic_scores.append(score)

    # 如果不合格，生成重试反馈
    retry_feedback = ""
    if score["overall"] < 4.0:
        retry_feedback = score.get("feedback", "结果质量不达标，请重新执行。")
        logs.append(f"[Critic] 步骤 {step_id} 不合格，反馈: {retry_feedback}")
    else:
        logs.append(f"[Critic] 步骤 {step_id} 合格，进入下一环节")

    token_used, role_token_used, budget_events = record_token_usage(state, "critic", token_consumed)

    return {
        "critic_scores": critic_scores,
        "retry_feedback": retry_feedback,
        "token_used": token_used,
        "role_token_used": role_token_used,
        "budget_events": budget_events,
        "logs": logs,
    }


def _fast_evaluate(result: dict) -> dict:
    """
    快速评估模式（预算紧张时不调 LLM）

    用简单规则判断结果质量：
    - 如果结果包含"错误"或"失败"，给低分
    - 如果结果太短（<50字符），给中分
    - 否则给及格分
    """
    text = result.get("result", "")
    success = result.get("success", False)

    if not success or "错误" in text or "失败" in text:
        return {
            "accuracy": 2, "consistency": 2, "completeness": 2,
            "overall": 2.0, "feedback": "执行结果包含错误，请重试。"
        }

    if len(text) < 50:
        return {
            "accuracy": 3, "consistency": 3, "completeness": 2,
            "overall": 2.7, "feedback": "结果信息过少，请补充更多细节。"
        }

    return {
        "accuracy": 4, "consistency": 4, "completeness": 4,
        "overall": 4.0, "feedback": "快速验证通过。"
    }


def _rule_evaluate(result: dict) -> dict:
    """
    规则验证模式（不消耗 Token）

    对特定类型的执行结果用规则验证质量：
    - Python 执行结果：检查是否有输出、是否包含错误
    - 搜索结果：检查是否返回了有效信息
    - 通用：检查结果长度和错误标记

    返回 None 表示规则无法判断，需要回退到 LLM 评分
    """
    text = result.get("result", "")
    action = result.get("action", "")
    success = result.get("success", False)
    description = result.get("description", "")

    # 执行失败的直接给低分
    if not success or "错误" in text or "执行错误" in text:
        return {
            "accuracy": 2, "consistency": 2, "completeness": 2,
            "overall": 2.0, "feedback": f"执行失败，请检查参数和代码后重试。",
            "step_id": result.get("step_id", 0),
        }

    # Python 执行结果验证
    if action == "python":
        # 检查是否有实际输出
        if not text.strip() or text.strip() == "输出:" or text.strip() == "无输出":
            return {
                "accuracy": 2, "consistency": 2, "completeness": 2,
                "overall": 2.0, "feedback": "Python 代码没有产生输出，请添加 print 语句。",
                "step_id": result.get("step_id", 0),
            }
        # 检查是否包含 traceback
        if "Traceback" in text or "SyntaxError" in text or "NameError" in text:
            return {
                "accuracy": 2, "consistency": 2, "completeness": 2,
                "overall": 2.0, "feedback": "Python 代码执行报错，请修复后重试。",
                "step_id": result.get("step_id", 0),
            }

        # === 计算结果完整性检查 ===
        desc_lower = description.lower() if description else ""
        result_lower = text.lower()

        # 如果步骤要求"判断"，但结果没有明确的判断结论
        if ("判断" in desc_lower or "是否" in desc_lower or 
            "是不是" in desc_lower or "对吗" in desc_lower):
            has_conclusion = any(kw in result_lower for kw in [
                "true", "false", "是", "否", "不是", "整数", "偶数", "奇数",
                "大于", "小于", "等于", "能", "不能"
            ])
            if not has_conclusion:
                return {
                    "accuracy": 3, "consistency": 3, "completeness": 2,
                    "overall": 2.7, "feedback": "结果缺少明确的判断结论，请添加 True/False 或 是/否。",
                    "step_id": result.get("step_id", 0),
                }

        # 如果步骤要求"计算"或"求"，但结果中没有数字
        if ("计算" in desc_lower or "求" in desc_lower or 
            "多少" in desc_lower or "等于" in desc_lower):
            import re
            nums = re.findall(r'\d+', text)
            if not nums:
                return {
                    "accuracy": 2, "consistency": 2, "completeness": 2,
                    "overall": 2.0, "feedback": "计算结果中未找到有效数字，请检查代码输出。",
                    "step_id": result.get("step_id", 0),
                }

        # 输出完整且正确，给高分
        return {
            "accuracy": 5, "consistency": 5, "completeness": 5,
            "overall": 5.0, "feedback": "Python 执行成功，结果完整且正确。",
            "step_id": result.get("step_id", 0),
        }

    # 搜索结果验证
    if action == "search":
        # 检查是否返回了模拟数据
        if "[模拟搜索] 未找到" in text:
            return {
                "accuracy": 2, "consistency": 3, "completeness": 2,
                "overall": 2.3, "feedback": "搜索未返回有效结果，建议更换关键词重试。",
                "step_id": result.get("step_id", 0),
            }
        # 检查搜索结果长度
        if len(text) < 30:
            return {
                "accuracy": 3, "consistency": 3, "completeness": 2,
                "overall": 2.7, "feedback": "搜索结果信息过少，建议细化搜索关键词。",
                "step_id": result.get("step_id", 0),
            }
        # 搜索结果正常
        return {
            "accuracy": 4, "consistency": 4, "completeness": 4,
            "overall": 4.0, "feedback": "搜索结果有效。",
            "step_id": result.get("step_id", 0),
        }

    if action == "webshop":
        if "NO_MATCH" in text or "错误" in text:
            return {
                "accuracy": 2, "consistency": 2, "completeness": 2,
                "overall": 2.0, "feedback": "未找到满足约束的商品，需要调整查询或候选集。",
                "step_id": result.get("step_id", 0),
            }
        # 真实 WebShop 环境：返回 HTML 非非 SELECTED，改用 reward 信号判定
        # webshop_interact 的输出含 "奖励=X.XXX"，reward≥0.5 视为成功
        import re as _re_ws
        m = _re_ws.search(r"奖励\s*=\s*([0-9]*\.?[0-9]+)", text)
        if m:
            reward_val = float(m.group(1))
            if reward_val >= 0.5:
                return {
                    "accuracy": 5, "consistency": 5, "completeness": 5,
                    "overall": 5.0, "feedback": f"真实 WebShop 环境 reward={reward_val:.3f}≥0.5，购买成功。",
                    "step_id": result.get("step_id", 0),
                }
            if reward_val > 0:
                # reward>0 但 <0.5：部分匹配，可接受不再重试（避免无效 4 轮重试白烧 Token）
                return {
                    "accuracy": 4, "consistency": 4, "completeness": 3,
                    "overall": 3.7, "feedback": f"真实 WebShop reward={reward_val:.3f}（部分匹配），可接受不再重试。",
                    "step_id": result.get("step_id", 0),
                }
            # reward==0：真实环境未买到，给低分允许 1 次重试（不再 4 轮）
            return {
                "accuracy": 2, "consistency": 3, "completeness": 2,
                "overall": 2.3, "feedback": "真实 WebShop reward=0.0，未完成购买，可重试 1 次。",
                "step_id": result.get("step_id", 0),
            }
        # 本地 mock 适配器：仍走 SELECTED 行判定
        if "SELECTED:" in text:
            return {
                "accuracy": 5, "consistency": 5, "completeness": 5,
                "overall": 5.0, "feedback": "WebShop商品选择满足约束。",
                "step_id": result.get("step_id", 0),
            }
        return {
            "accuracy": 3, "consistency": 3, "completeness": 2,
            "overall": 2.7, "feedback": "WebShop结果缺少明确SELECTED输出。",
            "step_id": result.get("step_id", 0),
        }

    # 其他类型：规则无法判断，返回 None 让 LLM 评分
    return None
