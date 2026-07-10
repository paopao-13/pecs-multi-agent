"""
Demo: PECS 多智能体 vs ReAct 单智能体对比演示

在相同任务上对比 PECS 框架和 ReAct 基线的执行过程，
直观展示多角色分工在准确率和 Token 消耗上的优势。

运行方式：
    cd pecs-multi-agent
    python demos/pecs_vs_react_demo.py

注意：完整对比需要配置 DEEPSEEK_API_KEY。
未配置时仅展示框架结构和预期对比结果。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from graph.builder import run_task


# 预期的对比结果（来自 28 道 GAIA Mock 样例集实测）
EXPECTED_RESULTS = {
    "total_samples": 28,
    "pecs": {
        "accuracy": "100% (28/28)",
        "avg_token": 53,
        "total_token": 1484,
    },
    "react": {
        "accuracy": "78.6% (22/28)",
        "avg_token": 402,
        "total_token": 11256,
    },
}


def run_comparison(task: str):
    """在单个任务上对比 PECS 和 ReAct"""
    api_key = os.getenv("DEEPSEEK_API_KEY", "")

    print(f"\n{'=' * 60}")
    print(f"  任务: {task}")
    print(f"{'=' * 60}")

    if not api_key:
        print("\n  [INFO] 未配置 DEEPSEEK_API_KEY，跳过实时对比")
        print("  [INFO] 以下展示框架执行（启发式路径）和预期对比数据\n")
        # 用启发式跑一个任务展示流程
        result = run_task(task)
        print(f"  PECS 执行结果:")
        print(f"    答案: {result.get('final_answer', 'N/A')[:100]}")
        print(f"    Token: {result.get('token_used', 0)}")
        return

    # 有 API Key 时跑真实对比
    print("\n  [PECS 框架执行中...]")
    pecs_result = run_task(task)
    pecs_token = pecs_result.get("token_used", 0)
    pecs_answer = pecs_result.get("final_answer", "")

    print(f"  PECS 结果: {pecs_answer[:100]}")
    print(f"  PECS Token: {pecs_token}")

    # ReAct 基线（简化版：单轮 LLM 推理）
    print("\n  [ReAct 基线执行中...]")
    from benchmarks.react_baseline import run_react_task
    react_result = run_react_task(task)
    react_token = react_result.get("token_used", 0)
    react_answer = react_result.get("final_answer", "")

    print(f"  ReAct 结果: {react_answer[:100]}")
    print(f"  ReAct Token: {react_token}")

    # 对比
    print(f"\n  --- 对比 ---")
    print(f"  Token 节省: {react_token - pecs_token} ({(1 - pecs_token/max(react_token,1))*100:.1f}%)")


def show_summary():
    """展示批量评测的汇总对比数据"""
    print(f"\n{'=' * 60}")
    print(f"  批量评测汇总（{EXPECTED_RESULTS['total_samples']} 道 GAIA Mock 样例）")
    print(f"{'=' * 60}\n")

    pecs = EXPECTED_RESULTS["pecs"]
    react = EXPECTED_RESULTS["react"]

    print(f"  {'指标':<20} {'ReAct 基线':>15} {'PECS 框架':>15} {'提升':>15}")
    print(f"  {'─' * 65}")
    print(f"  {'准确率':<20} {react['accuracy']:>15} {pecs['accuracy']:>15} {'+21.4pp':>15}")
    print(f"  {'平均Token/任务':<20} {react['avg_token']:>15} {pecs['avg_token']:>15} {'-86.8%':>15}")
    print(f"  {'总Token消耗':<20} {react['total_token']:>15} {pecs['total_token']:>15} {'-9782':>15}")

    print(f"""
  消融实验关键发现：
  ┌──────────────────────────────────────────────────────────────┐
  │ single_agent (纯ReAct)  准确率 82.1%  Token 1111           │
  │ full_pecs (完整四角色)   准确率 100%   Token 53             │
  │ → 多角色分工提升准确率 17.9pp，Token 降低 95.2%             │
  └──────────────────────────────────────────────────────────────┘

  Token 降本口径说明：
  - 整体架构降本 86.8%（含启发式兜底层贡献）
  - 纯预算调度模块降本 11.7%（有/无预算调度对照）
  - Mock样例启发式命中率高，官方数据集上调度模块贡献占比将上升
""")


def main():
    print("=" * 60)
    print("  PECS 多智能体 vs ReAct 单智能体 对比演示")
    print("=" * 60)

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if api_key:
        print("\n  [INFO] 检测到 DEEPSEEK_API_KEY，将执行实时对比")
    else:
        print("\n  [INFO] 未检测到 DEEPSEEK_API_KEY，展示框架演示 + 预期数据")

    # 单任务对比
    run_comparison("计算2的100次方")

    # 展示批量汇总
    show_summary()

    print("=" * 60)
    print("  Demo 完成！")
    print("=" * 60)
    print("""
  完整对比复现方法：
  1. 配置 DEEPSEEK_API_KEY
  2. 运行批量评测：
     python -m benchmarks.gaia_eval --level 1
     python -m benchmarks.react_baseline --level 1
  3. 或一键运行消融实验：
     bash scripts/run_all_ablation.sh
""")


if __name__ == "__main__":
    main()
