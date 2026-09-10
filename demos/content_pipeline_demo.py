"""
Demo: AI 内容生成 Pipeline（无需 API Key）

场景（对齐真实业务：AI 内容生成后端）
  主题 → 单条生成 → 批量生成（含 token 预算三级降级）→ LLM 自动评测
       → A/B 选优 → 成本归因

本 Demo 用 tools/content_pipeline.py 的确定性 Mock 工具，不发起任何真实
LLM 调用，因此**无 API Key、无网络也能完整跑通**。

关于注册时机：
  这 4 个工具只在 RUN_MODE=business 时进入 TOOL_REGISTRY（约束 K4，
  避免污染 GAIA/WebShop 评测的可用工具集）。本 Demo 直接调用工具函数，
  因此 eval 模式下同样可跑；但只有 business 模式下 Planner 才「看得见」它们。

运行方式：
    cd pecs-multi-agent
    python demos/content_pipeline_demo.py
    或（展示已注册状态）：
    RUN_MODE=business python demos/content_pipeline_demo.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from metrics.cost_attribution import render_report  # noqa: E402
from tools.content_pipeline import (  # noqa: E402
    ab_select,
    batch_generate,
    generate_content,
    llm_judge,
)


def _hr(title: str) -> None:
    print("\n" + "=" * 68)
    print(f"  {title}")
    print("=" * 68)


def main() -> None:
    print("PECS · AI 内容生成 Pipeline Demo（Mock，无 API 依赖）")
    print(f"当前 RUN_MODE = {config.RUN_MODE}"
          f"（business 时内容工具会被注册进工具表）")
    try:
        from tools import TOOL_REGISTRY
        content_tools = [t for t in TOOL_REGISTRY if t in
                         ("generate_content", "batch_generate", "llm_judge", "ab_select")]
        print(f"已注册的内容工具：{content_tools if content_tools else '（无，eval 模式不注册）'}")
    except Exception as exc:  # noqa: BLE001
        print(f"读取工具表失败：{exc}")

    # ---------- Step 1：单条生成 ----------
    _hr("Step 1 · 单条文案生成")
    draft = generate_content({"topic": "PECS 多智能体框架", "style": "种草"})
    print(draft)

    # ---------- Step 2：批量生成 + 预算降级 ----------
    _hr("Step 2 · 批量生成（观察 token 预算三级降级）")
    items = [
        {"topic": "多智能体协作", "style": "专业"},
        {"topic": "Token 成本控制", "style": "严谨"},
        {"topic": "工具容错与熔断", "style": "活泼"},
    ]
    for budget in (50000, 400, 300):
        print(f"\n--- 任务预算 = {budget} tokens ---")
        print(batch_generate({"items": items, "budget": budget}))

    # ---------- Step 3：LLM 自动评测 ----------
    _hr("Step 3 · LLM 自动评测（LLM-as-judge）")
    print(llm_judge({"content": draft}))

    # ---------- Step 4：A/B 选优 ----------
    _hr("Step 4 · A/B 选优")
    variants = [
        generate_content({"topic": "PECS 多智能体框架", "style": "专业"}),
        generate_content({"topic": "PECS 多智能体框架", "style": "种草"}),
        generate_content({"topic": "PECS 多智能体框架", "style": "严谨"}),
    ]
    print(ab_select({"variants": variants}))

    # ---------- Step 5：成本归因（与 Day4 成本归因打通）----------
    _hr("Step 5 · 成本归因（把这次 pipeline 的钱花在哪讲清楚）")
    # 用本 Demo 产出的内容合成一个任务状态，复用真实归因逻辑
    simulated_state = {
        "token_used": 1860,
        "token_budget": 50000,
        "step_count": 4,
        "iteration": 0,
        "role_token_used": {"planner": 180, "executor": 1180, "critic": 320, "synthesizer": 180},
        "budget_events": [
            {"role": "planner", "tokens": 180, "iteration": 0, "degrade_level": 0},
            {"role": "executor", "tokens": 620, "iteration": 0, "degrade_level": 0},
            {"role": "executor", "tokens": 560, "iteration": 0, "degrade_level": 0},
            {"role": "critic", "tokens": 320, "iteration": 0, "degrade_level": 0},
            {"role": "synthesizer", "tokens": 180, "iteration": 0, "degrade_level": 0},
        ],
        "results": [
            {"action": "generate_content", "result": draft},
            {"action": "batch_generate", "result": "批量结果"},
            {"action": "llm_judge", "result": "评测结果"},
            {"action": "ab_select", "result": "选优结果"},
        ],
    }
    print(render_report(simulated_state))

    print("\n完成。真实接入时把 tools/content_pipeline.py 的 _simulate_llm 换成 LLM 网关即可，"
          "工具签名与返回契约不变。")


if __name__ == "__main__":
    main()
