"""
GAIA 全量 33 题评测入口（PECS 多智能体 vs ReAct 基线）

扩样从 10 题到 33 题，直接回应"样本太小"质疑。
内置样本库 33 题，分布：计算 16 / 推理 10 / 其他 7（文件解析+网页浏览）

用法：
  python run_gaia_full.py

结果保存至 results/gaia_multi_agent.json 和 results/gaia_react_baseline.json
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from benchmarks.gaia_eval import evaluate_gaia, GAIA_L1_SAMPLES, save_results
from benchmarks.react_baseline import evaluate_react_gaia


def _pp(a, b):
    return round((a - b) * 100, 2)


def main():
    total = len(GAIA_L1_SAMPLES)
    print("=" * 60)
    print("  GAIA 全量评测（" + str(total) + " 题）")
    print("  模型: " + os.getenv("LLM_MODEL", "未设置"))
    print("=" * 60)

    print("\n>>> [1/2] PECS 多智能体框架")
    t0 = time.time()
    ma = evaluate_gaia(num_samples=None)  # None = 全部 33 题
    dt1 = time.time() - t0
    print("    成功率: " + str(ma["accuracy"] * 100) + "%  "
          "(" + str(ma["correct_count"]) + "/" + str(ma["total_samples"]) + ")  "
          "平均 Token/题: " + str(ma["avg_tokens_per_task"]) + "  "
          "耗时: " + str(round(dt1 / 60, 1)) + "min")

    print("\n>>> [2/2] ReAct 单 Agent 基线")
    t0 = time.time()
    re = evaluate_react_gaia(num_samples=None)  # None = 全部 33 题
    dt2 = time.time() - t0
    print("    成功率: " + str(re["accuracy"] * 100) + "%  "
          "(" + str(re["correct_count"]) + "/" + str(re["total_samples"]) + ")  "
          "平均 Token/题: " + str(re["avg_tokens_per_task"]) + "  "
          "耗时: " + str(round(dt2 / 60, 1)) + "min")

    pp = _pp(ma["accuracy"], re["accuracy"])
    token_diff = 0.0
    if re["avg_tokens_per_task"]:
        token_diff = round(
            (re["avg_tokens_per_task"] - ma["avg_tokens_per_task"])
            / re["avg_tokens_per_task"] * 100, 1
        )

    print("\n" + "=" * 60)
    print("  汇总")
    print("  多智能体准确率 : " + str(ma["accuracy"] * 100) + "%")
    print("  ReAct 准确率   : " + str(re["accuracy"] * 100) + "%")
    print("  差值 (PECS-ReAct): " + str(pp) + " pp")
    print("  Token 降本     : " + str(token_diff) + "%")
    print("  总耗时: " + str(round((dt1 + dt2) / 60, 1)) + "min")
    print("=" * 60)

    save_results({
        "benchmark": "gaia_full",
        "total_samples": total,
        "multi_agent": {
            "accuracy": ma["accuracy"],
            "correct_count": ma["correct_count"],
            "total_samples": ma["total_samples"],
            "avg_tokens_per_task": ma["avg_tokens_per_task"],
        },
        "react_baseline": {
            "accuracy": re["accuracy"],
            "correct_count": re["correct_count"],
            "total_samples": re["total_samples"],
            "avg_tokens_per_task": re["avg_tokens_per_task"],
        },
        "diff": {
            "accuracy_pp": pp,
            "token_savings_pct": token_diff,
        },
    }, "gaia_run.json")

    print("\n结果已保存到 results/gaia_run.json")


if __name__ == "__main__":
    main()
