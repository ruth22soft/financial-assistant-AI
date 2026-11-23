"""Smoke test for the web app: it loads, sample data fills the dashboard. No AI calls are made."""
from streamlit.testing.v1 import AppTest

from money_agent import config


def test_app_loads_and_shows_sample_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "money.db")  # never touch the real database

    at = AppTest.from_file("../app.py", default_timeout=30).run()
    assert not at.exception
    assert any("No spending recorded yet" in i.value for i in at.info)  # empty state first

    next(b for b in at.sidebar.button if b.label == "Load sample data").click().run()
    assert not at.exception
    assert at.session_state.ledger.check_budgets()["budgets"], "budgets should be loaded"
    assert len(at.dataframe) == 1  # recent expenses table


def test_demo_mode_gives_each_visitor_sample_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "money.db")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DEMO_MAX_AI_REQUESTS", 0)
    monkeypatch.setattr(config, "api_key", lambda: "test-key")

    at = AppTest.from_file("../app.py", default_timeout=30).run()
    assert not at.exception
    assert at.session_state.ledger.get_spending("this_month")["total"] > 0  # pre-filled
    assert not (tmp_path / "money.db").exists()  # nothing written to disk

    at.chat_input[0].set_value("Paid 200 for a taxi").run()  # limit is 0, so no AI call is made
    assert "limit of AI requests" in at.session_state.chat[-1]["text"]
