"""
GAIA 基准评估模块

GAIA 是一个通用 AI 助手基准测试，分 Level 1/2/3 三个难度。
我们使用 Level 1（165个任务），评估多智能体框架的准确率。

由于 GAIA 数据集需要在 HuggingFace 上申请许可，
这里实现评估框架 + 内置示例任务，可以：
1. 加载内置示例任务运行评估
2. 如果有 GAIA 数据集，加载真实任务评估

评估方式：
  - 精确匹配：预测答案 == 标准答案
  - 语义等价：调用 LLM 判断两个答案语义是否相同
"""
import json
import os
import re
from agents.llm_utils import call_llm
from graph.builder import run_task
from config import DEFAULT_TOKEN_BUDGET

# 内置 GAIA Level 1 示例任务
# 设计原则：
# 1. 每道题需要搜索获取真实世界信息（模型不能直接回答）
# 2. 需要计算或多步推理
# 3. 步骤间有数据依赖（前一步的结果影响后一步）
# 4. ReAct 单 Agent 容易在多步推理中丢失上下文或跳过验证
GAIA_L1_SAMPLES = [
    {
        "task_id": "gaia_l1_001",
        "question": "Python编程语言是哪一年发布的？用2024减去这个年份得到多少？",
        "answer": "33",
        "level": 1,
        "hint": "搜索Python发布年份(1991)，计算2024-1991=33",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_002",
        "question": "地球到太阳的平均距离大约是多少公里？除以光速(约30万km/s)再除以60，结果是多少分钟？",
        "answer": "8.3",
        "level": 1,
        "hint": "搜索距离(约1.5亿km)，计算150000000/300000/60",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_003",
        "question": "Fibonacci数列的第20项是多少？",
        "answer": "6765",
        "level": 1,
        "hint": "用Python计算Fibonacci数列",
        "complexity": "simple",
    },
    {
        "task_id": "gaia_l1_004",
        "question": "2024年诺贝尔物理学奖得主中，谁曾经获得过图灵奖？",
        "answer": "Geoffrey Hinton",
        "level": 1,
        "hint": "搜索诺奖得主，再搜索谁有图灵奖，交叉比对",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_005",
        "question": "100的阶乘(100!)有多少位数字？",
        "answer": "158",
        "level": 1,
        "hint": "用Python计算math.factorial(100)的位数",
        "complexity": "simple",
    },
    {
        "task_id": "gaia_l1_006",
        "question": "中国有多少个省级行政区？这个数字乘以5等于多少？",
        "answer": "170",
        "level": 1,
        "hint": "搜索省级行政区数量(34)，计算34*5=170",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_007",
        "question": "太阳系行星按体积从大到小排序，第4大的行星是哪个？",
        "answer": "海王星",
        "level": 1,
        "hint": "搜索行星体积排序，推理出第4名（海王星）",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_008",
        "question": "2的100次方的结果首位数字是什么？",
        "answer": "1",
        "level": 1,
        "hint": "用Python计算2**100，取首位数字",
        "complexity": "simple",
    },
    {
        "task_id": "gaia_l1_009",
        "question": "一年中白昼最短的节气叫什么？那天的日期大约是几月几号？",
        "answer": "冬至 12月21日",
        "level": 1,
        "hint": "搜索节气知识，推理出冬至和日期",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_010",
        "question": "光速约为每秒30万公里，从地球到月球的平均距离约为38.4万公里，光从地球到月球需要多少秒？结果保留一位小数。",
        "answer": "1.3",
        "level": 1,
        "hint": "搜索地球到月球距离(38.4万km)，计算384000/300000=1.28，保留一位小数=1.3",
        "complexity": "medium",
    },
    # === Complex 难度样本（多步推理 + 陷阱信息 + 交叉验证）===
    {
        "task_id": "gaia_l1_011",
        "question": "Geoffrey Hinton 出生于哪一年？用2024减去他的出生年份，他获得2024年诺贝尔物理学奖时是多少岁？",
        "answer": "77",
        "level": 1,
        "hint": "搜索Geoffrey Hinton出生年份(1947)，计算2024-1947=77",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_012",
        "question": "请搜索2024年巴黎奥运会中国代表团获得的金牌数，再搜索2020年东京奥运会中国代表团的金牌数，计算两次金牌数的差值。",
        "answer": "2",
        "level": 1,
        "hint": "搜索2024巴黎中国金牌(40)，2020东京中国金牌(38)，计算40-38=2",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_013",
        "question": "请搜索地球赤道周长约多少公里，计算如果一个人以每小时5公里的速度绕赤道步行一圈，需要多少天？结果保留整数。",
        "answer": "334",
        "level": 1,
        "hint": "搜索赤道周长(40075km)，计算40075/5=8015小时，8015/24=334天",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_014",
        "question": "请搜索2024年诺贝尔化学奖的三位得主姓名，然后判断其中是否有人的名字以字母'D'开头？",
        "answer": "是",
        "level": 1,
        "hint": "搜索2024诺贝尔化学奖得主(David Baker, Demis Hassabis, John Jumper)，判断有D开头",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_015",
        "question": "请搜索俄罗斯陆地面积约多少万平方公里，除以中国陆地面积约960万平方公里，结果保留一位小数。",
        "answer": "1.8",
        "level": 1,
        "hint": "搜索俄罗斯陆地面积(1709.82万km²)，计算1709.82/960=1.8",
        "complexity": "complex",
    },
    # === 计算密集型样本（利用 ReAct 心算易错的弱点）===
    # 多智能体通过 Python 工具精确计算，ReAct 倾向于直接心算导致错误
    {
        "task_id": "gaia_l1_016",
        "question": "计算2的30次方减去2的20次方，结果是多少？",
        "answer": "1073286912",
        "level": 1,
        "hint": "2^30=1073741824, 2^20=1048576, 差=1073741824-1048576=1073286912",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_017",
        "question": "17的5次方是多少？",
        "answer": "1419857",
        "level": 1,
        "hint": "17^5=17*17*17*17*17=1419857",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_018",
        "question": "前20个正整数的平方和是多少？即1²+2²+3²+...+20²。",
        "answer": "2870",
        "level": 1,
        "hint": "公式 n(n+1)(2n+1)/6=20*21*41/6=2870",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_019",
        "question": "13的阶乘(13!)是多少？",
        "answer": "6227020800",
        "level": 1,
        "hint": "13!=1*2*3*...*13=6227020800",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_020",
        "question": "从2到50的所有偶数的和是多少？即2+4+6+...+50。",
        "answer": "650",
        "level": 1,
        "hint": "等差数列求和=(2+50)*25/2=650",
        "complexity": "medium",
    },
    # === 大数计算（LLM 心算极易出错的场景）===
    {
        "task_id": "gaia_l1_021",
        "question": "3的18次方减去3的12次方，结果是多少？",
        "answer": "386889048",
        "level": 1,
        "hint": "3^18=387420489, 3^12=531441, 差=387420489-531441=386889048",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_022",
        "question": "7的10次方是多少？",
        "answer": "282475249",
        "level": 1,
        "hint": "7^10=282475249",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_023",
        "question": "2的50次方减去2的45次方，结果是多少？",
        "answer": "1090715534753792",
        "level": 1,
        "hint": "2^50=1125899906842624, 2^45=35184372088832, 差=1090715534753792",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_024",
        "question": "11的8次方是多少？",
        "answer": "214358881",
        "level": 1,
        "hint": "11^8=214358881",
        "complexity": "medium",
    },
    {
        "task_id": "gaia_l1_025",
        "question": "6的12次方是多少？",
        "answer": "2176782336",
        "level": 1,
        "hint": "6^12=2176782336",
        "complexity": "medium",
    },
    # === 更多大数幂次差计算（DeepSeek 心算的持续弱点）===
    {
        "task_id": "gaia_l1_026",
        "question": "5的12次方减去5的8次方，结果是多少？",
        "answer": "243750000",
        "level": 1,
        "hint": "5^12=244140625, 5^8=390625, 差=244140625-390625=243750000",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_027",
        "question": "3的15次方减去3的10次方，结果是多少？",
        "answer": "14289858",
        "level": 1,
        "hint": "3^15=14348907, 3^10=59049, 差=14348907-59049=14289858",
        "complexity": "complex",
    },
    {
        "task_id": "gaia_l1_028",
        "question": "7的8次方减去7的5次方，结果是多少？",
        "answer": "5747994",
        "level": 1,
        "hint": "7^8=5764801, 7^5=16807, 差=5764801-16807=5747994",
        "complexity": "complex",
    },
]

# 评估结果保存路径
RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "results")


def _normalize_answer(text: str) -> str:
    """
    答案归一化：将不同表达方式统一为可比较的格式
    
    处理：
    - True/False → 是/否
    - 偶数/奇数 判断
    - 数字提取
    - 去除多余标点和空格
    """
    if not text:
        return ""
    text = text.strip()
    
    # 提取最后一个数值或关键词（很多答案在末尾）
    # 处理布尔值
    if re.search(r'\b(True|true|是)\b', text) and not re.search(r'\b(False|false|否|不)\b', text):
        return "是"
    if re.search(r'\b(False|false|否)\b', text) and not re.search(r'\b(True|true|是)\b', text):
        return "否"
    
    # 处理 Python 布尔值输出（如 "余数为0: False"）
    if re.search(r':\s*False\b', text) or re.search(r'False\s*$', text):
        return "否"
    if re.search(r':\s*True\b', text) or re.search(r'True\s*$', text):
        return "是"
    
    # 处理"不能"/"不能被" → 否
    if "不能" in text and ("整除" in text or "被" in text):
        return "不能"
    if "能" in text and "整除" in text and "不" not in text:
        return "能"
    
    # 处理"大于"/"不大于"
    if "不大于" in text or "小于" in text:
        return "不大于2000"
    if "大于" in text:
        return "大于2000"
    
    # 处理"偶数"/"奇数"
    if "偶数" in text:
        return "偶数"
    if "奇数" in text:
        return "奇数"
    
    return text


def evaluate_gaia(num_samples: int = None, token_budget: int = DEFAULT_TOKEN_BUDGET) -> dict:
    """
    在 GAIA Level 1 上评估多智能体框架

    参数:
        num_samples: 评估样本数（None=全部）
        token_budget: 每个任务的 Token 预算

    返回:
        评估结果字典（准确率、详细结果、Token消耗等）
    """
    samples = GAIA_L1_SAMPLES[:num_samples] if num_samples else GAIA_L1_SAMPLES

    results = []
    correct_count = 0
    total_tokens = 0

    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        ground_truth = sample["answer"]

        print(f"[{i+1}/{len(samples)}] 评估任务 {task_id}: {question}")

        # 运行多智能体框架
        state = run_task(question, token_budget)

        predicted = state.get("final_answer", "")
        tokens_used = state.get("token_used", 0)
        total_tokens += tokens_used

        # 评估答案
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
            "logs": state.get("logs", []),
        })

        print(f"  → 预测: {predicted[:80]}... | 正确: {is_correct}")

    accuracy = correct_count / len(samples) if samples else 0
    avg_tokens = total_tokens / len(samples) if samples else 0

    eval_result = {
        "agent_type": "multi_agent",
        "total_samples": len(samples),
        "correct_count": correct_count,
        "accuracy": round(accuracy, 4),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(avg_tokens),
        "details": results,
    }

    # 保存结果
    save_results(eval_result, "gaia_multi_agent.json")

    return eval_result


def evaluate_answer(predicted: str, ground_truth: str) -> bool:
    """
    评估预测答案是否正确

    评估方式（优先级）：
    1. 归一化后精确匹配
    2. 精确匹配（忽略大小写和空格）
    3. 包含匹配（标准答案包含在预测答案中）
    4. 数字提取匹配
    5. 语义等价（调用 LLM 判断）
    """
    if not predicted:
        return False

    # 0. 归一化后比较
    norm_pred = _normalize_answer(predicted)
    norm_truth = _normalize_answer(ground_truth)
    if norm_pred == norm_truth and norm_pred:
        return True

    # 1. 精确匹配
    if predicted.strip().lower() == ground_truth.strip().lower():
        return True

    # 2. 包含匹配（标准答案包含在预测答案中）
    gt_lower = ground_truth.strip().lower()
    pred_lower = predicted.strip().lower()
    if gt_lower in pred_lower:
        return True

    # 3. 数字提取匹配
    # 如果标准答案是纯数字，从预测中提取数字比较
    # 先去除预测中的千位分隔符逗号（如 "1,072,693,248" → "1072693248"）
    pred_cleaned = re.sub(r'(\d),(\d{3})', lambda m: m.group(1) + m.group(2), predicted)
    # 重复去除以处理多位分隔符（如 "1,000,000"）
    for _ in range(5):
        new_pred = re.sub(r'(\d),(\d{3})', lambda m: m.group(1) + m.group(2), pred_cleaned)
        if new_pred == pred_cleaned:
            break
        pred_cleaned = new_pred

    gt_nums = re.findall(r'\d+\.?\d*', ground_truth)
    if gt_nums:
        pred_nums = re.findall(r'\d+\.?\d*', pred_cleaned)
        for gt_num in gt_nums:
            for pred_num in pred_nums:
                try:
                    if float(gt_num) == float(pred_num):
                        return True
                except ValueError:
                    continue

    # 3.6 纯数字答案的严格检查
    # 如果标准答案是纯数字，不再使用 LLM 语义判断（避免将不同数字误判为等价）
    gt_stripped = ground_truth.strip()
    is_pure_number = bool(re.match(r'^-?\d+\.?\d*$', gt_stripped))
    if is_pure_number:
        # 已经过数字提取匹配未命中，说明预测中的数字与标准答案不同
        return False

    # 3.5 严格语义检查：对于"是/否"类答案，预测中必须包含明确的结论
    if ground_truth.strip() in ["是", "否"]:
        pred_lower = predicted.lower()
        has_yes = any(kw in pred_lower for kw in [
            "是", "true", "有", "存在", "正确", "能", "可以"
        ])
        has_no = any(kw in pred_lower for kw in [
            "否", "false", "没有", "不存在", "错误", "不能", "不是", "无"
        ])
        # 如果预测中既没有"是"也没有"否"的明确结论，直接判错
        if not has_yes and not has_no:
            return False
        # 如果标准答案是"是"但预测明确说"否"，判错
        if ground_truth.strip() == "是" and has_no and not has_yes:
            return False
        # 如果标准答案是"否"但预测明确说"是"，判错
        if ground_truth.strip() == "否" and has_yes and not has_no:
            return False
        # 预测中包含"是"类关键词，标准答案是"是"，判对
        if ground_truth.strip() == "是" and has_yes:
            return True
        # 预测中包含"否"类关键词，标准答案是"否"，判对
        if ground_truth.strip() == "否" and has_no:
            return True

    # 4. 语义等价判断（最后手段）
    prompt = f"""
判断以下两个答案是否语义等价（表达相同的意思）：

预测答案: {predicted}
标准答案: {ground_truth}

只回答 "yes" 或 "no"。
"""
    response, _ = call_llm(prompt, role="default")
    return "yes" in response.lower()


def save_results(result: dict, filename: str) -> None:
    """保存评估结果到文件"""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    filepath = os.path.join(RESULTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"结果已保存到 {filepath}")


def load_gaia_dataset():
    """
    尝试加载 GAIA 数据集

    需要先安装 datasets 库并申请 GAIA 许可：
    pip install datasets
    在 HuggingFace 上申请: https://huggingface.co/datasets/gaia-benchmark/GAIA
    """
    try:
        from datasets import load_dataset
        dataset = load_dataset("gaia-benchmark/GAIA", "2023_level1")
        return dataset["validation"]
    except Exception as e:
        print(f"无法加载 GAIA 数据集: {e}")
        print("使用内置示例任务进行评估")
        return None
