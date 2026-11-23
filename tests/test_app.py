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
