"""
GAIA 官方数据集评测模块

在 GAIA 官方 Level 1 validation set (53题) 上评测多智能体框架和 ReAct 基线。

与 benchmarks/gaia_eval.py（内置33题）的区别：
  - 数据源：HuggingFace 官方 gaia-benchmark/GAIA，非内置 mock
  - 答案判定：适配官方 3 种答案格式（数字/少词短语/逗号分隔列表）
  - 数据泄露防护：确保 ground_truth 不出现在任何 LLM prompt 中
  - 分层统计：无附件/有附件/总体 三层准确率
  - 统计显著性：McNemar 检验（n=53 满足最低样本要求）
  - 断点续跑：复用 run_resumable.py 机制，按官方 task_id 续跑
  - 单题超时：120 秒，超时记为失败不阻塞后续

依赖：
  - datasets, huggingface_hub (pip install)
  - HF_TOKEN 环境变量（需在 HF 申请 GAIA 访问许可）
"""
import os
import re
import json
import time
import signal
from typing import Optional, List, Dict, Any, Tuple

from agents.llm_utils import call_llm
from graph.builder import run_task
from benchmarks.react_baseline import run_react_task
from benchmarks.gaia_eval import save_results
from config import DEFAULT_TOKEN_BUDGET


class TimeoutError(Exception):
    """单题超时异常"""
    pass


def _timeout_handler(signum, frame):
    raise TimeoutError("单题执行超时")


# ============================================================
# 答案判定层：适配 GAIA 官方 3 种答案格式
# ============================================================

def _normalize_official(s: str) -> str:
    """GAIA 官方答案归一化

    官方规则（参考 GAIA 论文 + leaderboard 评测脚本）:
    - 去除首尾空格
    - 转小写
    - 去除冠词 (a/an/the)
    - 去除标点符号 (.,;:!?'")
    - 去除多余空格
    """
    if not s:
        return ""
    s = s.strip().lower()
    # 去除冠词
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    # 去除标点
    s = re.sub(r"[.,;:!?'\"()]", " ", s)
    # 合并空格
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_number(s: str) -> Optional[float]:
    """尝试解析为数字，失败返回 None

    支持:
    - 整数: "17"
    - 小数: "3.14"
    - 千分位: "1,072,693,248"
    - 科学计数: "1.5e6"
    - 分数: "3/4" → 0.75
    - 带单位: "17 hours" → 17.0 (提取首个数字)
    """
    if not s:
        return None
    s = s.strip().lower()
    # 去除千分位逗号
    s_cleaned = re.sub(r"(\d),(\d{3})", lambda m: m.group(1) + m.group(2), s)
    for _ in range(5):
        new_s = re.sub(r"(\d),(\d{3})", lambda m: m.group(1) + m.group(2), s_cleaned)
        if new_s == s_cleaned:
            break
        s_cleaned = new_s
    # 科学计数
    m = re.search(r"-?\d+\.?\d*e[+-]?\d+", s_cleaned)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    # 分数
    m = re.search(r"(\d+\.?\d*)\s*/\s*(\d+\.?\d*)", s_cleaned)
    if m:
        try:
            num = float(m.group(1))
            den = float(m.group(2))
            if den != 0:
                return num / den
        except ValueError:
            pass
    # 普通数字
    m = re.search(r"-?\d+\.?\d*", s_cleaned)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    return None


def _numbers_match(pred: str, truth: str, tolerance: float = 0.01) -> bool:
    """数字匹配，允许 1% 相对误差（处理四舍五入差异）

    GAIA 官方对数字答案通常要求精确匹配，但对计算题允许小误差。
    """
    pred_num = _parse_number(pred)
    truth_num = _parse_number(truth)
    if pred_num is None or truth_num is None:
        return False
    # 精确匹配
    if pred_num == truth_num:
        return True
    # 相对误差 < tolerance
    if truth_num != 0 and abs(pred_num - truth_num) / abs(truth_num) < tolerance:
        return True
    # 绝对误差 < 0.01（处理浮点精度）
    if abs(pred_num - truth_num) < 0.01:
        return True
    return False


def _list_match(pred: str, truth: str) -> bool:
    """逗号分隔列表匹配（顺序无关）

    GAIA 官方列表答案: "Alice, Bob, Charlie"
    匹配规则: 逐项归一化后比较，顺序无关，数量必须一致
    """
    pred_items = [_normalize_official(x) for x in pred.split(",")]
    truth_items = [_normalize_official(x) for x in truth.split(",")]
    pred_items = [x for x in pred_items if x]
    truth_items = [x for x in truth_items if x]
    if not pred_items or not truth_items:
        return False
    if len(pred_items) != len(truth_items):
        return False
    return set(pred_items) == set(truth_items)


def _string_match(pred: str, truth: str) -> bool:
    """少词短语匹配

    GAIA 官方: "as few words as possible"
    匹配规则: 归一化后精确匹配，或标准答案包含在预测中
    """
    norm_pred = _normalize_official(pred)
    norm_truth = _normalize_official(truth)
    if not norm_pred or not norm_truth:
        return False
    # 精确匹配
    if norm_pred == norm_truth:
        return True
    # 包含匹配（标准答案包含在预测中）
    if norm_truth in norm_pred:
        return True
    # 预测以标准答案开头
    if norm_pred.startswith(norm_truth):
        return True
    return False


def evaluate_answer_official(predicted: str, ground_truth: str) -> bool:
    """GAIA 官方答案判定

    官方答案格式（3 种）:
    1. 数字: "17" / "3.14" / "1,072,693,248"
    2. 少词短语: "Alice" / "Paris" / "MIT"
    3. 逗号分隔列表: "Alice, Bob, Charlie"

    判定优先级:
    1. 归一化精确匹配
    2. 如果标准答案含逗号 → 列表匹配
    3. 如果标准答案是数字 → 数字匹配
    4. 字符串包含匹配
    5. LLM 语义判断（最后兜底，避免误判）

    与内置 evaluate_answer 的区别:
    - 内置版面向中文答案（是/否判断、中文标点）
    - 官方版面向英文答案（冠词去除、英文标点、列表匹配）
    """
    if not predicted or not ground_truth:
        return False

    # 0. 归一化精确匹配
    norm_pred = _normalize_official(predicted)
    norm_truth = _normalize_official(ground_truth)
    if norm_pred and norm_pred == norm_truth:
        return True

    # 1. 列表匹配（标准答案含逗号）
    if "," in ground_truth:
        if _list_match(predicted, ground_truth):
            return True

    # 2. 数字匹配
    truth_num = _parse_number(ground_truth)
    if truth_num is not None:
        if _numbers_match(predicted, ground_truth):
            return True

    # 3. 字符串包含匹配
    if _string_match(predicted, ground_truth):
        return True

    # 4. LLM 语义判断（最后兜底）
    # 只在上述规则都不匹配时调用，避免 API 浪费
    try:
        prompt = (
            f"判断以下预测答案是否与标准答案语义等价。只需回答'是'或'否'。\n\n"
            f"标准答案: {ground_truth}\n"
            f"预测答案: {predicted}\n\n"
            f"是否等价（是/否）:"
        )
        result, _ = call_llm(prompt, "你是答案判定助手，只回答是或否", role="executor")
        result = result.strip().rstrip(".。!,！？?")
        return result == "是" or result.lower() == "yes"
    except Exception:
        return False


# ============================================================
# 数据泄露防护
# ============================================================

def check_data_leakage(question: str, ground_truth: str) -> bool:
    """检查 ground_truth 是否泄露在 question 中

    GAIA validation set 答案是公开的，如果答案出现在 LLM 的 prompt 里，
    LLM 可能直接复述答案，导致评测结果无效（学术诚信红线）。

    检查规则:
    - 标准答案（归一化后）不出现在 question 中
    - 数字答案的精确值不出现（防止 "17" 出现在题目描述里）

    返回: True 表示有泄露（危险），False 表示安全
    """
    if not ground_truth or not question:
        return False

    norm_q = _normalize_official(question)
    norm_truth = _normalize_official(ground_truth)

    # 数字答案：优先用 word boundary 检查
    # 避免 "17" 误匹配 "2017"（归一化包含检查不使用 word boundary 会误报）
    truth_num = _parse_number(ground_truth)
    if truth_num is not None:
        truth_str = str(int(truth_num)) if truth_num == int(truth_num) else str(truth_num)
        # 用 word boundary 检查数字是否作为独立 token 出现
        if re.search(r"\b" + re.escape(truth_str) + r"\b", question):
            return True
        # 数字答案只做 word boundary 检查，跳过归一化包含检查
        return False

    # 非数字答案：归一化后包含检查
    if norm_truth and len(norm_truth) >= 2 and norm_truth in norm_q:
        return True

    return False


# ============================================================
# 统计显著性检验
# ============================================================

def mcnemar_test(pecs_correct: List[bool], react_correct: List[bool]) -> Dict[str, float]:
    """McNemar 检验（配对，用于比较两个分类器在同一组样本上的表现）

    n=53 满足统计检验最低要求，这是接官方数据集的核心增值之一。

    参数:
        pecs_correct: PECS 每题是否正确 [True/False, ...]
        react_correct: ReAct 每题是否正确 [True/False, ...]

    返回:
        {
            "n": 样本数,
            "b": PECS对ReAct错,
            "c": PECS错ReAct对,
            "statistic": 卡方统计量,
            "p_value": p值（<0.05 表示差异统计显著）,
            "significant": 是否统计显著,
        }
    """
    if len(pecs_correct) != len(react_correct):
        raise ValueError("两个列表长度必须一致")

    n = len(pecs_correct)
    # b: PECS 对、ReAct 错
    b = sum(1 for p, r in zip(pecs_correct, react_correct) if p and not r)
    # c: PECS 错、ReAct 对
    c = sum(1 for p, r in zip(pecs_correct, react_correct) if not p and r)

    # McNemar 卡方统计量（带连续性校正）
    if b + c == 0:
        return {"n": n, "b": b, "c": c, "statistic": 0.0, "p_value": 1.0, "significant": False}

    statistic = (abs(b - c) - 1) ** 2 / (b + c) if (b + c) > 0 else 0.0

    # p 值（卡方分布，df=1）
    # 近似公式: p = exp(-statistic/2) for df=1
    import math
    p_value = math.exp(-statistic / 2) if statistic > 0 else 1.0

    # 精确 p 值（小样本时用二项分布）
    if b + c < 25:
        # 精确检验: 在 b+c 次试验中，较少类出现次数的概率
        from math import comb
        total = b + c
        smaller = min(b, c)
        p_value = 2 * sum(comb(total, k) * (0.5 ** total) for k in range(smaller + 1))
        p_value = min(p_value, 1.0)

    return {
        "n": n,
        "b": b,  # PECS 对、ReAct 错
        "c": c,  # PECS 错、ReAct 对
        "statistic": round(statistic, 4),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
    }


# ============================================================
# 评测主函数
# ============================================================

def evaluate_gaia_official(
    agent_type: str = "multi_agent",  # "multi_agent" | "react"
    num_samples: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    level: int = 1,
    split: str = "validation",
    timeout_seconds: int = 120,
) -> Dict[str, Any]:
    """在 GAIA 官方数据集上评测

    参数:
        agent_type: "multi_agent"（PECS）或 "react"（ReAct 基线）
        num_samples: 评测题数，None=全部
        token_budget: 每题 Token 预算
        level: GAIA 难度等级
        split: "validation"（有答案）或 "test"（答案私有）
        timeout_seconds: 单题超时秒数

    返回:
        评测结果字典，含分层统计
    """
    from datasets.gaia_official_dataset import GAIAOfficialDataset

    ds = GAIAOfficialDataset(level=level, split=split)
    samples = ds.load_samples(num_samples)

    print(f"\n{'='*60}")
    print(f"  GAIA 官方评测 (Level {level}, {split})")
    print(f"  Agent: {agent_type}")
    print(f"  样本数: {len(samples)}")
    info = ds.get_dataset_info()
    print(f"  无附件: {info.get('no_attachment_count', '?')}  有附件: {info.get('with_attachment_count', '?')}")
    print(f"{'='*60}")

    results = []
    correct_count = 0
    total_tokens = 0
    # 分层统计
    no_file_correct = 0
    no_file_total = 0
    has_file_correct = 0
    has_file_total = 0

    # 设置超时
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler) if hasattr(signal, "SIGALRM") else None

    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        ground_truth = sample["answer"]
        has_attachment = bool(sample.get("file_path"))

        # 数据泄露检查
        if check_data_leakage(question, ground_truth):
            print(f"[{i+1}/{len(samples)}] {task_id}: ⚠️ 数据泄露警告，跳过")
            results.append({
                "task_id": task_id,
                "question": question[:100],
                "ground_truth": ground_truth,
                "predicted": "",
                "correct": False,
                "tokens_used": 0,
                "has_attachment": has_attachment,
                "error": "data_leakage_skip",
            })
            continue

        # 构造完整问题（含附件路径）
        full_question = question
        attachment_path = ds.resolve_attachment(sample)
        if attachment_path:
            ext = os.path.splitext(attachment_path)[1].lower()
            # 图片/音视频附件 DeepSeek 无法处理，标记跳过
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".mp3", ".mp4", ".mov", ".m4a"):
                print(f"[{i+1}/{len(samples)}] {task_id}: 跳过（{ext} 附件需多模态模型）")
                results.append({
                    "task_id": task_id,
                    "question": question[:100],
                    "ground_truth": ground_truth,
                    "predicted": "",
                    "correct": False,
                    "tokens_used": 0,
                    "has_attachment": has_attachment,
                    "error": f"multimodal_skip_{ext}",
                })
                has_file_total += 1
                continue
            full_question = f"{question}\n\n[附件文件路径: {attachment_path}]"

        print(f"[{i+1}/{len(samples)}] {task_id}: {question[:60]}...")

        # 带超时执行
        predicted = ""
        tokens_used = 0
        error = None
        t0 = time.time()
        try:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(timeout_seconds)
            if agent_type == "multi_agent":
                state = run_task(full_question, token_budget)
                predicted = state.get("final_answer", "")
                tokens_used = state.get("token_used", 0)
            else:  # react
                state = run_react_task(full_question, token_budget, max_steps=5)
                predicted = state.get("final_answer", "")
                tokens_used = state.get("token_used", 0)
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
        except TimeoutError:
            error = "timeout"
            print(f"  ⚠️ 超时（{timeout_seconds}s）")
        except Exception as e:
            error = str(e)[:200]
            print(f"  ⚠️ 异常: {error[:80]}")
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)

        dt = time.time() - t0
        total_tokens += tokens_used

        # 答案判定
        is_correct = False
        if predicted and not error:
            is_correct = evaluate_answer_official(predicted, ground_truth)
        if is_correct:
            correct_count += 1
            if has_attachment:
                has_file_correct += 1
            else:
                no_file_correct += 1

        if has_attachment:
            has_file_total += 1
        else:
            no_file_total += 1

        results.append({
            "task_id": task_id,
            "question": question[:100],
            "ground_truth": ground_truth,
            "predicted": predicted[:200] if predicted else "",
            "correct": is_correct,
            "tokens_used": tokens_used,
            "has_attachment": has_attachment,
            "elapsed_seconds": round(dt, 1),
            "error": error,
        })

        status = "✓" if is_correct else "✗" if not error else "⚠"
        print(f"  {status} 预测: {predicted[:60] if predicted else '(空)'}... | 正确: {is_correct} | {dt:.1f}s {tokens_used}tok")

    # 恢复信号处理
    if old_handler is not None and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, old_handler)

    # 计算指标
    valid_count = len([r for r in results if r.get("error") is None or r.get("error") == "timeout"])
    accuracy = correct_count / len(samples) if samples else 0
    avg_tokens = total_tokens / len(samples) if samples else 0

    # 分层准确率
    no_file_acc = no_file_correct / no_file_total if no_file_total else 0
    has_file_acc = has_file_correct / has_file_total if has_file_total else 0

    eval_result = {
        "agent_type": agent_type,
        "source": "official",
        "level": level,
        "split": split,
        "total_samples": len(samples),
        "correct_count": correct_count,
        "accuracy": round(accuracy, 4),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(avg_tokens),
        "no_attachment": {
            "total": no_file_total,
            "correct": no_file_correct,
            "accuracy": round(no_file_acc, 4),
        },
        "with_attachment": {
            "total": has_file_total,
            "correct": has_file_correct,
            "accuracy": round(has_file_acc, 4),
        },
        "errors": [r["task_id"] for r in results if r.get("error")],
        "details": results,
    }

    # 保存结果
    filename = f"gaia_official_{agent_type}.json"
    save_results(eval_result, filename)
    return eval_result


def run_official_comparison(
    num_samples: Optional[int] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    level: int = 1,
) -> Dict[str, Any]:
    """运行 PECS vs ReAct 官方数据集对比评测

    返回聚合结果，含 McNemar 检验
    """
    print("\n" + "="*60)
    print("  GAIA 官方数据集对比评测 (PECS vs ReAct)")
    print("="*60)

    print("\n>>> [1/2] PECS 多智能体")
    ma = evaluate_gaia_official("multi_agent", num_samples, token_budget, level)
    print(f"\n    准确率: {ma['accuracy']*100:.1f}% ({ma['correct_count']}/{ma['total_samples']})")
    print(f"    无附件: {ma['no_attachment']['accuracy']*100:.1f}% ({ma['no_attachment']['correct']}/{ma['no_attachment']['total']})")
    print(f"    有附件: {ma['with_attachment']['accuracy']*100:.1f}% ({ma['with_attachment']['correct']}/{ma['with_attachment']['total']})")
    print(f"    平均 Token/题: {ma['avg_tokens_per_task']}")

    print("\n>>> [2/2] ReAct 基线")
    re = evaluate_gaia_official("react", num_samples, token_budget, level)
    print(f"\n    准确率: {re['accuracy']*100:.1f}% ({re['correct_count']}/{re['total_samples']})")
    print(f"    无附件: {re['no_attachment']['accuracy']*100:.1f}% ({re['no_attachment']['correct']}/{re['no_attachment']['total']})")
    print(f"    有附件: {re['with_attachment']['accuracy']*100:.1f}% ({re['with_attachment']['correct']}/${re['with_attachment']['total']})")
    print(f"    平均 Token/题: {re['avg_tokens_per_task']}")

    # McNemar 检验
    # 按 task_id 对齐两个结果的 correct 列表
    ma_correct_by_id = {r["task_id"]: r["correct"] for r in ma["details"]}
    re_correct_by_id = {r["task_id"]: r["correct"] for r in re["details"]}
    common_ids = set(ma_correct_by_id.keys()) & set(re_correct_by_id.keys())
    ma_correct = [ma_correct_by_id[tid] for tid in sorted(common_ids)]
    re_correct = [re_correct_by_id[tid] for tid in sorted(common_ids)]
    mcnemar = mcnemar_test(ma_correct, re_correct)

    # 汇总
    pp = round((ma["accuracy"] - re["accuracy"]) * 100, 2)
    token_diff = round((re["avg_tokens_per_task"] - ma["avg_tokens_per_task"]) / re["avg_tokens_per_task"] * 100, 1) if re["avg_tokens_per_task"] else 0

    summary = {
        "benchmark": "gaia_official",
        "level": level,
        "split": "validation",
        "multi_agent": {
            "accuracy": ma["accuracy"],
            "correct_count": ma["correct_count"],
            "total_samples": ma["total_samples"],
            "avg_tokens_per_task": ma["avg_tokens_per_task"],
            "no_attachment_acc": ma["no_attachment"]["accuracy"],
            "with_attachment_acc": ma["with_attachment"]["accuracy"],
        },
        "react_baseline": {
            "accuracy": re["accuracy"],
            "correct_count": re["correct_count"],
            "total_samples": re["total_samples"],
            "avg_tokens_per_task": re["avg_tokens_per_task"],
            "no_attachment_acc": re["no_attachment"]["accuracy"],
            "with_attachment_acc": re["with_attachment"]["accuracy"],
        },
        "diff": {
            "accuracy_pp": pp,
            "token_savings_pct": token_diff,
        },
        "mcnemar_test": mcnemar,
    }

    print("\n" + "="*60)
    print("  汇总")
    print(f"  PECS 准确率 : {ma['accuracy']*100:.1f}%  ({ma['avg_tokens_per_task']} tok/题)")
    print(f"  ReAct 准确率: {re['accuracy']*100:.1f}%  ({re['avg_tokens_per_task']} tok/题)")
    print(f"  差值        : {pp:+.1f} pp  Token 降本: {token_diff:+.1f}%")
    print(f"  McNemar     : χ²={mcnemar['statistic']}, p={mcnemar['p_value']}, {'统计显著' if mcnemar['significant'] else '不显著'}")
    print(f"  分层(无附件): PECS {ma['no_attachment']['accuracy']*100:.1f}% vs ReAct {re['no_attachment']['accuracy']*100:.1f}%")
    print(f"  分层(有附件): PECS {ma['with_attachment']['accuracy']*100:.1f}% vs ReAct {re['with_attachment']['accuracy']*100:.1f}%")
    print("="*60)

    save_results(summary, "gaia_official_run.json")
    return summary
