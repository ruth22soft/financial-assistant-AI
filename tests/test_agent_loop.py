"""Test the agent loop with a scripted fake client, so no API key or network is needed.

The fake returns real `ChatCompletion` objects from the openai package, shaped like
Gemini's replies, including the hidden thought signature Gemini attaches to tool calls.
"""
import json
from datetime import date
from types import SimpleNamespace as NS

from openai.types.chat import ChatCompletion

from money_agent import db
from money_agent.agent import MoneyAgent
from money_agent.tools import Ledger


def completion(content=None, tool_calls=None, finish_reason=None):
    finish_reason = finish_reason or ("tool_calls" if tool_calls else "stop")
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "gemini-3.8-flash",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}],
    })


def call(id_, name, args, signature=None):
    tc = {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    if signature:
        tc["extra_content"] = {"google": {"thought_signature": signature}}
    return tc


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        return self.responses.pop(0)


def make_agent(responses):
    ledger = Ledger(db.connect(":memory:"), today=lambda: date(2026, 11, 18))
    ledger.set_budget("Transport", 2500)
    client = FakeClient(responses)
    seen = []
    agent = MoneyAgent(ledger, client=client, model="test-model",
                       on_tool_call=lambda n, i, o, e: seen.append((n, e)))
    return agent, ledger, client, seen


def test_agent_calls_tool_then_answers():
    agent, ledger, client, seen = make_agent([
        completion(tool_calls=[call("c1", "add_expense", {"amount": 200, "category": "Transport"}, signature="SIG")]),
        completion(content="Saved 200 birr under Transport."),
    ])
    reply = agent.ask("Paid 200 for a taxi")

    assert reply == "Saved 200 birr under Transport."
    assert seen == [("add_expense", False)]
    assert ledger.get_spending("today")["total"] == 200

    first, second = client.calls
    assert first["messages"][0]["role"] == "system"
    assert first["model"] == "test-model"
    assert {t["function"]["name"] for t in first["tools"]} >= {"add_expense", "check_budgets"}
    # The second request carries the assistant's tool call (with Gemini's signature kept)
    # and then the tool result linked to it by id.
    assistant, tool = second["messages"][-2], second["messages"][-1]
    assert assistant["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "SIG"}}
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": tool["content"]}
    assert json.loads(tool["content"])["category"] == "Transport"


def test_parallel_tool_calls_results_follow_in_order():
    agent, ledger, client, _ = make_agent([
        completion(tool_calls=[call("a", "add_expense", {"amount": 80, "category": "Food & Drinks"}, "SIG"),
                               call("b", "add_expense", {"amount": 200, "category": "Transport"})]),
        completion(content="Saved both."),
    ])
    agent.ask("Coffee 80 and taxi 200")
    msgs = client.calls[1]["messages"]
    assert [m["role"] for m in msgs[-3:]] == ["assistant", "tool", "tool"]
    assert [m["tool_call_id"] for m in msgs[-2:]] == ["a", "b"]
    assert ledger.get_spending("today")["total"] == 280


def test_tool_errors_go_back_to_the_model():
    agent, _, client, seen = make_agent([
        completion(tool_calls=[call("c1", "delete_expense", {"expense_id": 42})]),
        completion(content="I couldn't find that expense."),
    ])
    agent.ask("Delete expense 42")
    result = client.calls[1]["messages"][-1]
    assert "No expense with id 42" in json.loads(result["content"])["error"]
    assert seen == [("delete_expense", True)]


def test_bad_json_arguments_are_reported_not_run():
    bad = {"id": "c1", "type": "function", "function": {"name": "add_expense", "arguments": "{amount: 5"}}
    agent, ledger, client, seen = make_agent([completion(tool_calls=[bad]), completion(content="Sorry, again?")])
    agent.ask("5 birr")
    assert seen == [("add_expense", True)]
    assert ledger.get_spending("today")["total"] == 0


def test_blocked_reply_rolls_back_the_turn():
    agent, _, _, _ = make_agent([completion(content=None, finish_reason="content_filter")])
    reply = agent.ask("something the model declines")
    assert "can't help" in reply
    assert agent.messages == []


def test_receipt_photo_is_sent_as_image():
    agent, _, client, _ = make_agent([completion(content="Recorded.")])
    agent.send_receipt("sample_data/receipt.png")
    content = client.calls[0]["messages"][-1]["content"]
    image = next(part for part in content if part["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/png;base64,")


def test_sms_is_masked_before_sending():
    agent, _, client, _ = make_agent([completion(content="Logged.")])
    agent.send_sms("Account 1000123456789 debited ETB 80.00 at Bole Cafe")
    sent = client.calls[0]["messages"][-1]["content"]
    assert "1000123456789" not in sent and "****6789" in sent
    assert sent.startswith("<bank_sms>")


def test_system_prompt_includes_date_and_memory():
    ledger = Ledger(db.connect(":memory:"), today=lambda: date(2026, 11, 18))
    ledger.remember("merchant:Habesha Restaurant", "usually Business")
    agent = MoneyAgent(ledger, client=FakeClient([]), model="m")
    assert "Wednesday, 18 November 2026" in agent.system
    assert "merchant:Habesha Restaurant: usually Business" in agent.system
