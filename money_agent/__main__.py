"""Command line for the AI Financial Assistant.

    python -m money_agent seed --reset      # load six weeks of sample data
    python -m money_agent report            # budgets and spending, no AI needed
    python -m money_agent chat              # talk to the assistant
    python -m money_agent daily-check       # the proactive check (schedule this daily)
    python -m money_agent weekly-summary    # the Sunday summary (schedule this weekly)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import openai

from . import config, db
from .agent import MoneyAgent, make_client
from .errors import friendly_error, missing_key_message
from .config import DB_PATH
from .seed import seed
from .tools import Ledger

HELP = """Type a message, or use a command:
  /photo PATH [note]   send a receipt photo, e.g. /photo sample_data/receipt.png
  /sms TEXT            forward a bank SMS (account numbers are masked first)
  /check               run the daily budget check
  /weekly              get the weekly summary
  /report              show budgets and spending (no AI)
  /help                show this help
  /quit                leave"""

_TTY = sys.stdout.isatty()


def _dim(s: str) -> str:
    return f"\033[2m{s}\033[0m" if _TTY else s


def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if _TTY else s


def print_tool_call(name: str, tool_input: dict, output: str, is_error: bool) -> None:
    """Show each action the agent takes, so the audience can watch the loop."""
    args = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
    mark = "x" if is_error else "->"
    print(_dim(f"   {mark} {name}({args})"))
    if is_error:
        print(_dim(f"     {json.loads(output).get('error')}"))


def report(ledger: Ledger) -> str:
    data = ledger.check_budgets()
    lines = [f"{data['month']}: day {data['day_of_month']} of {data['days_in_month']}", ""]
    lines.append(f"{'Category':<20}{'Spent':>10}{'Limit':>10}{'Used':>7}  {'Pace':>10}  Status")
    for b in data["budgets"]:
        lines.append(f"{b['category']:<20}{b['month_spent_in_category']:>10,.0f}{b['monthly_limit']:>10,.0f}"
                     f"{b['used_pct']:>6}%  {b['projected_month_total']:>10,.0f}  {b['status']}")
    month = ledger.get_spending("this_month")
    lines += ["", f"Total this month: {month['total']:,.0f} birr across {month['number_of_expenses']} expenses"]
    return "\n".join(lines)


def _call(fn, *args) -> str:
    """Run one agent call and turn API errors into plain advice."""
    print(_dim("   (thinking... press Ctrl+C to cancel)"))
    try:
        return fn(*args)
    except KeyboardInterrupt:
        return "Cancelled. You can type a new message."
    except openai.OpenAIError as exc:
        message = friendly_error(exc)
        if message is None:
            raise
        if isinstance(exc, openai.APITimeoutError):
            message += " Run `python -m money_agent ping` to test the connection."
        return message


def chat(agent: MoneyAgent, ledger: Ledger) -> None:
    print(_bold("Money Assistant") + "  (type /help for commands)")
    while True:
        try:
            line = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        cmd, _, rest = line.partition(" ")
        if cmd in ("/quit", "/exit"):
            return
        if cmd == "/help":
            print(HELP)
            continue
        if cmd == "/report":
            print(report(ledger))
            continue
        if cmd == "/photo":
            path, _, note = rest.strip().partition(" ")
            if not path:
                print("Usage: /photo PATH [note]")
                continue
            reply = _call(agent.send_receipt, path, note)
        elif cmd == "/sms":
            if not rest.strip():
                print("Usage: /sms TEXT")
                continue
            reply = _call(agent.send_sms, rest)
        elif cmd == "/check":
            reply = _call(agent.daily_check)
        elif cmd == "/weekly":
            reply = _call(agent.weekly_summary)
        else:
            reply = _call(agent.ask, line)
        print(_bold("assistant > ") + reply)


def ping() -> int:
    """Send one tiny request to check that the key, model and connection all work."""
    client = make_client()
    started = time.monotonic()

    def one_request() -> str:
        r = client.chat.completions.create(
            model=config.MODEL, messages=[{"role": "user", "content": "Reply with just the word OK."}])
        return f"OK: the AI answered \"{(r.choices[0].message.content or '').strip()}\""

    result = _call(one_request)
    print(f"{result}  ({time.monotonic() - started:.1f} seconds)")
    return 0 if result.startswith("OK:") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="money_agent", description="AI Financial Assistant")
    parser.add_argument("command", nargs="?", default="chat",
                        choices=["chat", "seed", "report", "daily-check", "weekly-summary", "ping"])
    parser.add_argument("--reset", action="store_true", help="with seed: clear the database first")
    parser.add_argument("--quiet", action="store_true", help="hide the tool calls the agent makes")
    args = parser.parse_args(argv)

    ledger = Ledger(db.connect(DB_PATH))

    if args.command == "seed":
        n = seed(ledger, reset=args.reset)
        print(f"Added {n} sample expenses and 6 budgets to {DB_PATH}")
        return 0
    if args.command == "report":
        print(report(ledger))
        return 0

    if config.PROVIDER not in config.PROVIDERS and not (config.BASE_URL and config.MODEL):
        print(f"Unknown provider '{config.PROVIDER}'. Use gemini or ollama, or set MONEY_AGENT_BASE_URL "
              "and MONEY_AGENT_MODEL for another OpenAI-compatible service.")
        return 2
    if not config.api_key():
        print(missing_key_message())
        return 2
    print(_dim(f"Using {config.MODEL} via {config.PROVIDER}"))
    if args.command == "ping":
        return ping()
    agent = MoneyAgent(ledger, on_tool_call=None if args.quiet else print_tool_call)

    if args.command == "daily-check":
        print(_call(agent.daily_check))
    elif args.command == "weekly-summary":
        print(_call(agent.weekly_summary))
    else:
        chat(agent, ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
