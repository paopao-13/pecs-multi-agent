"""
Demo: 零配置快速体验（无需 API Key）

本 Demo 展示 PECS 框架在无 DeepSeek API Key 的情况下，
通过启发式兜底层 + Python 安全沙箱完成任务执行。

适用场景：
- 无 Key 快速演示（无需提前配 Key）
- CI/CD 环境验证框架可运行性
- 新用户 clone 后立即体验

运行方式：
    cd pecs-multi-agent
    python demos/quickstart_no_api.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from graph.builder import build_graph, create_initial_state


def run_single_task(query: str, token_budget: int = 50000):
    """运行单个任务并打印执行过程"""
    print(f"\n{'=' * 60}")
    print(f"  任务: {query}")
    print(f"  预算: {token_budget} tokens")
    print(f"{'=' * 60}\n")

    app = build_graph(token_budget=token_budget)
    initial_state = create_initial_state(query, token_budget=token_budget)
    result = app.invoke(initial_state)

    # 打印执行日志
    logs = result.get("logs", [])
    print("--- 执行日志 ---")
    for log in logs:
        print(f"  {log}")

    # 打印关键结果
    print(f"\n--- 执行结果 ---")
    print(f"  最终答案: {result.get('final_answer', 'N/A')}")
    print(f"  Token 消耗: {result.get('token_used', 0)}")
    print(f"  迭代轮次: {result.get('iteration', 0)}")

    # 打印调度决策
    decisions = result.get("scheduler_decisions", [])
    if decisions:
        print(f"\n--- 调度决策 ({len(decisions)} 条) ---")
        for d in decisions:
            print(f"  [{d.get('actor', '?')}] {d.get('decision', '?')} - {d.get('reason', '?')}")

    # 打印角色 Token 明细
    role_tokens = result.get("role_token_used", {})
    if any(role_tokens.values()):
        print(f"\n--- 角色 Token 明细 ---")
        for role, tokens in role_tokens.items():
            print(f"  {role:15s}: {tokens} tokens")

    return result


def main():
    print("=" * 60)
    print("  PECS 多智能体框架 - 零配置快速体验")
    print("  无需 API Key，启发式兜底层 + Python 安全沙箱")
    print("=" * 60)

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if api_key:
        print("\n  [INFO] 检测到 DEEPSEEK_API_KEY，将使用 LLM 路径")
    else:
        print("\n  [INFO] 未检测到 DEEPSEEK_API_KEY，将使用启发式兜底路径")
        print("  [INFO] 启发式路径不消耗 Token，适合快速演示")

    # 任务1：大数计算（启发式直接走 Python 沙箱）
    print("\n" + "─" * 60)
    print("  任务 1/3：大数计算（启发式路径，零 Token 消耗）")
    print("─" * 60)
    run_single_task("计算2的100次方")

    # 任务2：阶乘计算
    print("\n" + "─" * 60)
    print("  任务 2/3：阶乘计算（Python 安全沙箱执行）")
    print("─" * 60)
    run_single_task("17的5次方是多少？")

    # 任务3：Fibonacci 数列
    print("\n" + "─" * 60)
    print("  任务 3/3：Fibonacci 数列（验证沙箱多步执行）")
    print("─" * 60)
    run_single_task("Fibonacci数列的第20项是多少？")

    print("\n" + "=" * 60)
    print("  Demo 完成！")
    print("=" * 60)
    print("""
  说明：
  1. 以上任务均通过启发式兜底层识别为计算类任务，直接走 Python 安全沙箱执行
  2. 启发式路径不调用 LLM，Token 消耗为 0
  3. 如需体验完整的 LLM 规划路径，请配置 DEEPSEEK_API_KEY 后运行：
     python demos/demo_batch_task.py
  4. 如需查看安全沙箱拦截演示，运行：
     python demos/security_sandbox_demo.py
""")


if __name__ == "__main__":
    main()
