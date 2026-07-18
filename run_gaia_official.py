"""
GAIA 官方数据集评测入口

在 GAIA 官方 Level 1 validation set (53题) 上评测 PECS 多智能体 vs ReAct 基线。
这是从"内置33题自测"升级到"官方 benchmark 验证"的质变。

用法:
  # 先跑 3 题验证链路
  python run_gaia_official.py --num 3

  # 跑全量 53 题
  python run_gaia_official.py

  # 只跑 PECS（复用已有 ReAct 结果）
  python run_gaia_official.py --only multi_agent

结果保存至:
  results/gaia_official_multi_agent.json
  results/gaia_official_react.json
  results/gaia_official_run.json (聚合 + McNemar 检验)
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from benchmarks.gaia_official import evaluate_gaia_official, run_official_comparison
from benchmarks.gaia_eval import save_results
from config import DEFAULT_TOKEN_BUDGET


def main():
    parser = argparse.ArgumentParser(description="GAIA 官方数据集评测（53题 validation set）")
    parser.add_argument("--num", type=int, default=None, help="评测题数（默认全部53题）")
    parser.add_argument("--only", choices=["all", "multi_agent", "react"], default="all",
                        help="all=PECS+ReAct对比; multi_agent=仅PECS; react=仅ReAct")
    parser.add_argument("--budget", type=int, default=DEFAULT_TOKEN_BUDGET, help="每题 Token 预算")
    parser.add_argument("--timeout", type=int, default=120, help="单题超时秒数")
    args = parser.parse_args()

    if args.only == "all":
        # 完整对比 + McNemar
        summary = run_official_comparison(args.num, args.budget)
        print("\n聚合结果已保存到 results/gaia_official_run.json")
    else:
        # 单独跑
        result = evaluate_gaia_official(
            agent_type=args.only,
            num_samples=args.num,
            token_budget=args.budget,
            timeout_seconds=args.timeout,
        )
        print(f"\n准确率: {result['accuracy']*100:.1f}% ({result['correct_count']}/{result['total_samples']})")
        print(f"无附件: {result['no_attachment']['accuracy']*100:.1f}%  有附件: {result['with_attachment']['accuracy']*100:.1f}%")
        print(f"平均 Token/题: {result['avg_tokens_per_task']}")


if __name__ == "__main__":
    main()
