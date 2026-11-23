import json
from datetime import date

import pytest

from money_agent import db
from money_agent.seed import seed
from money_agent.tools import TOOLS, Ledger, ToolError, openai_tools, run_tool

TODAY = date(2026, 11, 18)  # day 18 of a 30-day month, like the demo story


@pytest.fixture
def ledger():
    return Ledger(db.connect(":memory:"), today=lambda: TODAY)


def test_add_expense_defaults_to_today_and_reports_budget(ledger):
    ledger.set_budget("Food & Drinks", 3600)
    out = ledger.add_expense(350, "food & drinks", merchant="Habesha Restaurant")
    assert out["spent_on"] == "2026-11-18"
    assert out["category"] == "Food & Drinks"  # case is normalised
    assert out["month_spent_in_category"] == 350
    assert out["monthly_limit"] == 3600
    assert out["status"] == "ok"


def test_demo_story_numbers(ledger):
    """3,060 of 3,600 spent by day 18 -> 85% used, about 1,500 birr over at this pace."""
    ledger.set_budget("Food & Drinks", 3600)
    ledger.add_expense(3060, "Food & Drinks", spent_on="2026-11-10")
    status = ledger.check_budgets()["budgets"][0]
    assert status["used_pct"] == 85
    assert status["status"] == "watch"
    assert status["projected_month_total"] == 5100
    forecast = ledger.forecast_month("Food & Drinks")
    assert forecast["projected_over_by"] == 1500
    assert forecast["days_left"] == 12


def test_get_spending_by_period(ledger):
    ledger.add_expense(200, "Transport", spent_on="2026-11-17")  # Monday this week
    ledger.add_expense(80, "Food & Drinks", spent_on="2026-11-18")
    ledger.add_expense(500, "Groceries", spent_on="2026-11-14")  # last week
    ledger.add_expense(900, "Groceries", spent_on="2026-10-30")  # last month
    week = ledger.get_spending("this_week")
    assert week["total"] == 280
    assert ledger.get_spending("last_week")["total"] == 500
    assert ledger.get_spending("this_month")["total"] == 780
    assert ledger.get_spending("last_month")["total"] == 900
    assert ledger.get_spending("this_month", category="Groceries")["total"] == 500


def test_correction_flow(ledger):
    saved = ledger.add_expense(350, "Food & Drinks", merchant="Habesha Restaurant")
    updated = ledger.update_expense(saved["saved_expense_id"], category="Business", note="work lunch")
    assert updated["updated_expense"]["category"] == "Business"
    ledger.remember("merchant:Habesha Restaurant", "usually Business (work lunches)")
    assert ledger.memories() == {"merchant:Habesha Restaurant": "usually Business (work lunches)"}
    ledger.delete_expense(saved["saved_expense_id"])
    assert ledger.list_expenses()["expenses"] == []


@pytest.mark.parametrize("kwargs, message", [
    ({"amount": -5, "category": "Transport"}, "greater than zero"),
    ({"amount": 50, "category": "Snacks"}, "Unknown category"),
    ({"amount": 50, "category": "Transport", "spent_on": "18/11/2026"}, "YYYY-MM-DD"),
    ({"amount": 50, "category": "Transport", "spent_on": "2026-12-01"}, "future"),
])
def test_add_expense_rejects_bad_input(ledger, kwargs, message):
    with pytest.raises(ToolError, match=message):
        ledger.add_expense(**kwargs)


def test_run_tool_returns_errors_to_the_model(ledger):
    output, is_error = run_tool(ledger, "delete_expense", {"expense_id": 999})
    assert is_error and "No expense with id 999" in json.loads(output)["error"]
    output, is_error = run_tool(ledger, "add_expense", {"amount": 10, "category": "Transport", "hack": 1})
    assert is_error and "Unexpected fields" in json.loads(output)["error"]
    output, is_error = run_tool(ledger, "drop_tables", {})
    assert is_error


def test_tool_schemas_match_ledger_methods():
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert set(schema["required"]) <= set(schema["properties"])
        assert callable(getattr(Ledger, tool["name"]))


def test_openai_tool_format_is_portable():
    tools = {t["function"]["name"]: t for t in openai_tools()}
    assert set(tools) == {t["name"] for t in TOOLS}
    assert all(t["type"] == "function" for t in tools.values())
    assert "parameters" not in tools["check_budgets"]["function"]  # no-input tools leave it out
    text = json.dumps(list(tools.values()))
    assert "additionalProperties" not in text and '"format"' not in text


def test_seed_creates_data_and_budgets(ledger):
    n = seed(ledger)
    assert n > 50
    report = ledger.check_budgets()
    assert len(report["budgets"]) == 6
    assert ledger.get_spending("this_month")["total"] > 0
