"""
WebShop 真实环境评测入口（PECS 多智能体 vs ReAct 基线）

为什么要这个脚本：
  benchmarks/webshop_eval.py 里的 evaluate_webshop() / evaluate_react_webshop()
  只有函数、没有命令行入口；而 run_resumable.py / run_real_baseline.py 都是 GAIA 的。
  本脚本提供一条干净命令，专跑 WebShop，并直接算出 +pp 差值。

用法（本机真实环境）：
  # 终端 1：起 HTTP 桥（详见 docs/webshop_local_runbook.md）
  conda activate webshop
  python webshop_server.py --port 8000 --num-products 1000

  # 终端 2：跑 PECS vs ReAct 对比
  export WEBSHOP_SERVER_URL=http://localhost:8000
  python run_webshop.py

未设置 WEBSHOP_SERVER_URL 时，自动走本地 8 商品 mock（target_id 子串匹配），
用于先验证整条链路是否通，无需真实环境。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from benchmarks.webshop_eval import evaluate_webshop, evaluate_react_webshop
from benchmarks.gaia_eval import save_results


def _pp(a: float, b: float) -> float:
    return round((a - b) * 100, 2)


def main():
    real = bool(os.environ.get("WEBSHOP_SERVER_URL"))
    mode = "真实 WebShop 环境" if real else "本地 mock（未设置 WEBSHOP_SERVER_URL）"
    print("=" * 60)
    print("  WebShop 评测")
    print(f"  模式: {mode}")
    print(f"  模型: {os.getenv('LLM_MODEL')}")
    print("=" * 60)

    print("\n>>> [1/2] PECS 多智能体框架")
    ma = evaluate_webshop()
    print(f"    成功率: {ma['success_rate'] * 100:.1f}%  "
          f"({ma['success_count']}/{ma['total_samples']})  "
          f"平均 Token/题: {ma['avg_tokens_per_task']}")

    print("\n>>> [2/2] ReAct 单 Agent 基线")
    re = evaluate_react_webshop()
    print(f"    成功率: {re['success_rate'] * 100:.1f}%  "
          f"({re['success_count']}/{re['total_samples']})  "
          f"平均 Token/题: {re['avg_tokens_per_task']}")

    pp = _pp(ma["success_rate"], re["success_rate"])
    token_diff = 0.0
    if re["avg_tokens_per_task"]:
        token_diff = round(
            (re["avg_tokens_per_task"] - ma["avg_tokens_per_task"])
            / re["avg_tokens_per_task"] * 100, 1
        )

    print("\n" + "=" * 60)
    print("  汇总")
    print(f"  多智能体成功率 : {ma['success_rate'] * 100:.1f}%")
    print(f"  ReAct 成功率    : {re['success_rate'] * 100:.1f}%")
    print(f"  差值 (PECS-ReAct): {pp:+.1f} pp")
    print(f"  Token 降本      : {token_diff:+.1f}%")
    print("=" * 60)

    save_results({
        "benchmark": "webshop",
        "mode": "real" if real else "local_mock",
        "multi_agent": {
            "success_rate": ma["success_rate"],
            "success_count": ma["success_count"],
            "total_samples": ma["total_samples"],
            "avg_tokens_per_task": ma["avg_tokens_per_task"],
        },
        "react_baseline": {
            "success_rate": re["success_rate"],
            "success_count": re["success_count"],
            "total_samples": re["total_samples"],
            "avg_tokens_per_task": re["avg_tokens_per_task"],
        },
        "diff": {
            "success_pp": pp,
            "token_savings_pct": token_diff,
        },
    }, "webshop_run.json")

    print("\n结果已保存到 results/webshop_run.json")


if __name__ == "__main__":
    main()
