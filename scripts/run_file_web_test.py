"""
专项测试：文件解析 + 网页浏览题（gaia_l1_029 ~ gaia_l1_033）

验证 Phase 3 新增工具的效果。
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from benchmarks.gaia_eval import GAIA_L1_SAMPLES, evaluate_answer
from graph.builder import run_task

# 只测试新增的文件/网页题
NEW_IDS = {"gaia_l1_029", "gaia_l1_030", "gaia_l1_031", "gaia_l1_032", "gaia_l1_033"}


def main():
    samples = [s for s in GAIA_L1_SAMPLES if s["task_id"] in NEW_IDS]
    print("=" * 60)
    print(f"文件/网页题专项测试（{len(samples)} 题）")
    print("=" * 60)

    results = []
    correct = 0
    total_tokens = 0

    for i, s in enumerate(samples):
        q = s["question"]
        gt = s["answer"]
        print(f"\n[{i+1}/{len(samples)}] {s['task_id']}: {q[:60]}")

        try:
            st = run_task(q, token_budget=50000, use_heuristics=True)
            pred = st.get("final_answer", "")
            toks = st.get("token_used", 0)
            logs = st.get("logs", [])
        except Exception as e:
            pred = f"[ERROR] {e}"
            toks = 0
            logs = []

        ok = evaluate_answer(pred, gt)
        if ok:
            correct += 1
        total_tokens += toks

        results.append({
            "id": s["task_id"],
            "question": q,
            "ground_truth": gt,
            "predicted": pred,
            "correct": ok,
            "tokens": toks,
        })

        print(f"  预测: {pred[:80]}")
        print(f"  正确: {ok} | Token: {toks}")
        # 打印关键日志
        for log in logs:
            if any(k in log for k in ["Planner", "Executor", "file_parse", "web_browse", "Synthesizer"]):
                print(f"    {log}")

        if i < len(samples) - 1:
            time.sleep(1)

    acc = correct / len(samples) if samples else 0
    avg = total_tokens / len(samples) if samples else 0
    print("\n" + "=" * 60)
    print(f"文件/网页题准确率: {correct}/{len(samples)} = {acc:.1%}")
    print(f"平均Token: {avg:.0f}")
    print("=" * 60)

    with open("results/file_web_test.json", "w", encoding="utf-8") as f:
        json.dump({
            "accuracy": round(acc, 4),
            "correct": correct,
            "total": len(samples),
            "avg_tokens": round(avg),
            "details": results,
        }, f, ensure_ascii=False, indent=2)
    print("已保存 results/file_web_test.json")


if __name__ == "__main__":
    main()
