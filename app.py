"""
Flask Web 应用入口

提供两个核心功能：
1. 单任务执行：输入一个问题，看四个Agent协作过程和最终答案
2. 基准评估：在GAIA数据集上跑评估，对比多Agent vs ReAct基线
"""
import os
import sys
import json
import time
from flask import Flask, render_template, request, jsonify

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from graph.builder import build_graph, create_initial_state
from graph.token_budget import TokenBudgetManager
from benchmarks.gaia_eval import evaluate_gaia, GAIA_L1_SAMPLES
from benchmarks.react_baseline import evaluate_react_gaia, run_react_task
from benchmarks.report import run_sample_report
from benchmarks.webshop_eval import evaluate_react_webshop, evaluate_webshop
from config import DEFAULT_TOKEN_BUDGET, FLASK_HOST, FLASK_PORT, FLASK_DEBUG

app = Flask(__name__, static_folder="static")


@app.route("/")
def index():
    """主页面"""
    return render_template("index.html")


@app.route("/api/run_task", methods=["POST"])
def api_run_task():
    """
    执行单个任务

    请求: {"query": "用户问题", "token_budget": 50000}
    响应: {"final_answer": "...", "logs": [...], "token_used": ..., ...}
    """
    data = request.json
    query = data.get("query", "")
    token_budget = data.get("token_budget", DEFAULT_TOKEN_BUDGET)

    if not query:
        return jsonify({"error": "请输入问题"}), 400

    try:
        # 构建并执行图
        compiled_graph = build_graph(token_budget)
        initial_state = create_initial_state(query, token_budget)
        final_state = compiled_graph.invoke(initial_state)

        return jsonify({
            "success": True,
            "query": query,
            "final_answer": final_state.get("final_answer", ""),
            "token_used": final_state.get("token_used", 0),
            "token_budget": token_budget,
            "iteration": final_state.get("iteration", 0),
            "plan": final_state.get("plan", []),
            "results": final_state.get("results", []),
            "critic_scores": final_state.get("critic_scores", []),
            "role_token_used": final_state.get("role_token_used", {}),
            "budget_events": final_state.get("budget_events", []),
            "scheduler_decisions": final_state.get("scheduler_decisions", []),
            "logs": final_state.get("logs", []),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/run_react", methods=["POST"])
def api_run_react():
    """
    用 ReAct 基线执行同一个任务（用于对比）

    请求: {"query": "用户问题", "token_budget": 50000}
    """
    data = request.json
    query = data.get("query", "")
    token_budget = data.get("token_budget", DEFAULT_TOKEN_BUDGET)

    if not query:
        return jsonify({"error": "请输入问题"}), 400

    try:
        result = run_react_task(query, token_budget)
        return jsonify({
            "success": True,
            "query": query,
            "final_answer": result.get("final_answer", ""),
            "token_used": result.get("token_used", 0),
            "token_budget": token_budget,
            "logs": result.get("logs", []),
            "steps": result.get("steps", []),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/eval_gaia", methods=["POST"])
def api_eval_gaia():
    """
    在 GAIA Level 1 上评估

    请求: {"num_samples": 10, "agent_type": "multi_agent" | "react"}
    """
    data = request.json
    num_samples = data.get("num_samples", 10)
    agent_type = data.get("agent_type", "multi_agent")

    try:
        if agent_type == "react":
            result = evaluate_react_gaia(num_samples)
        else:
            result = evaluate_gaia(num_samples)

        return jsonify({
            "success": True,
            "accuracy": result["accuracy"],
            "correct_count": result["correct_count"],
            "total_samples": result["total_samples"],
            "total_tokens": result["total_tokens"],
            "avg_tokens": result["avg_tokens_per_task"],
            "details": result["details"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/eval_webshop", methods=["POST"])
def api_eval_webshop():
    """
    在本地 WebShop-style adapter 上评估

    请求: {"num_samples": 6, "agent_type": "multi_agent" | "react"}
    """
    data = request.json
    num_samples = data.get("num_samples", 6)
    agent_type = data.get("agent_type", "multi_agent")

    try:
        if agent_type == "react":
            result = evaluate_react_webshop(num_samples)
        else:
            result = evaluate_webshop(num_samples)

        return jsonify({
            "success": True,
            "mode": result["mode"],
            "success_rate": result["success_rate"],
            "success_count": result["success_count"],
            "total_samples": result["total_samples"],
            "total_tokens": result["total_tokens"],
            "avg_tokens": result["avg_tokens_per_task"],
            "details": result["details"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/target_report", methods=["POST"])
def api_target_report():
    """
    生成 GAIA/WebShop/成本目标聚合报告（本地样例模式）

    请求: {"num_gaia": 5, "num_webshop": 6}
    """
    data = request.json or {}
    num_gaia = data.get("num_gaia", 5)
    num_webshop = data.get("num_webshop", 6)

    try:
        result = run_sample_report(num_gaia=num_gaia, num_webshop=num_webshop)
        return jsonify({"success": True, "report": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/gaia_samples")
def api_gaia_samples():
    """获取 GAIA 示例任务列表"""
    samples = []
    for s in GAIA_L1_SAMPLES:
        samples.append({
            "task_id": s["task_id"],
            "question": s["question"],
            "level": s["level"],
        })
    return jsonify({"samples": samples})


if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  LangGraph 多智能体协作框架")
    print(f"  访问 http://{FLASK_HOST}:{FLASK_PORT}")
    print(f"{'='*50}\n")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
