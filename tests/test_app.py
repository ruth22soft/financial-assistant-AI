"""Smoke test for the web app: it loads, sample data fills the dashboard. No AI calls are made."""
from streamlit.testing.v1 import AppTest

import json
from types import SimpleNamespace as NS

from money_agent import agent as agent_module
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
    assert "used all the free demo requests" in at.session_state.chat[-1]["text"]


class _FakeClient:
    """Stands in for the AI service and records which key it was created with."""
    keys = []

    def __init__(self, key):
        _FakeClient.keys.append(key)
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        msg = NS(content="Saved.", tool_calls=None,
                 model_dump=lambda exclude_none=True: {"role": "assistant", "content": "Saved."})
        return NS(choices=[NS(finish_reason="stop", message=msg)])


def test_visitor_can_paste_their_own_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "money.db")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DEMO_MAX_AI_REQUESTS", 0)  # shared key would be blocked...
    monkeypatch.setattr(config, "api_key", lambda: None)       # ...and there is no shared key at all
    monkeypatch.setattr(agent_module, "make_client", lambda key=None: _FakeClient(key))
    _FakeClient.keys = []

    at = AppTest.from_file("../app.py", default_timeout=30).run()
    at.chat_input[0].set_value("Paid 30 for taxi").run()
    assert "paste a free Gemini API key in the sidebar" in at.session_state.chat[-1]["text"]

    at.sidebar.text_input(key="visitor_key").input("  visitor-key-123  ").run()
    at.chat_input[0].set_value("Paid 30 for taxi").run()
    assert not at.exception
    assert at.session_state.chat[-1]["text"] == "Saved."   # the visitor's key is not capped
    assert _FakeClient.keys == ["visitor-key-123"]          # spaces trimmed, their key used
