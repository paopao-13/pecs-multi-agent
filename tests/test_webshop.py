from graph.builder import run_task
from tools.webshop import webshop_select


def test_webshop_tool_selects_matching_product():
    result = webshop_select({
        "instruction": "Find a decaf chamomile herbal tea under $16."
    })

    assert "SELECTED:" in result
    assert "ws_tea_002" in result


def test_webshop_task_runs_through_langgraph():
    state = run_task(
        "WebShop任务：Find a compact USB-C charger under $18.",
        token_budget=5000,
    )

    assert "ws_usb_002" in state["final_answer"]
    assert state["results"][0]["action"] == "webshop"
