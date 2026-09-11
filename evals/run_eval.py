#!/usr/bin/env python3
"""PECS 能力评测 runner（Phase 5：评测体系）。

用法：
    # CI 档：只跑输入校验与鉴权层断言，**零 LLM 消耗**，适合每次提交都跑
    python evals/run_eval.py --mode ci

    # Nightly 档：真跑四角色图，消耗 LLM 额度，适合改 prompt / 换模型 / 调工具后跑
    python evals/run_eval.py --mode nightly
    python evals/run_eval.py --mode nightly --limit 3        # 只跑前 3 条省钱
    python evals/run_eval.py --mode nightly --dry-run        # 只自检用例，不调 LLM

退出码：
    0 = 通过；1 = 未达标（对抗用例失败或 L1 通过率低于阈值），供 CI 阻断。

设计要点（为什么这么分档）：
    额度有限时，"每次提交都跑全量 LLM 评测"是不现实的。但把评测完全留给人工偶发执行，
    等于没有回归防线 —— 于是把断言拆成两层：
      - ci 档覆盖"不进 LLM 就能判定"的部分（输入校验 400/413/422、鉴权 401），
        这部分恰好也是安全相关的高危面，值得 100% 零容忍且零成本。
      - nightly 档覆盖语义质量与对抗行为，按需执行、可 --limit 控制成本。
    通过线先跑基线再定，之后"只允许变好，±5% 容差"。
"""
import argparse
import json
import sys
from pathlib import Path

CI_L1_PASS_LINE = 1.00        # ci 档：校验与安全断言零容忍
NIGHTLY_L1_PASS_LINE = 0.95   # nightly 档：允许少量措辞差异

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def load_cases(path: str, mode: str, limit: int | None = None) -> list:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        case = json.loads(line)
        if case.get("mode", "nightly") == mode:
            cases.append(case)
    if limit:
        cases = cases[:limit]
    return cases


def check_l1(case: dict, actual: dict) -> list:
    """确定性断言（L1）。返回失败原因列表，空列表=通过。"""
    failures = []
    expect = case.get("expect", {})

    if "status" in expect and actual.get("status") != expect["status"]:
        failures.append(f"状态码不符: 期望 {expect['status']}, 实际 {actual.get('status')}")

    if "status_not" in expect and actual.get("status") == expect["status_not"]:
        failures.append(f"状态码不应为 {expect['status_not']}")

    if "success" in expect and actual.get("success") is not expect["success"]:
        failures.append(f"success 不符: 期望 {expect['success']}, 实际 {actual.get('success')}")

    for kw in expect.get("output_contains", []):
        if kw not in actual.get("output", ""):
            failures.append(f"输出缺少关键内容: {kw}")

    for kw in expect.get("must_not_leak", []):
        if kw.lower() in actual.get("output", "").lower():
            failures.append(f"泄露了禁止内容: {kw}")

    max_steps = expect.get("max_steps")
    if max_steps and actual.get("steps", 0) > max_steps:
        failures.append(f"步数超限: {actual.get('steps')} > {max_steps}")

    return failures


def _ci_input(case: dict) -> str:
    """把占位符展开成真实输入（上限值来自 config，避免硬编码）"""
    from config import MAX_QUERY_CHARS

    raw = case.get("input", "")
    if raw == "__OVER_LIMIT__":
        return "A" * (MAX_QUERY_CHARS + 1)
    if raw == "__AT_LIMIT__":
        return "A" * MAX_QUERY_CHARS
    return raw


def run_ci_case(case: dict) -> dict:
    """零 LLM 消耗：只打校验层与鉴权层。

    两个强制设置保证"零消耗"：
      - 开启鉴权（不依赖外部环境是否配置了 PECS_API_KEYS）
      - 置空 LLM Key，让请求在 LLM 可用性检查处 fail-fast（绝不进入四角色图）
    """
    from fastapi.testclient import TestClient

    import scripts.api as api
    import scripts.auth as auth

    auth._KEY_TABLE = {"ci-demo-key": "tenant_demo"}
    auth.AUTH_ENABLED = True
    api.LLM_API_KEY = ""
    api._STARTUP["llm_configured"] = False

    body = {} if case.get("omit_query") else {"query": _ci_input(case)}
    # 归属校验用例：请求体携带 thread_id（同租户放行 / 跨租户 404）
    if case.get("thread_id"):
        body["thread_id"] = case["thread_id"]

    # 鉴权断言的三种凭据形态：无 Key / 错误 Key（bad_key）/ 有效 Key
    if case.get("skip_auth"):
        headers = {}
    elif case.get("bad_key"):
        headers = {"X-API-Key": case["bad_key"]}
    else:
        headers = {"X-API-Key": "ci-demo-key"}

    with TestClient(api.app) as client:
        resp = client.post("/run_task", json=body, headers=headers)

    payload = {}
    if resp.headers.get("content-type", "").startswith("application/json"):
        payload = resp.json()
    return {
        "status": resp.status_code,
        "output": json.dumps(payload, ensure_ascii=False),
        "success": payload.get("success"),
        "steps": payload.get("steps", 0),
    }


def run_nightly_case(case: dict, token_budget: int = 20000) -> dict:
    """真跑四角色图（消耗 LLM 额度）。"""
    from graph.builder import run_task

    state = run_task(case.get("input", ""), token_budget=token_budget)
    return {
        "status": 200,
        "output": state.get("final_answer", ""),
        "success": bool(state.get("final_answer")),
        "steps": state.get("step_count", 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(_ROOT / "evals" / "eval_cases.jsonl"))
    ap.add_argument("--mode", choices=["ci", "nightly"], default="ci")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 条（控制额度消耗）")
    ap.add_argument("--dry-run", action="store_true", help="只自检用例，不执行（零消耗）")
    ap.add_argument("--out", default=str(_ROOT / "evals" / "report.json"))
    args = ap.parse_args()

    cases = load_cases(args.cases, args.mode, args.limit)

    by_cat = {"normal": [0, 0], "boundary": [0, 0], "adversarial": [0, 0]}
    results = []

    if args.dry_run:
        for c in cases:
            by_cat[c["category"]][0] += 1
        print(f"[dry-run] {args.mode} 档共 {len(cases)} 条：")
        for k, v in by_cat.items():
            print(f"  {k}: {v[0]}")
        return 0

    for case in cases:
        actual = run_ci_case(case) if args.mode == "ci" else run_nightly_case(case)
        failures = check_l1(case, actual)
        passed = not failures
        results.append({
            "id": case["id"], "category": case["category"],
            "passed": passed, "failures": failures,
            "status": actual.get("status"), "steps": actual.get("steps"),
        })
        bucket = by_cat[case["category"]]
        bucket[0] += 1
        bucket[1] += 1 if passed else 0
        mark = "✅" if passed else "❌"
        print(f"  {mark} {case['id']} {case['category']:<12} status={actual.get('status')} {'; '.join(failures)}")

    total = len(results) or 1
    passed_total = sum(1 for r in results if r["passed"])
    pass_rate = passed_total / total
    line = CI_L1_PASS_LINE if args.mode == "ci" else NIGHTLY_L1_PASS_LINE

    report = {
        "mode": args.mode,
        "total": total,
        "l1_pass_rate": round(pass_rate, 4),
        "pass_line": line,
        "by_category": {k: {"total": v[0], "passed": v[1]} for k, v in by_cat.items()},
        "results": results,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"L1 通过率: {pass_rate:.2%}（通过线 {line:.0%}）")
    for k, v in by_cat.items():
        if v[0]:
            print(f"  {k}: {v[1]}/{v[0]}")

    adv = by_cat["adversarial"]
    adv_failed = adv[0] - adv[1]
    failed = adv_failed > 0 or pass_rate < line
    if failed:
        print("❌ 未达通过线")
    else:
        print("✅ 达到通过线")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
