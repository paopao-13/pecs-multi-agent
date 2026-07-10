"""
Demo: Token 预算三级降级演示

展示 PECS 框架的 Token 预算感知调度器在不同预算阈值下的降级行为：
  70% 预算 → Critic 跳过低风险步骤验证
  85% 预算 → Planner 合并剩余步骤
  95% 预算 → Synthesizer 强制用已有结果输出

同时展示角色独立配额机制：
  每个角色有独立 Token 配额，单角色超限时触发角色级降级

运行方式：
    cd pecs-multi-agent
    python demos/token_budget_demo.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from graph.token_budget import (
    get_budget_policy,
    get_degrade_level,
    record_token_usage,
    check_role_budget,
    TokenBudgetManager,
)
from config import DEFAULT_TOKEN_BUDGET, BUDGET_ALLOCATION


def demo_global_degradation():
    """演示全局预算三级降级"""
    print("\n" + "=" * 60)
    print("  Part 1: 全局预算三级降级")
    print("=" * 60)
    print(f"\n  总预算: {DEFAULT_TOKEN_BUDGET} tokens")
    print(f"  降级阈值: 70% / 85% / 95%\n")

    # 模拟不同消耗阶段
    scenarios = [
        ("正常阶段", 0),
        ("消耗 30%", 15000),
        ("触发降级1 (70%)", 36000),
        ("触发降级2 (85%)", 43000),
        ("触发降级3 (95%)", 48000),
    ]

    print(f"  {'阶段':<25} {'已消耗':>8} {'剩余':>8} {'使用率':>8} {'降级级':>6} {'跳过Critic':>10} {'合并步骤':>8} {'强制综合':>8}")
    print("  " + "─" * 90)

    for name, token_used in scenarios:
        state = {"token_used": token_used, "token_budget": DEFAULT_TOKEN_BUDGET}
        policy = get_budget_policy(state)
        level = get_degrade_level(token_used, DEFAULT_TOKEN_BUDGET)
        remaining = DEFAULT_TOKEN_BUDGET - token_used
        ratio = token_used / DEFAULT_TOKEN_BUDGET * 100

        print(f"  {name:<25} {token_used:>8} {remaining:>8} {ratio:>7.1f}% {level:>6} "
              f"{'是' if policy['skip_low_risk_critic'] else '否':>10} "
              f"{'是' if policy['merge_steps'] else '否':>8} "
              f"{'是' if policy['force_synthesize'] else '否':>8}")


def demo_role_quota():
    """演示角色独立配额"""
    print("\n" + "=" * 60)
    print("  Part 2: 角色独立配额")
    print("=" * 60)
    print(f"\n  总预算: {DEFAULT_TOKEN_BUDGET} tokens")
    print(f"\n  角色配额分配:")

    for role, ratio in BUDGET_ALLOCATION.items():
        quota = int(DEFAULT_TOKEN_BUDGET * ratio)
        print(f"    {role:15s}: {quota:>6} tokens ({ratio*100:.0f}%)")

    # 模拟角色消耗
    print(f"\n  模拟场景：Executor 过度消耗 Token")
    print("  " + "─" * 70)

    manager = TokenBudgetManager(DEFAULT_TOKEN_BUDGET)

    # 正常消耗
    for role, tokens in [("planner", 3000), ("executor", 15000), ("critic", 5000)]:
        manager.consume(role, tokens)
        ratio = manager.get_role_usage_ratio(role)
        exceeded = not manager.check_role_quota(role)
        action = manager.get_role_degrade_action(role) if exceeded else "正常"
        print(f"  {role:15s} 消耗 {tokens:>6} | 使用率 {ratio*100:>5.1f}% | 状态: {action}")

    # Executor 继续消耗，触发角色级降级
    print(f"\n  Executor 再消耗 8000 tokens（总计 23000，超过 25000 配额的 92%）...")
    manager.consume("executor", 8000)
    ratio = manager.get_role_usage_ratio("executor")
    exceeded = not manager.check_role_quota("executor")
    action = manager.get_role_degrade_action("executor") if exceeded else "正常"
    print(f"  {'executor':15s} 消耗 {8000:>6} | 使用率 {ratio*100:>5.1f}% | 状态: {action}")

    # 再消耗到超限
    print(f"\n  Executor 再消耗 3000 tokens（总计 26000，超过 25000 配额）...")
    manager.consume("executor", 3000)
    ratio = manager.get_role_usage_ratio("executor")
    exceeded = not manager.check_role_quota("executor")
    action = manager.get_role_degrade_action("executor") if exceeded else "正常"
    print(f"  {'executor':15s} 消耗 {3000:>6} | 使用率 {ratio*100:>5.1f}% | 状态: {action}")

    # 展示预算事件
    print(f"\n  预算事件记录 ({len(manager.budget_events)} 条):")
    for event in manager.budget_events:
        if event.get("event") == "role_quota_exceeded":
            print(f"    [{event['role']}] 配额超限! "
                  f"已用 {event['role_used']}/{event['role_budget']} "
                  f"→ 降级动作: {event['degrade_action']}")


def demo_route_integration():
    """演示角色配额在路由函数中的集成"""
    print("\n" + "=" * 60)
    print("  Part 3: 角色配额接入路由函数")
    print("=" * 60)

    # 模拟 Executor 超配额时的路由决策
    print("\n  场景：Executor Token 消耗超过独立配额")
    state = {
        "token_budget": 50000,
        "token_used": 28000,
        "role_token_used": {
            "planner": 2000,
            "executor": 26000,  # 超过 25000 配额
            "critic": 0,
            "synthesizer": 0,
        },
    }

    quota = check_role_budget(state, "executor")
    print(f"\n  Executor 配额检查:")
    print(f"    已消耗: {state['role_token_used']['executor']} tokens")
    print(f"    配额上限: {int(50000 * 0.50)} tokens")
    print(f"    超出: {quota['exceeded']}")
    print(f"    剩余: {quota['remaining']} tokens")
    print(f"    降级动作: {quota['action']}")
    print(f"\n  路由决策: Executor 超配额 → 强制去 Synthesizer（跳过后续执行）")

    # 模拟 Critic 超配额
    print(f"\n  场景：Critic Token 消耗超过独立配额")
    state2 = {
        "token_budget": 50000,
        "token_used": 12000,
        "role_token_used": {
            "planner": 2000,
            "executor": 0,
            "critic": 11000,  # 超过 10000 配额
            "synthesizer": 0,
        },
    }
    quota2 = check_role_budget(state2, "critic")
    print(f"\n  Critic 配额检查:")
    print(f"    已消耗: {state2['role_token_used']['critic']} tokens")
    print(f"    配额上限: {int(50000 * 0.20)} tokens")
    print(f"    超出: {quota2['exceeded']}")
    print(f"    降级动作: {quota2['action']}")
    print(f"\n  路由决策: Critic 超配额 → 跳过评审，直接执行下一步或综合")


def main():
    print("=" * 60)
    print("  PECS Token 预算感知调度演示")
    print("  三级降级 + 角色独立配额")
    print("=" * 60)

    demo_global_degradation()
    demo_role_quota()
    demo_route_integration()

    print("\n" + "=" * 60)
    print("  Demo 完成！")
    print("=" * 60)
    print("""
  总结：
  1. 全局三级降级：70%跳过低风险Critic → 85%合并步骤 → 95%强制综合
  2. 角色独立配额：单角色超限时触发角色级降级，无需等待全局预算耗尽
  3. 路由集成：角色配额检查嵌入4个路由函数 + Planner节点
  4. 双层防护：全局降级 + 角色配额，确保单任务成本可控
""")


if __name__ == "__main__":
    main()
