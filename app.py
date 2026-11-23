"""Web app for the AI Financial Assistant.

Run:  streamlit run app.py
Then open the address it prints (usually http://localhost:8501).
"""
from __future__ import annotations

import html
import os
from datetime import date

import openai
import streamlit as st

# On Streamlit Community Cloud, settings live in the app's "Secrets" box. Copy them into the
# environment before money_agent reads its settings. Locally, the .env file is used instead.
try:
    for _key, _value in st.secrets.items():
        _pairs = _value.items() if hasattr(_value, "items") else [(_key, _value)]  # also accept a [section]
        for _k, _v in _pairs:
            if isinstance(_v, (str, int, float, bool)):
                os.environ.setdefault(_k, str(_v))
except Exception:  # no secrets file: running locally
    pass

from money_agent import config, db  # noqa: E402  (must come after the secrets are loaded)
from money_agent import agent as agent_module  # noqa: E402
from money_agent.agent import MoneyAgent
from money_agent.errors import friendly_error, missing_key_message  # noqa: E402
from money_agent.seed import seed  # noqa: E402
from money_agent.tools import Ledger  # noqa: E402

st.set_page_config(page_title="Money Assistant", page_icon=":material/account_balance_wallet:", layout="wide")

STATUS = {  # fill, track, icon, label: a status always shows an icon and a word, never color alone
    "ok": ("#2E58CC", "#E7EDFB", "✓", "On track"),
    "watch": ("#E8A10E", "#FDF0DB", "!", "Watch"),
    "over": ("#D03B3B", "#FBE8E6", "✕", "Over budget"),
}

st.markdown("""
<style>
  .block-container { padding-top: 2rem; }
  .meter-row { margin: 0 0 14px; }
  .meter-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2px 8px; font-size: 0.92rem; }
  .meter-head b { font-weight: 600; color: #141B36; }
  .meter-head span { color: #4B536F; font-variant-numeric: tabular-nums; }
  .meter-track { height: 8px; border-radius: 99px; margin: 6px 0 4px; overflow: hidden; }
  .meter-fill { height: 100%; border-radius: 99px; }
  .meter-foot { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2px 8px; font-size: 0.8rem; color: #4B536F; }
  .status-badge { display: inline-flex; white-space: nowrap; align-items: center; gap: 5px; font-weight: 600; color: #141B36; }
  .status-dot { width: 16px; height: 16px; border-radius: 50%; color: #fff; font-size: 10px; line-height: 16px;
                text-align: center; display: inline-block; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(105px, 1fr)); gap: 12px; margin: 4px 0 16px; }
  .tile { background: #F4F6FA; border-radius: 10px; padding: 10px 12px; min-width: 0; }
  .tile-label { font-size: 0.78rem; color: #4B536F; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tile-value { white-space: nowrap; font-size: 1.45rem; font-weight: 600; color: #141B36; font-variant-numeric: tabular-nums; }
  .action { font-family: ui-monospace, Menlo, monospace; font-size: 0.82rem; color: #4B536F; margin: 2px 0; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- session state
def init_state() -> None:
    ss = st.session_state
    if "ledger" not in ss:
        if config.DEMO_MODE:  # a private, throwaway database for each visitor, pre-filled
            ss.ledger = Ledger(db.connect(":memory:", check_same_thread=False))
            seed(ss.ledger)
        else:
            ss.ledger = Ledger(db.connect(config.DB_PATH, check_same_thread=False))
        ss.ai_requests = 0
        ss.chat = []        # what the page shows: {"role", "text", "actions", "image"}
        ss.agent = None     # created on first use, so the page works without a key
        ss.upload_key = 0   # changing this clears the upload box after a receipt is sent


KEY_PAGE = "https://aistudio.google.com/apikey"


def active_key() -> tuple[str | None, str]:
    """Which key to use: the visitor's own (pasted in the sidebar) first, then the app owner's."""
    own = (st.session_state.get("visitor_key") or "").strip()
    if own:
        return own, "visitor"
    if config.api_key():
        return config.api_key(), "shared"
    return None, "none"


def get_agent() -> MoneyAgent | None:
    ss = st.session_state
    key, _ = active_key()
    if key is None:
        return None
    if ss.agent is None:
        ss.agent = MoneyAgent(ss.ledger, client=agent_module.make_client(key), on_tool_call=_record_action)
    elif ss.get("agent_key") != key:  # the visitor added or changed their key: keep the chat, swap the key
        ss.agent.client = agent_module.make_client(key)
    ss.agent_key = key
    return ss.agent


def no_key_message() -> str:
    if config.PROVIDER != "gemini":
        return missing_key_message()
    return (f"To start, paste a free Gemini API key in the sidebar under **Your AI key**. "
            f"You can get one in a minute at {KEY_PAGE} (sign in with Google, then click Create API key).")


def _record_action(name: str, args: dict, output: str, is_error: bool) -> None:
    st.session_state.current_actions.append((name, args, is_error))


def run_agent(user_text: str, call, image: bytes | None = None) -> None:
    """Show the user's message, let the agent work, show its reply with the actions it took."""
    ss = st.session_state
    ss.chat.append({"role": "user", "text": user_text, "image": image})
    agent = get_agent()
    ss.current_actions = []
    shared = active_key()[1] == "shared"
    if agent is None:
        reply = no_key_message()
    elif config.DEMO_MODE and shared and ss.ai_requests >= config.DEMO_MAX_AI_REQUESTS:
        reply = ("You've used all the free demo requests for this session. Paste your own free Gemini key in "
                 f"the sidebar under **Your AI key** to keep going (get one at {KEY_PAGE}).")
    else:
        if shared:
            ss.ai_requests += 1
        with st.spinner("Thinking..."):
            try:
                reply = call(agent)
            except openai.OpenAIError as exc:
                reply = explain(exc)
    ss.chat.append({"role": "assistant", "text": reply, "actions": list(ss.current_actions)})
    st.rerun()


# ---------------------------------------------------------------- pieces of the page
def render_key_box() -> None:
    ss = st.session_state
    st.header("Your AI key")
    st.text_input("Gemini API key", type="password", key="visitor_key", placeholder="Paste your key (AIza...)",
                  help="Free from Google AI Studio. Used only in this browser tab; never saved.")
    _, source = active_key()
    if source == "visitor":
        st.caption("Using your key. It stays in this browser tab only: it isn't saved and no one else can see it.")
        if st.button("Check my key", width="stretch"):
            ss.key_check = check_key(active_key()[0])
        if ss.get("key_check"):
            ok, message = ss.key_check
            (st.success if ok else st.error)(message)
    elif source == "shared":
        left = max(0, config.DEMO_MAX_AI_REQUESTS - ss.ai_requests) if config.DEMO_MODE else None
        extra = f" ({left} requests left in this session)" if left is not None else ""
        st.caption(f"Optional: the demo's shared key is being used{extra}. Paste your own to remove the limit.")
    else:
        st.caption("Needed to chat. It's free and takes about a minute.")
    st.link_button("Get a free key", KEY_PAGE, width="stretch")


def explain(exc: Exception) -> str:
    """Plain-language error; a visitor's bad key gets advice about the sidebar, not the .env file."""
    message = friendly_error(exc) or f"Something went wrong: {exc}"
    if "API key was rejected" in message and active_key()[1] == "visitor":
        return ("Google rejected that key. Copy it again from AI Studio (it usually starts with AIza) and paste "
                "the whole key in the sidebar, with no spaces.")
    return message


def check_key(key: str) -> tuple[bool, str]:
    """Send one tiny request with the visitor's key and report whether it works."""
    try:
        agent_module.make_client(key).chat.completions.create(
            model=config.MODEL, messages=[{"role": "user", "content": "Reply with just OK."}])
        return True, "Your key works."
    except openai.OpenAIError as exc:
        return False, explain(exc)


def render_sidebar() -> None:
    ss = st.session_state
    with st.sidebar:
        if config.PROVIDER == "gemini":
            render_key_box()
            st.divider()
        st.header("Add spending")

        st.subheader("Receipt photo")
        upload = st.file_uploader("Drop a photo of a receipt", type=["png", "jpg", "jpeg", "webp"],
                                  key=f"receipt_{ss.upload_key}")
        note = st.text_input("Note (optional)", placeholder="e.g. work lunch", key=f"note_{ss.upload_key}")
        if upload is not None:
            st.image(upload, width=160)
            if st.button("Read receipt", type="primary", width="stretch"):
                data, media_type = upload.getvalue(), upload.type
                ss.upload_key += 1
                run_agent(f"Receipt photo: {upload.name}" + (f" ({note})" if note else ""),
                          lambda a: a.send_receipt_bytes(data, media_type, note), image=data)

        st.subheader("Bank SMS")
        sms = st.text_area("Paste the text of a bank SMS", height=100,
                           placeholder="Your account 1000123456789 was debited ETB 80.00 at ...")
        if st.button("Record from SMS", width="stretch", disabled=not sms.strip()):
            run_agent("Bank SMS (account numbers masked before sending)", lambda a: a.send_sms(sms))

        st.divider()
        st.header("Ask the agent")
        if st.button("Daily budget check", width="stretch"):
            run_agent("Run the daily budget check", lambda a: a.daily_check())
        if st.button("Weekly summary", width="stretch"):
            run_agent("Give me the weekly summary", lambda a: a.weekly_summary())

        st.divider()
        st.header("Data")
        if st.button("Load sample data", width="stretch",
                     help="Replaces everything with about six weeks of made-up spending and budgets."):
            seed(ss.ledger, reset=True)
            ss.chat, ss.agent = [], None
            st.rerun()
        if config.DEMO_MODE:
            used = ss.ai_requests
            st.caption(f"Demo session: {used} of {config.DEMO_MAX_AI_REQUESTS} AI requests used. "
                       f"AI model: {config.MODEL}. Account numbers are masked before anything is sent to the AI.")
        else:
            st.caption(f"AI model: {config.MODEL} via {config.PROVIDER}. Data is stored on this computer in "
                       f"{config.DB_PATH.name}. Account numbers are masked before anything is sent to the AI.")


def render_chat() -> None:
    ss = st.session_state
    st.subheader("Chat")
    box = st.container(height=560, border=True)
    with box:
        if not ss.chat:
            with st.chat_message("assistant"):
                st.write("Hi! Tell me what you spent, drop a receipt photo in the sidebar, or ask about your "
                         "budget. For example: *Paid 200 for a taxi* or *How much did I spend on food this month?*")
        for msg in ss.chat:
            with st.chat_message(msg["role"]):
                if msg.get("image"):
                    st.image(msg["image"], width=180)
                st.write(msg["text"])
                actions = msg.get("actions") or []
                if actions:
                    with st.expander(f"Agent actions ({len(actions)})"):
                        for name, args, is_error in actions:
                            shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
                            mark = "✕" if is_error else "→"
                            st.markdown(f'<div class="action">{mark} {html.escape(name)}({html.escape(shown)})</div>',
                                        unsafe_allow_html=True)
    if prompt := st.chat_input("Paid 200 for a taxi"):
        run_agent(prompt, lambda a: a.ask(prompt))


def _meter(b: dict) -> str:
    fill, track, icon, label = STATUS[b["status"]]
    pct = min(100, b["used_pct"])
    tip = (f"{b['category']}: {b['month_spent_in_category']:,.0f} of {b['monthly_limit']:,.0f} birr used. "
           f"At this pace: {b['projected_month_total']:,.0f} birr by month end.")
    return f"""
<div class="meter-row" title="{html.escape(tip)}">
  <div class="meter-head"><b>{html.escape(b['category'])}</b>
    <span>{b['month_spent_in_category']:,.0f} / {b['monthly_limit']:,.0f} birr</span></div>
  <div class="meter-track" style="background:{track}"><div class="meter-fill" style="width:{pct}%;background:{fill}"></div></div>
  <div class="meter-foot"><span class="status-badge"><span class="status-dot" style="background:{fill}">{icon}</span>{label}</span>
    <span>{b['used_pct']}% used · pace {b['projected_month_total']:,.0f}</span></div>
</div>"""


def render_dashboard() -> None:
    ledger: Ledger = st.session_state.ledger
    budgets = ledger.check_budgets()
    month = ledger.get_spending("this_month")
    st.subheader(budgets["month"])
    st.caption(f"Day {budgets['day_of_month']} of {budgets['days_in_month']}")

    if month["number_of_expenses"] == 0 and not budgets["budgets"]:
        st.info("No spending recorded yet. Click **Load sample data** in the sidebar, or tell the assistant what "
                "you spent.")
        return

    forecast = ledger.forecast_month()
    tiles = [("Spent (birr)", f"{month['total']:,.0f}"),
             ("Forecast (birr)", f"{forecast['projected_month_total']:,.0f}"),
             ("Days left", str(forecast["days_left"]))]
    st.markdown('<div class="tiles">' + "".join(
        f'<div class="tile" title="{label}"><div class="tile-label">{label}</div>'
        f'<div class="tile-value">{value}</div></div>' for label, value in tiles) + "</div>",
        unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("**Budgets**")
        if budgets["budgets"]:
            order = {"over": 0, "watch": 1, "ok": 2}
            rows = sorted(budgets["budgets"], key=lambda b: (order[b["status"]], -b["used_pct"]))
            st.markdown("".join(_meter(b) for b in rows), unsafe_allow_html=True)
        else:
            st.caption("No budgets yet. Ask the assistant: “Set my food budget to 3,600”.")

    st.markdown("**Recent expenses**")
    recent = ledger.list_expenses(limit=8)["expenses"]
    if recent:
        st.dataframe(
            [{"Date": date.fromisoformat(r["spent_on"]).strftime("%d %b"), "Merchant": r["merchant"] or "",
              "Category": r["category"], "Birr": r["amount"]} for r in recent],
            hide_index=True, width="stretch",
            column_config={"Birr": st.column_config.NumberColumn(format="%,.0f")},
        )


# ---------------------------------------------------------------- page
init_state()
st.title("Money Assistant")
st.caption("An AI agent that tracks your expenses and helps you stay on budget. It reads, records and advises; "
           "it can never move money.")
if config.DEMO_MODE:
    st.info("**Demo:** you have your own copy of sample data, which resets when you close this tab. "
            "Please don't enter real financial details.", icon=":material/science:")
render_sidebar()
left, right = st.columns([3, 2], gap="large")
with left:
    render_chat()
with right:
    render_dashboard()
