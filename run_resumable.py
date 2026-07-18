"""
可恢复评测驱动（应对沙箱进程存活限制）

设计目标：
  本环境会在长耗时 Python 进程（>~120s）脱离前台后将其回收，导致端到端
  评测无法一次跑完。本脚本利用 LangGraph 的 SQLite 检查点，把一次完整任务
  拆成「多次短进程调用」，每次只推进若干节点（受 120s 前台窗口约束），进程
  被杀也不丢进度，下次用同一 thread_id 续跑，直到任务完成。

用法（每次一个 Bash 调用，进程短、必返回）：
  python run_resumable.py <task_id> [--db results/ckpt.db] [--budget 50000] [--no-heuristics]

  - 第一次调用：用 query 初始化并推进图，落盘检查点。
  - 后续调用（同 task_id）：从检查点续跑，继续推进。
  - 当 stdout 出现 "DONE" 行时，任务已完成，final_answer 已打印。

状态文件：results/_resume_<task_id>.json 记录每次推进的进度摘要。
"""
import sys
import os
import json
import time
import argparse

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from benchmarks.gaia_eval import GAIA_L1_SAMPLES, evaluate_answer
from graph.builder import run_task, resume_task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--db", default=None, help="检查点 db 路径；默认按 task_id 隔离 (results/ckpt_<task_id>.db)")
    ap.add_argument("--budget", type=int, default=50000)
    ap.add_argument("--no-heuristics", action="store_true")
    args = ap.parse_args()

    task = next((x for x in GAIA_L1_SAMPLES if x["task_id"] == args.task_id), None)
    if not task:
        print("UNKNOWN_TASK", args.task_id)
        return

    use_heuristics = not args.no_heuristics
    db = args.db or f"results/ckpt_{args.task_id}.db"
    os.makedirs(os.path.dirname(db), exist_ok=True)

    t0 = time.time()
    if os.path.exists(db):
        # 已有检查点 → 续跑（复用断点，不重新传 query）
        try:
            final = resume_task(args.task_id, db)
        except Exception as e:
            # 续跑失败（例如检查点损坏）→ 退回用 query 重新初始化
            print("RESUME_FAIL:", repr(e)[:200], "-> reinit")
            final = run_task(task["question"], token_budget=args.budget,
                             use_heuristics=use_heuristics,
                             thread_id=args.task_id, checkpoint_db=db)
    else:
        # 首次运行
        final = run_task(task["question"], token_budget=args.budget,
                         use_heuristics=use_heuristics,
                         thread_id=args.task_id, checkpoint_db=db)

    dt = time.time() - t0

    # final 可能是 AgentState 或 dict
    if hasattr(final, "model_dump"):
        fs = final.model_dump()
    else:
        fs = dict(final)

    fa = fs.get("final_answer", "")
    tok = fs.get("token_used", 0)
    # 错误特征词：命中说明 final_answer 是报错文本，不能当作有效完成
    _FAIL_MARKERS = (
        "Traceback", "NameError", "TypeError", "SyntaxError",
        "执行错误", "[LLM调用失败]", "错误", "未定义",
    )
    fa_is_error = any(m in str(fa) for m in _FAIL_MARKERS)
    # 是否到达 END：
    #  - 有非空 final_answer 且不是报错文本 → 视为完成
    #  - 或迭代次数达到上限（强制终止，由上层评估判定对错）
    # 注意：报错文本（如 NameError）不得被判定为完成，否则错误答案被冻结
    done = (bool(fa) and not fa_is_error) or fs.get("iteration", 0) >= 5

    # 写进度摘要（供下次判断是否还需续跑）
    summary = {
        "task_id": args.task_id,
        "final_answer": fa,
        "token_used": tok,
        "done": done,
        "elapsed_this_call": round(dt, 1),
        "last_update": time.strftime("%H:%M:%S"),
    }
    with open(f"results/_resume_{args.task_id}.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    if done:
        # 评估对错
        gt = task.get("ground_truth", "")
        correct, _ = evaluate_answer(fa, gt) if gt else (None, "")
        print("DONE task_id=%s elapsed=%.1fs tokens=%s final_answer=%s" % (
            args.task_id, dt, tok, fa[:200]))
        print("GROUND_TRUTH=%s CORRECT=%s" % (gt, correct))
    else:
        print("PROGRESS task_id=%s elapsed=%.1fs tokens=%s final_answer_so_far=%s" % (
            args.task_id, dt, tok, fa[:120]))
        print("NOT_DONE -> 请再次运行同一命令续跑")


if __name__ == "__main__":
    main()
