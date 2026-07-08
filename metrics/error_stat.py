"""
Critic 错误拦截统计脚本

解析 results/ 目录下所有评测结果 JSON 文件，统计：
  - Critic 拦截次数（logs 中包含"不合格"的条目）
  - 错误分类：参数错误、逻辑遗漏、结果不完整、其他
  - 修正成功次数（拦截后重试最终正确的任务数）

输出到 results/error_stat.json。

用法:
    python -m metrics.error_stat
    python -m metrics.error_stat --results-dir results/
    python -m metrics.error_stat --output results/error_stat.json
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")

# 错误分类关键词映射
ERROR_PATTERNS: Dict[str, List[str]] = {
    "tool_param_error": [
        "参数", "关键词", "搜索参数", "tool_input",
        "参数错误", "参数缺少", "参数不正确", "参数不准确",
    ],
    "plan_logic_omission": [
        "遗漏", "缺少步骤", "缺少搜索", "计划缺少", "计划逻辑",
        "补充", "未包含.*步骤", "逻辑遗漏", "步骤遗漏",
        "硬编码", "未经.*验证", "未经搜索",
    ],
    "result_incomplete": [
        "不完整", "信息过少", "信息不足", "结果不完整",
        "内容不匹配", "过少", "不够完整", "结果缺失",
        "数据不足", "内容不足",
    ],
}

# Critic 拦截日志的正则模式
# 匹配: [Critic] 步骤 1 不合格，反馈: xxx
RE_INTERCEPT = re.compile(r"\[Critic\].*不合格.*反馈[:：]\s*(.+)", re.DOTALL)
# 匹配: [Critic] 规则验证步骤 1: 评分 2.7 (xxx)
RE_SCORE_FEEDBACK = re.compile(r"\[Critic\].*规则验证步骤.*评分\s*[\d.]+\s*[（(](.+)[)）]", re.DOTALL)
# 匹配: [Critic] 步骤 1 不合格
RE_REJECT = re.compile(r"\[Critic\].*不合格")


def classify_error(feedback_text: str) -> str:
    """
    根据 Critic 反馈文本对错误进行分类。

    参数:
        feedback_text: Critic 反馈文本

    返回:
        错误类型字符串: tool_param_error / plan_logic_omission /
        result_incomplete / other
    """
    text = feedback_text.strip()

    for error_type, keywords in ERROR_PATTERNS.items():
        for kw in keywords:
            if re.search(kw, text):
                return error_type

    return "other"


def extract_critic_interceptions(logs: List[str]) -> List[Dict[str, Any]]:
    """
    从日志列表中提取所有 Critic 拦截记录。

    参数:
        logs: 单个任务的日志列表

    返回:
        拦截记录列表，每条包含 step_id, feedback, error_type
    """
    interceptions = []

    for log_entry in logs:
        if not isinstance(log_entry, str):
            continue

        # 检查是否为"不合格"日志
        if not RE_REJECT.search(log_entry):
            continue

        # 提取反馈文本
        feedback = ""
        match = RE_INTERCEPT.search(log_entry)
        if match:
            feedback = match.group(1).strip()
        else:
            # 尝试从评分日志中提取反馈
            match_score = RE_SCORE_FEEDBACK.search(log_entry)
            if match_score:
                feedback = match_score.group(1).strip()

        # 提取步骤号
        step_match = re.search(r"步骤\s*(\d+)", log_entry)
        step_id = int(step_match.group(1)) if step_match else None

        error_type = classify_error(feedback) if feedback else "other"

        interceptions.append({
            "step_id": step_id,
            "feedback": feedback,
            "error_type": error_type,
            "raw_log": log_entry,
        })

    return interceptions


def find_details_arrays(obj: Any, path: str = "") -> List[Tuple[str, List[Dict]]]:
    """
    递归搜索 JSON 对象中所有名为 "details" 的数组。

    参数:
        obj: JSON 对象（dict / list / 其他）
        path: 当前递归路径（用于标识来源）

    返回:
        列表，每项为 (路径标识, details数组)
    """
    results = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key
            if key == "details" and isinstance(value, list):
                results.append((current_path, value))
            else:
                results.extend(find_details_arrays(value, current_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            current_path = f"{path}[{i}]"
            results.extend(find_details_arrays(item, current_path))

    return results


def analyze_detail(detail: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    分析单个任务详情，提取 Critic 拦截信息。

    参数:
        detail: 单个任务的详情字典

    返回:
        包含拦截信息的字典，无拦截则返回 None
    """
    logs = detail.get("logs", [])
    if not logs:
        return None

    interceptions = extract_critic_interceptions(logs)
    if not interceptions:
        return None

    # 判断最终是否正确（修正成功）
    is_correct = detail.get("correct", False)
    is_success = detail.get("success", None)
    final_correct = bool(is_correct or is_success)

    task_id = detail.get("task_id", "unknown")
    question = detail.get("question", detail.get("instruction", ""))

    return {
        "task_id": task_id,
        "question": question,
        "interception_count": len(interceptions),
        "interceptions": interceptions,
        "final_correct": final_correct,
        "correction_successful": final_correct,  # 拦截后最终正确 = 修正成功
    }


def analyze_file(filepath: str) -> Dict[str, Any]:
    """
    分析单个 JSON 结果文件。

    参数:
        filepath: JSON 文件路径

    返回:
        该文件的统计结果
    """
    filename = os.path.basename(filepath)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {
            "filename": filename,
            "error": f"无法解析: {e}",
            "total_interceptions": 0,
            "error_types": {},
            "correction_success": 0,
            "tasks_with_interceptions": [],
        }

    # 递归查找所有 details 数组
    details_arrays = find_details_arrays(data)

    total_interceptions = 0
    error_type_counts: Dict[str, int] = defaultdict(int)
    correction_success_count = 0
    tasks_with_interceptions: List[Dict] = []

    for source_path, details in details_arrays:
        for detail in details:
            if not isinstance(detail, dict):
                continue

            analysis = analyze_detail(detail)
            if analysis is None:
                continue

            analysis["source"] = source_path
            tasks_with_interceptions.append(analysis)
            total_interceptions += analysis["interception_count"]

            for interception in analysis["interceptions"]:
                error_type_counts[interception["error_type"]] += 1

            if analysis["correction_successful"]:
                correction_success_count += 1

    return {
        "filename": filename,
        "details_sources": [path for path, _ in details_arrays],
        "total_interceptions": total_interceptions,
        "error_types": dict(error_type_counts),
        "correction_success": correction_success_count,
        "tasks_with_interceptions": tasks_with_interceptions,
    }


def run_statistics(results_dir: str = RESULTS_DIR) -> Dict[str, Any]:
    """
    统计 results/ 目录下所有 JSON 文件的 Critic 拦截情况。

    参数:
        results_dir: 结果目录路径

    返回:
        汇总统计字典
    """
    if not os.path.isdir(results_dir):
        raise FileNotFoundError(f"结果目录不存在: {results_dir}")

    json_files = sorted([
        os.path.join(results_dir, f)
        for f in os.listdir(results_dir)
        if f.endswith(".json") and f != "error_stat.json"
    ])

    file_results = []
    grand_total_interceptions = 0
    grand_error_types: Dict[str, int] = defaultdict(int)
    grand_correction_success = 0
    grand_tasks_intercepted = 0

    for filepath in json_files:
        file_stat = analyze_file(filepath)
        file_results.append(file_stat)

        grand_total_interceptions += file_stat["total_interceptions"]
        for etype, count in file_stat["error_types"].items():
            grand_error_types[etype] += count
        grand_correction_success += file_stat["correction_success"]
        grand_tasks_intercepted += len(file_stat["tasks_with_interceptions"])

    # 计算修正成功率
    correction_rate = (
        round(grand_correction_success / grand_tasks_intercepted, 4)
        if grand_tasks_intercepted > 0 else 0
    )

    summary = {
        "statistics_type": "critic_error_interception",
        "results_dir": os.path.relpath(results_dir, PROJECT_ROOT),
        "total_files_analyzed": len(json_files),
        "total_interceptions": grand_total_interceptions,
        "total_tasks_intercepted": grand_tasks_intercepted,
        "correction_success_count": grand_correction_success,
        "correction_success_rate": correction_rate,
        "error_type_breakdown": {
            "tool_param_error": grand_error_types.get("tool_param_error", 0),
            "plan_logic_omission": grand_error_types.get("plan_logic_omission", 0),
            "result_incomplete": grand_error_types.get("result_incomplete", 0),
            "other": grand_error_types.get("other", 0),
        },
        "error_type_labels": {
            "tool_param_error": "参数错误",
            "plan_logic_omission": "逻辑遗漏",
            "result_incomplete": "结果不完整",
            "other": "其他",
        },
        "files": file_results,
    }

    return summary


def print_summary(summary: Dict[str, Any]):
    """打印统计摘要到控制台。"""
    print(f"\n{'=' * 70}")
    print("  Critic 错误拦截统计报告")
    print(f"{'=' * 70}")
    print(f"  分析文件数:       {summary['total_files_analyzed']}")
    print(f"  拦截总数:         {summary['total_interceptions']}")
    print(f"  被拦截任务数:     {summary['total_tasks_intercepted']}")
    print(f"  修正成功数:       {summary['correction_success_count']}")
    print(f"  修正成功率:       {summary['correction_success_rate']:.2%}")
    print(f"\n  --- 错误分类 ---")
    for etype, label in summary["error_type_labels"].items():
        count = summary["error_type_breakdown"].get(etype, 0)
        print(f"  {label:<12} ({etype}): {count}")

    print(f"\n  --- 各文件统计 ---")
    print(f"  {'文件名':<35} {'拦截数':<8} {'修正成功':<8} {'任务数':<8}")
    print(f"  {'-' * 65}")
    for f in summary["files"]:
        print(
            f"  {f['filename']:<35} "
            f"{f['total_interceptions']:<8} "
            f"{f['correction_success']:<8} "
            f"{len(f['tasks_with_interceptions']):<8}"
        )
    print(f"{'=' * 70}")


def main():
    parser = argparse.ArgumentParser(
        description="统计评测结果中 Critic 的错误拦截情况",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m metrics.error_stat
  python -m metrics.error_stat --results-dir results/
  python -m metrics.error_stat --output results/custom_stat.json
        """,
    )
    parser.add_argument(
        "--results-dir",
        type=str,
        default=RESULTS_DIR,
        help="评测结果 JSON 文件所在目录（默认: results/）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join(RESULTS_DIR, "error_stat.json"),
        help="输出统计结果文件路径（默认: results/error_stat.json）",
    )

    args = parser.parse_args()

    summary = run_statistics(args.results_dir)

    # 确保输出目录存在
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print_summary(summary)
    print(f"\n  统计结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
