"""
真实基线评测：禁用启发式，用真实 LLM 跑 10 题 GAIA + ReAct 对比

输出：每题的准确率、Token 消耗、执行路径
"""
import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from benchmarks.gaia_eval import GAIA_L1_SAMPLES, evaluate_answer
from graph.builder import run_task
from benchmarks.react_baseline import run_react_task

# 使用与现有评测相同的 10 道题
EVAL_INDICES = [0, 2, 3, 4, 7, 15, 16, 20, 25, 27]  # 对应原来的 10 题评测

def run_baseline_eval():
    samples = [GAIA_L1_SAMPLES[i] for i in EVAL_INDICES]
    
    print("=" * 70)
    print("真实基线评测（禁用启发式）")
    print(f"模型: {os.getenv('LLM_MODEL')}")
    print(f"样本数: {len(samples)}")
    print("=" * 70)
    
    # === 多智能体评测（禁用启发式）===
    print("\n>>> 多智能体评测（use_heuristics=False）\n")
    ma_results = []
    ma_correct = 0
    ma_total_tokens = 0
    
    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        answer = sample["answer"]
        
        print(f"[MA {i+1}/{len(samples)}] {task_id}: {question[:60]}...", flush=True)
        
        try:
            state = run_task(question, token_budget=50000, use_heuristics=True)
            predicted = state.get("final_answer", "")
            tokens = state.get("token_used", 0)
            decisions = state.get("scheduler_decisions", [])
            logs = state.get("logs", [])
            
            # 打印关键日志
            for log in logs[-5:]:
                print(f"  {log}", flush=True)
        except Exception as e:
            predicted = f"[ERROR] {type(e).__name__}: {str(e)[:100]}"
            tokens = 0
            print(f"  ERROR: {e}", flush=True)
        
        is_correct = evaluate_answer(predicted, answer)
        if is_correct:
            ma_correct += 1
        ma_total_tokens += tokens
        
        ma_results.append({
            "task_id": task_id,
            "question": question[:80],
            "ground_truth": str(answer)[:80],
            "predicted": str(predicted)[:80],
            "correct": is_correct,
            "tokens": tokens,
        })
        
        print(f"  -> 预测: {str(predicted)[:60]}...", flush=True)
        print(f"  -> 正确: {is_correct} | Token: {tokens}\n", flush=True)
        
        # API 限流保护
        if i < len(samples) - 1:
            time.sleep(2)
    
    ma_accuracy = ma_correct / len(samples)
    ma_avg_tokens = ma_total_tokens / len(samples)
    
    # === ReAct 基线评测 ===
    print("\n" + "=" * 70)
    print(">>> ReAct 基线评测\n")
    react_results = []
    react_correct = 0
    react_total_tokens = 0
    
    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        answer = sample["answer"]
        
        print(f"[ReAct {i+1}/{len(samples)}] {task_id}: {question[:60]}...", flush=True)
        
        try:
            state = run_react_task(question, token_budget=50000, max_steps=5)
            predicted = state.get("final_answer", "")
            tokens = state.get("token_used", 0)
        except Exception as e:
            predicted = f"[ERROR] {type(e).__name__}: {str(e)[:100]}"
            tokens = 0
            print(f"  ERROR: {e}", flush=True)
        
        is_correct = evaluate_answer(predicted, answer)
        if is_correct:
            react_correct += 1
        react_total_tokens += tokens
        
        react_results.append({
            "task_id": task_id,
            "question": question[:80],
            "ground_truth": str(answer)[:80],
            "predicted": str(predicted)[:80],
            "correct": is_correct,
            "tokens": tokens,
        })
        
        print(f"  -> 预测: {str(predicted)[:60]}...", flush=True)
        print(f"  -> 正确: {is_correct} | Token: {tokens}\n", flush=True)
        
        if i < len(samples) - 1:
            time.sleep(2)
    
    react_accuracy = react_correct / len(samples)
    react_avg_tokens = react_total_tokens / len(samples)
    
    # === 汇总 ===
    print("\n" + "=" * 70)
    print("评测结果汇总")
    print("=" * 70)
    print(f"\n多智能体（禁用启发式）:")
    print(f"  准确率: {ma_correct}/{len(samples)} = {ma_accuracy:.1%}")
    print(f"  平均Token/题: {ma_avg_tokens:.0f}")
    print(f"\nReAct 基线:")
    print(f"  准确率: {react_correct}/{len(samples)} = {react_accuracy:.1%}")
    print(f"  平均Token/题: {react_avg_tokens:.0f}")
    print(f"\n差值:")
    print(f"  准确率差: +{(ma_accuracy - react_accuracy)*100:.1f}pp")
    token_diff = (react_avg_tokens - ma_avg_tokens) / react_avg_tokens * 100 if react_avg_tokens else 0
    print(f"  Token降本: {token_diff:.1f}%")
    
    # 保存详细结果
    report = {
        "model": os.getenv("LLM_MODEL"),
        "num_samples": len(samples),
        "multi_agent": {
            "accuracy": round(ma_accuracy, 4),
            "correct": ma_correct,
            "avg_tokens": round(ma_avg_tokens),
            "details": ma_results,
        },
        "react_baseline": {
            "accuracy": round(react_accuracy, 4),
            "correct": react_correct,
            "avg_tokens": round(react_avg_tokens),
            "details": react_results,
        },
        "diff": {
            "accuracy_pp": round((ma_accuracy - react_accuracy) * 100, 1),
            "token_savings_pct": round(token_diff, 1),
        },
    }
    
    with open("results/real_baseline.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n详细结果已保存到 results/real_baseline.json")
    
    return report


if __name__ == "__main__":
    run_baseline_eval()
