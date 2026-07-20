"""
WebShop 真实环境评测入口（PECS 多智能体 vs ReAct 基线 vs ReAct-light 消融）

为什么要这个脚本：
  benchmarks/webshop_eval.py 里的 evaluate_*() 只有函数、没有命令行入口。
  本脚本提供一条干净命令，专跑 WebShop，并直接算出 +pp 差值。

三组对比（消融实验）：
  [1/3] PECS 多智能体    —— 完整规则层（Buy→click[Buy Now] + 搜到结果→click[ASIN]）
  [2/3] ReAct 纯 LLM     —— 无规则层（LLM 自由决策，易陷 search 循环）
  [3/3] ReAct 轻量规则层 —— 只有 Buy→click[Buy Now]，不强制 click[ASIN]
  目的：证明 PECS 优势来自"打破 search 循环"的规则2，而非"有规则层"本身

用法（本机真实环境）：
  # 终端 1：起 HTTP 桥（详见 docs/webshop_local_runbook.md）
  conda activate webshop
  python webshop_server.py --port 8000 --num-products 1000

  # 终端 2：跑三组完整对比
  export WEBSHOP_SERVER_URL=http://localhost:8000
  python run_webshop.py

  # 只跑 ReAct-light（复用已有 PECS/ReAct 结果，省时间）
  python run_webshop.py --only light

未设置 WEBSHOP_SERVER_URL 时，自动走本地 8 商品 mock（target_id 子串匹配），
用于先验证整条链路是否通，无需真实环境。
"""
import os
import sys
import argparse
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from agents.llm_utils import set_deterministic_mode
from benchmarks.webshop_eval import (
    evaluate_webshop,
    evaluate_react_webshop,
    evaluate_react_webshop_light,
)
from benchmarks.gaia_eval import save_results


def _pp(a: float, b: float) -> float:
    return round((a - b) * 100, 2)


def _load_existing(filename: str):
    """读取已有评测结果（--only light 模式复用，避免重跑 PECS/ReAct）。"""
    path = os.path.join("results", filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _print_result(label: str, r: dict):
    print(f"    成功率: {r['success_rate'] * 100:.1f}%  "
          f"({r['success_count']}/{r['total_samples']})  "
          f"平均 Token/题: {r['avg_tokens_per_task']}")


def main():
    # 可复现：固定所有角色 temperature=0，确保三组对比数字可复现、可 defense
    set_deterministic_mode()

    parser = argparse.ArgumentParser(description="WebShop 三组对比评测（PECS / ReAct / ReAct-light）")
    parser.add_argument(
        "--only", choices=["all", "light"], default="all",
        help="all=跑全部三组; light=只跑 ReAct-light（PECS/ReAct 复用已有结果）",
    )
    args = parser.parse_args()

    real = bool(os.environ.get("WEBSHOP_SERVER_URL"))
    mode = "真实 WebShop 环境" if real else "本地 mock（未设置 WEBSHOP_SERVER_URL）"
    print("=" * 60)
    print("  WebShop 评测（三组消融对比）")
    print(f"  模式: {mode}")
    print(f"  模型: {os.getenv('LLM_MODEL')}")
    print(f"  范围: {args.only}")
    print("=" * 60)

    if args.only == "light":
        # 复用已有 PECS/ReAct 结果，只跑 ReAct-light
        ma = _load_existing("webshop_multi_agent.json")
        re = _load_existing("webshop_react_baseline.json")
        if not ma or not re:
            print("错误：需要先跑完整评测（python run_webshop.py）生成 PECS/ReAct 基线数据")
            print(f"  results/webshop_multi_agent.json: {'存在' if ma else '缺失'}")
            print(f"  results/webshop_react_baseline.json: {'存在' if re else '缺失'}")
            return

        print("\n>>> [1/3] PECS 多智能体框架（复用已有结果）")
        _print_result("PECS", ma)

        print("\n>>> [2/3] ReAct 纯 LLM（复用已有结果）")
        _print_result("ReAct", re)

        print("\n>>> [3/3] ReAct 轻量规则层（本次评测）")
        rl = evaluate_react_webshop_light()
        _print_result("ReAct-light", rl)
    else:
        print("\n>>> [1/3] PECS 多智能体框架（完整规则层）")
        ma = evaluate_webshop()
        _print_result("PECS", ma)

        print("\n>>> [2/3] ReAct 纯 LLM（无规则层）")
        re = evaluate_react_webshop()
        _print_result("ReAct", re)

        print("\n>>> [3/3] ReAct 轻量规则层（仅 Buy 规则）")
        rl = evaluate_react_webshop_light()
        _print_result("ReAct-light", rl)

    # 汇总三组对比
    pp_vs_react = _pp(ma["success_rate"], re["success_rate"])
    pp_vs_light = _pp(ma["success_rate"], rl["success_rate"])
    pp_light_vs_react = _pp(rl["success_rate"], re["success_rate"])

    token_vs_react = 0.0
    if re["avg_tokens_per_task"]:
        token_vs_react = round(
            (re["avg_tokens_per_task"] - ma["avg_tokens_per_task"])
            / re["avg_tokens_per_task"] * 100, 1
        )
    token_vs_light = 0.0
    if rl["avg_tokens_per_task"]:
        token_vs_light = round(
            (rl["avg_tokens_per_task"] - ma["avg_tokens_per_task"])
            / rl["avg_tokens_per_task"] * 100, 1
        )

    print("\n" + "=" * 60)
    print("  汇总（三组消融对比）")
    print(f"  PECS 完整规则层    : {ma['success_rate'] * 100:.1f}%  ({ma['avg_tokens_per_task']} tok)")
    print(f"  ReAct 轻量规则层  : {rl['success_rate'] * 100:.1f}%  ({rl['avg_tokens_per_task']} tok)")
    print(f"  ReAct 纯 LLM      : {re['success_rate'] * 100:.1f}%  ({re['avg_tokens_per_task']} tok)")
    print("-" * 60)
    print(f"  PECS vs ReAct(纯LLM)   : {pp_vs_react:+.1f} pp 成功, {token_vs_react:+.1f}% Token")
    print(f"  PECS vs ReAct-light    : {pp_vs_light:+.1f} pp 成功, {token_vs_light:+.1f}% Token")
    print(f"  ReAct-light vs ReAct   : {pp_light_vs_react:+.1f} pp（轻量规则的增量贡献）")
    print("=" * 60)

    save_results({
        "benchmark": "webshop",
        "mode": "real" if real else "local_mock",
        "multi_agent": {
            "rule_layer": "full (Buy + click[ASIN] search-loop breaking)",
            "success_rate": ma["success_rate"],
            "success_count": ma["success_count"],
            "total_samples": ma["total_samples"],
            "avg_tokens_per_task": ma["avg_tokens_per_task"],
        },
        "react_baseline": {
            "rule_layer": "none (pure LLM)",
            "success_rate": re["success_rate"],
            "success_count": re["success_count"],
            "total_samples": re["total_samples"],
            "avg_tokens_per_task": re["avg_tokens_per_task"],
        },
        "react_light": {
            "rule_layer": "light (Buy only, no search-loop breaking)",
            "success_rate": rl["success_rate"],
            "success_count": rl["success_count"],
            "total_samples": rl["total_samples"],
            "avg_tokens_per_task": rl["avg_tokens_per_task"],
        },
        "diff": {
            "pecs_vs_react_pp": pp_vs_react,
            "pecs_vs_light_pp": pp_vs_light,
            "light_vs_react_pp": pp_light_vs_react,
            "token_savings_pecs_vs_react_pct": token_vs_react,
            "token_savings_pecs_vs_light_pct": token_vs_light,
        },
    }, "webshop_run.json")

    print("\n结果已保存到 results/webshop_run.json")


if __name__ == "__main__":
    main()
