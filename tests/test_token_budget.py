from graph.token_budget import get_budget_policy, record_token_usage


def test_budget_policy_levels():
    state = {"token_used": 800, "token_budget": 1000}
    policy = get_budget_policy(state, risk="low")

    assert policy["degrade_level"] == 1
    assert policy["fast_critic"] is True
    assert policy["skip_low_risk_critic"] is True
    assert policy["merge_steps"] is False


def test_record_token_usage_tracks_role_and_event():
    state = {
        "token_used": 100,
        "token_budget": 1000,
        "role_token_used": {"planner": 100, "executor": 0, "critic": 0, "synthesizer": 0},
        "budget_events": [],
    }

    token_used, role_token_used, budget_events = record_token_usage(state, "executor", 50)

    assert token_used == 150
    assert role_token_used["executor"] == 50
    assert budget_events[-1]["role"] == "executor"
    assert budget_events[-1]["token_used"] == 150
