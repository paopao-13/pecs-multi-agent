"""
BatchRunner —— 批量任务执行入口

功能：
- 支持批量传入任务列表（字符串列表或从数据集加载）
- 串行执行每个任务（调用 graph.builder.run_task）
- 统计：成功率、平均token消耗、总执行时间、错误数
- 导出指标到 results/batch_report.json
- 支持从 datasets/ 加载GAIA mock样本批量执行

CLI 用法:
    python -m src.batch_runner [--num-samples 10]
    python -m src.batch_runner --num-samples 5 --token-budget 30000
"""
import argparse
import json
import os
import time
import traceback
from datetime import datetime
from typing import List, Dict, Any, Optional

from graph.builder import run_task
from datasets.base_dataset import BaseDataset
from logger.graph_trace_logger import GraphTraceLogger, export_task_trace


# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULTS_DIR = os.path.join(_PROJECT_ROOT, "results")


class BatchRunner:
    """批量任务执行器"""

    def __init__(self, token_budget: int = 50000, verbose: bool = True):
        """
        初始化批量执行器

        参数:
            token_budget: 每个任务的 Token 预算上限
            verbose: 是否打印详细执行信息
        """
        self.token_budget = token_budget
        self.verbose = verbose
        self.trace_logger = GraphTraceLogger(verbose=verbose)

    def run_tasks(self, queries: List[str]) -> dict:
        """
        批量执行任务（纯字符串列表，无标准答案评估）

        参数:
            queries: 任务问题字符串列表

        返回:
            批量执行报告字典，包含：
            - total_tasks: 总任务数
            - success_count: 成功完成数（未报错）
            - error_count: 错误数
            - error_rate: 错误率
            - total_tokens: 总 Token 消耗
            - avg_tokens_per_task: 平均每任务 Token 消耗
            - total_time: 总执行时间（秒）
            - avg_time_per_task: 平均每任务执行时间（秒）
            - details: 每个任务的详细结果
        """
        if self.verbose:
            print(f"\n{'#'*60}")
            print(f"# BatchRunner: 开始批量执行 {len(queries)} 个任务")
            print(f"# Token 预算: {self.token_budget:,} / 任务")
            print(f"{'#'*60}\n")

        details = []
        success_count = 0
        error_count = 0
        total_tokens = 0
        total_time = 0.0

        for i, query in enumerate(queries):
            task_label = f"任务 {i+1}/{len(queries)}"
            result = self._run_single_task(query, task_label, i + 1)

            details.append(result)
            total_tokens += result.get("tokens_used", 0)
            total_time += result.get("execution_time", 0.0)

            if result.get("status") == "success":
                success_count += 1
            else:
                error_count += 1

        report = self._build_report(
            total_tasks=len(queries),
            success_count=success_count,
            error_count=error_count,
            total_tokens=total_tokens,
            total_time=total_time,
            details=details,
            source="custom_queries",
        )

        if self.verbose:
            self._print_summary(report)

        return report

    def run_dataset(self, dataset: BaseDataset, num_samples: int = None) -> dict:
        """
        从数据集加载样本并批量执行（含标准答案评估）

        参数:
            dataset: 实现 BaseDataset 接口的数据集实例
            num_samples: 执行前 N 条样本，None 表示全部

        返回:
            批量执行报告字典，包含：
            - total_tasks: 总任务数
            - success_count: 答案正确数
            - error_count: 执行错误数
            - success_rate: 成功率（答案正确率）
            - error_rate: 错误率
            - total_tokens: 总 Token 消耗
            - avg_tokens_per_task: 平均每任务 Token 消耗
            - total_time: 总执行时间（秒）
            - avg_time_per_task: 平均每任务执行时间（秒）
            - dataset_info: 数据集元信息
            - details: 每个任务的详细结果
        """
        # 加载样本
        samples = dataset.load_samples(num_samples)
        dataset_info = dataset.get_dataset_info()

        if self.verbose:
            print(f"\n{'#'*60}")
            print(f"# BatchRunner: 从数据集 [{dataset_info.get('name', 'Unknown')}] 加载 {len(samples)} 个样本")
            print(f"# 数据来源: {dataset_info.get('source', 'unknown')} | Mock: {dataset_info.get('is_mock', '?')}")
            print(f"# Token 预算: {self.token_budget:,} / 任务")
            print(f"{'#'*60}\n")

        details = []
        correct_count = 0
        error_count = 0
        total_tokens = 0
        total_time = 0.0

        for i, sample in enumerate(samples):
            task_id = sample.get("task_id", f"task_{i+1}")
            query = sample.get("question", "")
            ground_truth = sample.get("answer", "")
            task_label = f"任务 {i+1}/{len(samples)} [{task_id}]"

            result = self._run_single_task(query, task_label, i + 1, task_id=task_id)

            # 评估答案
            if result.get("status") == "success":
                predicted = result.get("final_answer", "")
                is_correct = dataset.evaluate_answer(predicted, ground_truth)
                result["ground_truth"] = ground_truth
                result["correct"] = is_correct
                if is_correct:
                    correct_count += 1
            else:
                result["ground_truth"] = ground_truth
                result["correct"] = False

            details.append(result)
            total_tokens += result.get("tokens_used", 0)
            total_time += result.get("execution_time", 0.0)

            if result.get("status") != "success":
                error_count += 1

            if self.verbose:
                status_icon = "OK" if result.get("correct") else "FAIL"
                print(f"  [{status_icon}] 预测: {_truncate(result.get('final_answer', ''), 60)}")
                print(f"         标准答案: {ground_truth}")
                print()

        total_tasks = len(samples)
        success_rate = correct_count / total_tasks if total_tasks > 0 else 0.0

        report = self._build_report(
            total_tasks=total_tasks,
            success_count=correct_count,
            error_count=error_count,
            total_tokens=total_tokens,
            total_time=total_time,
            details=details,
            source="dataset",
            dataset_info=dataset_info,
            success_rate=success_rate,
        )

        if self.verbose:
            self._print_summary(report)

        return report

    def _run_single_task(
        self,
        query: str,
        task_label: str,
        index: int,
        task_id: str = None,
    ) -> dict:
        """
        执行单个任务

        参数:
            query: 任务问题
            task_label: 任务标签（用于打印）
            index: 任务序号
            task_id: 任务ID（可选）

        返回:
            单任务执行结果字典
        """
        if self.verbose:
            print(f"[{task_label}] {query[:100]}")

        result = {
            "task_index": index,
            "task_id": task_id or f"task_{index}",
            "query": query,
            "status": "success",
            "final_answer": "",
            "tokens_used": 0,
            "execution_time": 0.0,
            "error": "",
        }

        try:
            start_time = time.time()
            state = run_task(query, token_budget=self.token_budget)
            elapsed = time.time() - start_time

            result["final_answer"] = state.get("final_answer", "")
            result["tokens_used"] = state.get("token_used", 0)
            result["execution_time"] = round(elapsed, 2)
            result["complexity"] = state.get("complexity", "")
            result["iteration"] = state.get("iteration", 0)

            # 导出全链路日志
            try:
                trace_path = export_task_trace(state, task_id=result["task_id"])
                result["trace_path"] = trace_path
            except Exception as trace_err:
                if self.verbose:
                    print(f"  [Warning] 日志导出失败: {trace_err}")

            if self.verbose:
                print(f"  → 完成 | Token: {result['tokens_used']:,} | 耗时: {result['execution_time']}s")

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            if self.verbose:
                print(f"  [ERROR] {e}")

        return result

    def _build_report(
        self,
        total_tasks: int,
        success_count: int,
        error_count: int,
        total_tokens: int,
        total_time: float,
        details: list,
        source: str = "custom_queries",
        dataset_info: dict = None,
        success_rate: float = None,
    ) -> dict:
        """构建批量执行报告"""
        # 对于 dataset 模式，success_count 是答案正确数
        # 对于 custom_queries 模式，success_count 是未报错数
        if success_rate is None:
            success_rate = success_count / total_tasks if total_tasks > 0 else 0.0

        report = {
            "report_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "token_budget_per_task": self.token_budget,
            "total_tasks": total_tasks,
            "success_count": success_count,
            "error_count": error_count,
            "success_rate": round(success_rate, 4),
            "error_rate": round(error_count / total_tasks, 4) if total_tasks > 0 else 0.0,
            "total_tokens": total_tokens,
            "avg_tokens_per_task": round(total_tokens / total_tasks) if total_tasks > 0 else 0,
            "total_time_seconds": round(total_time, 2),
            "avg_time_per_task_seconds": round(total_time / total_tasks, 2) if total_tasks > 0 else 0,
            "details": details,
        }

        if dataset_info:
            report["dataset_info"] = dataset_info

        return report

    def _print_summary(self, report: dict):
        """打印批量执行摘要"""
        print(f"\n{'='*60}")
        print(f"批量执行报告")
        print(f"{'='*60}")
        print(f"  总任务数:       {report['total_tasks']}")
        print(f"  成功数:         {report['success_count']}")
        print(f"  错误数:         {report['error_count']}")
        print(f"  成功率:         {report['success_rate']:.2%}")
        print(f"  总 Token 消耗:  {report['total_tokens']:,}")
        print(f"  平均 Token/任务: {report['avg_tokens_per_task']:,}")
        print(f"  总执行时间:     {report['total_time_seconds']}s")
        print(f"  平均时间/任务:  {report['avg_time_per_task_seconds']}s")
        print(f"{'='*60}")

    def save_report(self, report: dict, filename: str = "batch_report.json"):
        """
        保存报告到 results/ 目录

        参数:
            report: 批量执行报告字典
            filename: 输出文件名
        """
        os.makedirs(_RESULTS_DIR, exist_ok=True)
        filepath = os.path.join(_RESULTS_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        if self.verbose:
            print(f"\n报告已保存到: {filepath}")

        return filepath


def _truncate(text: str, max_len: int = 60) -> str:
    """截断文本"""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def main():
    """
    CLI入口：python -m src.batch_runner [--num-samples 10]

    从 GAIA Mock 数据集加载样本，批量执行并导出报告。
    """
    parser = argparse.ArgumentParser(
        description="PECS 多智能体批量任务执行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m src.batch_runner                    # 执行全部 GAIA Mock 样本
  python -m src.batch_runner --num-samples 10   # 执行前 10 个样本
  python -m src.batch_runner --num-samples 5 --token-budget 30000
        """,
    )
    parser.add_argument(
        "--num-samples", "-n",
        type=int,
        default=None,
        help="执行前 N 个样本（默认全部）",
    )
    parser.add_argument(
        "--token-budget", "-b",
        type=int,
        default=50000,
        help="每个任务的 Token 预算上限（默认 50000）",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="batch_report.json",
        help="报告输出文件名（默认 batch_report.json）",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="静默模式，不打印详细执行信息",
    )

    args = parser.parse_args()

    # 延迟导入，避免在模块加载时触发 HuggingFace 依赖
    from datasets.gaia_mock_dataset import GAIAMockDataset

    # 加载数据集
    dataset = GAIAMockDataset()
    dataset_info = dataset.get_dataset_info()

    print(f"数据集: {dataset_info['name']}")
    print(f"样本总数: {dataset_info['total_samples']}")
    print(f"数据来源: {dataset_info['source']}")
    print()

    # 创建批量执行器
    runner = BatchRunner(
        token_budget=args.token_budget,
        verbose=not args.quiet,
    )

    # 执行批量任务
    report = runner.run_dataset(dataset, num_samples=args.num_samples)

    # 保存报告
    runner.save_report(report, filename=args.output)

    print(f"\n完成! 成功率: {report['success_rate']:.2%} ({report['success_count']}/{report['total_tasks']})")


if __name__ == "__main__":
    main()
