"""The agent's tools: ordinary Python functions that read and write the ledger.

The AI model never touches the database directly. It asks for a tool by name, we
validate the input, run the function, and send the result back as JSON.
"""
from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .config import ALERT_THRESHOLD, CATEGORIES, FIXED_CATEGORIES


class ToolError(Exception):
    """A problem the model can fix (bad category, unknown id, ...). Sent back as an error result."""


# ---------------------------------------------------------------- helpers
def _round(x: float) -> float:
    return round(float(x), 2)


def _parse_date(value: str | None, today: date) -> date:
    if not value:
        return today
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ToolError(f"Date '{value}' must look like YYYY-MM-DD.") from exc


def _check_category(value: str) -> str:
    for c in CATEGORIES:
        if value.strip().lower() == c.lower():
            return c
    raise ToolError(f"Unknown category '{value}'. Use one of: {', '.join(CATEGORIES)}.")


def _check_amount(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("Amount must be a number.")
    if value <= 0:
        raise ToolError("Amount must be greater than zero.")
    if value > 10_000_000:
        raise ToolError("Amount looks too large. Please check it.")
    return float(value)


def _month_bounds(day: date) -> tuple[date, date]:
    last = calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=1), day.replace(day=last)


def _period_bounds(period: str, today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    if period == "today":
        return today, today
    if period == "this_week":
        return monday, today
    if period == "last_week":
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if period == "last_30_days":
        return today - timedelta(days=29), today
    raise ToolError("Period must be one of: today, this_week, last_week, this_month, last_month, last_30_days.")


# ---------------------------------------------------------------- the ledger
class Ledger:
    """All money operations. `today` can be fixed for tests and demos."""

    def __init__(self, conn: sqlite3.Connection, today: Callable[[], date] = date.today):
        self.conn = conn
        self._today = today

    @property
    def today(self) -> date:
        return self._today()

    # -- expenses ---------------------------------------------------------
    def add_expense(self, amount, category, spent_on=None, merchant=None, note=None, source="chat") -> dict:
        amount = _check_amount(amount)
        category = _check_category(category)
        day = _parse_date(spent_on, self.today)
        if day > self.today:
            raise ToolError("The date is in the future. Use today's date or earlier.")
        if source not in ("chat", "receipt", "sms", "seed"):
            source = "chat"
        cur = self.conn.execute(
            "INSERT INTO expenses (spent_on, amount, category, merchant, note, source) VALUES (?, ?, ?, ?, ?, ?)",
            (day.isoformat(), amount, category, (merchant or "").strip() or None, (note or "").strip() or None, source),
        )
        self.conn.commit()
        status = self._category_status(category, day)
        return {"saved_expense_id": cur.lastrowid, "amount": amount, "category": category,
                "spent_on": day.isoformat(), "merchant": merchant, **status}

    def update_expense(self, expense_id, amount=None, category=None, merchant=None, note=None, spent_on=None) -> dict:
        row = self._get(expense_id)
        changes = {}
        if amount is not None:
            changes["amount"] = _check_amount(amount)
        if category is not None:
            changes["category"] = _check_category(category)
        if merchant is not None:
            changes["merchant"] = merchant.strip() or None
        if note is not None:
            changes["note"] = note.strip() or None
        if spent_on is not None:
            changes["spent_on"] = _parse_date(spent_on, self.today).isoformat()
        if not changes:
            raise ToolError("Nothing to change. Pass at least one field to update.")
        sets = ", ".join(f"{k} = ?" for k in changes)
        self.conn.execute(f"UPDATE expenses SET {sets} WHERE id = ?", (*changes.values(), row["id"]))
        self.conn.commit()
        return {"updated_expense": dict(self._get(expense_id))}

    def delete_expense(self, expense_id) -> dict:
        row = self._get(expense_id)
        self.conn.execute("DELETE FROM expenses WHERE id = ?", (row["id"],))
        self.conn.commit()
        return {"deleted_expense": dict(row)}

    def list_expenses(self, limit=10, category=None) -> dict:
        limit = max(1, min(int(limit or 10), 50))
        sql = "SELECT id, spent_on, amount, category, merchant, note, source FROM expenses"
        args: list = []
        if category:
            sql += " WHERE category = ?"
            args.append(_check_category(category))
        sql += " ORDER BY spent_on DESC, id DESC LIMIT ?"
        args.append(limit)
        return {"expenses": [dict(r) for r in self.conn.execute(sql, args)]}

    def get_spending(self, period, category=None) -> dict:
        start, end = _period_bounds(period, self.today)
        where, args = "spent_on BETWEEN ? AND ?", [start.isoformat(), end.isoformat()]
        if category:
            where += " AND category = ?"
            args.append(_check_category(category))
        by_cat = self.conn.execute(
            f"SELECT category, SUM(amount) AS total, COUNT(*) AS n FROM expenses WHERE {where} "
            "GROUP BY category ORDER BY total DESC", args).fetchall()
        top = self.conn.execute(
            f"SELECT COALESCE(merchant, '(unknown)') AS merchant, SUM(amount) AS total FROM expenses "
            f"WHERE {where} GROUP BY merchant ORDER BY total DESC LIMIT 3", args).fetchall()
        total = sum(r["total"] for r in by_cat)
        return {
            "period": period, "from": start.isoformat(), "to": end.isoformat(),
            "total": _round(total), "number_of_expenses": sum(r["n"] for r in by_cat),
            "by_category": [{"category": r["category"], "total": _round(r["total"]), "count": r["n"]} for r in by_cat],
            "top_merchants": [{"merchant": r["merchant"], "total": _round(r["total"])} for r in top],
        }

    # -- budgets ----------------------------------------------------------
    def set_budget(self, category, monthly_limit) -> dict:
        category = _check_category(category)
        limit = _check_amount(monthly_limit)
        self.conn.execute(
            "INSERT INTO budgets (category, monthly_limit) VALUES (?, ?) "
            "ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit", (category, limit))
        self.conn.commit()
        return {"category": category, "monthly_limit": limit, **self._category_status(category, self.today)}

    def check_budgets(self) -> dict:
        rows = self.conn.execute("SELECT category FROM budgets ORDER BY category").fetchall()
        report = [{"category": r["category"], **self._category_status(r["category"], self.today)} for r in rows]
        start, end = _month_bounds(self.today)
        return {
            "month": self.today.strftime("%B %Y"), "day_of_month": self.today.day, "days_in_month": end.day,
            "alert_threshold_pct": int(ALERT_THRESHOLD * 100),
            "budgets": report,
            "needs_attention": [b["category"] for b in report if b["status"] != "ok"],
        }

    def forecast_month(self, category=None) -> dict:
        cat = _check_category(category) if category else None
        start, end = _month_bounds(self.today)
        where, args = "spent_on BETWEEN ? AND ?", [start.isoformat(), self.today.isoformat()]
        if cat:
            where += " AND category = ?"
            args.append(cat)
        spent = self.conn.execute(f"SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE {where}", args).fetchone()[0]
        if cat in FIXED_CATEGORIES:
            projected, method = spent, "fixed monthly bills: estimate equals what is already paid"
        elif cat:
            projected, method = spent / self.today.day * end.day, "straight-line: spent so far / days passed x days in month"
        else:
            fixed_spent = self.conn.execute(
                f"SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE {where} AND category IN "
                f"({', '.join('?' * len(FIXED_CATEGORIES))})", [*args, *sorted(FIXED_CATEGORIES)]).fetchone()[0]
            projected = fixed_spent + (spent - fixed_spent) / self.today.day * end.day
            method = "fixed bills as paid, plus day-to-day spending at its current pace"
        result = {"category": cat or "all", "spent_so_far": _round(spent),
                  "projected_month_total": _round(projected), "days_left": end.day - self.today.day,
                  "method": method}
        if cat:
            lim = self._limit(cat)
            if lim:
                result["monthly_limit"] = lim
                result["projected_over_by"] = _round(max(0.0, projected - lim))
        return result

    # -- memory -------------------------------------------------------------
    def remember(self, key, value) -> dict:
        key, value = str(key).strip(), str(value).strip()
        if not key or not value:
            raise ToolError("Both key and value are needed.")
        if len(key) > 100 or len(value) > 300:
            raise ToolError("Keep memories short: key up to 100 and value up to 300 characters.")
        self.conn.execute(
            "INSERT INTO memory (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP", (key, value))
        self.conn.commit()
        return {"remembered": {key: value}}

    def memories(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.conn.execute("SELECT key, value FROM memory ORDER BY key")}

    # -- internals ---------------------------------------------------------
    def _get(self, expense_id) -> sqlite3.Row:
        if isinstance(expense_id, bool) or not isinstance(expense_id, int):
            raise ToolError("expense_id must be a whole number. Use list_expenses to find it.")
        row = self.conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if row is None:
            raise ToolError(f"No expense with id {expense_id}. Use list_expenses to find the right one.")
        return row

    def _limit(self, category: str) -> float | None:
        row = self.conn.execute("SELECT monthly_limit FROM budgets WHERE category = ?", (category,)).fetchone()
        return row["monthly_limit"] if row else None

    def _category_status(self, category: str, day: date) -> dict:
        start, end = _month_bounds(day)
        # Pace is measured up to today for the current month; a past month is complete.
        upto = self.today if (day.year, day.month) == (self.today.year, self.today.month) else end
        spent = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE category = ? AND spent_on BETWEEN ? AND ?",
            (category, start.isoformat(), end.isoformat())).fetchone()[0]
        out = {"month_spent_in_category": _round(spent)}
        limit = self._limit(category)
        if not limit:
            out["monthly_limit"] = None
            return out
        used = spent / limit
        fixed = category in FIXED_CATEGORIES
        projected = spent if fixed else spent / upto.day * end.day
        if used >= 1:
            status = "over"
        elif not fixed and (used >= ALERT_THRESHOLD or projected > limit):
            status = "watch"
        else:
            status = "ok"
        out.update({"monthly_limit": limit, "used_pct": round(used * 100), "remaining": _round(limit - spent),
                    "projected_month_total": _round(projected), "status": status})
        return out


# ---------------------------------------------------------------- tool definitions for the model
def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_CATEGORY = {"type": "string", "enum": CATEGORIES}
_PERIOD = {"type": "string", "enum": ["today", "this_week", "last_week", "this_month", "last_month", "last_30_days"]}

TOOLS: list[dict] = [
    {
        "name": "add_expense",
        "description": "Save one purchase or debit to the ledger. Call once per purchase. Returns the saved record "
                       "and how much of the category's monthly budget is now used.",
        "input_schema": _obj({
            "amount": {"type": "number", "description": "Amount in birr, e.g. 350 or 80.5"},
            "category": _CATEGORY,
            "spent_on": {"type": "string", "format": "date", "description": "YYYY-MM-DD. Leave out for today."},
            "merchant": {"type": "string", "description": "Shop, restaurant or person paid, if known."},
            "note": {"type": "string", "description": "Short note, e.g. 'work lunch'."},
            "source": {"type": "string", "enum": ["chat", "receipt", "sms"], "description": "Where it came from."},
        }, ["amount", "category"]),
    },
    {
        "name": "update_expense",
        "description": "Correct a saved expense (wrong amount, category, merchant, note or date). "
                       "Find the id with list_expenses first.",
        "input_schema": _obj({
            "expense_id": {"type": "integer"},
            "amount": {"type": "number"},
            "category": _CATEGORY,
            "merchant": {"type": "string"},
            "note": {"type": "string"},
            "spent_on": {"type": "string", "format": "date", "description": "YYYY-MM-DD"},
        }, ["expense_id"]),
    },
    {
        "name": "delete_expense",
        "description": "Delete an expense that was saved by mistake or twice. Find the id with list_expenses first.",
        "input_schema": _obj({"expense_id": {"type": "integer"}}, ["expense_id"]),
    },
    {
        "name": "list_expenses",
        "description": "Show the most recent expenses, newest first, optionally for one category.",
        "input_schema": _obj({
            "limit": {"type": "integer", "description": "How many to return, 1 to 50. Default 10."},
            "category": _CATEGORY,
        }, []),
    },
    {
        "name": "get_spending",
        "description": "Total spending for a period, broken down by category, with the top merchants. "
                       "Weeks start on Monday.",
        "input_schema": _obj({"period": _PERIOD, "category": _CATEGORY}, ["period"]),
    },
    {
        "name": "set_budget",
        "description": "Create or change the monthly spending limit for a category.",
        "input_schema": _obj({"category": _CATEGORY,
                              "monthly_limit": {"type": "number", "description": "Limit in birr per month."}},
                             ["category", "monthly_limit"]),
    },
    {
        "name": "check_budgets",
        "description": "Compare this month's spending with every monthly limit. Status is 'ok', 'watch' "
                       "(80%+ used or on pace to go over) or 'over'.",
        "input_schema": _obj({}, []),
    },
    {
        "name": "forecast_month",
        "description": "Estimate the end-of-month total at the current pace, for all spending or one category.",
        "input_schema": _obj({"category": _CATEGORY}, []),
    },
    {
        "name": "remember",
        "description": "Save a lasting fact about the user so you get it right next time, e.g. "
                       "key 'merchant:Habesha Restaurant' value 'usually Business (work lunches)', or "
                       "key 'preference:currency' value 'show amounts in birr'.",
        "input_schema": _obj({"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
    },
]

_ALLOWED_ARGS = {t["name"]: set(t["input_schema"]["properties"]) for t in TOOLS}


def _portable(schema: dict) -> dict:
    """Drop JSON-schema keywords some free models reject. run_tool checks inputs anyway."""
    out = {k: v for k, v in schema.items() if k not in ("additionalProperties", "format")}
    if "properties" in out:
        out["properties"] = {k: _portable(v) for k, v in out["properties"].items()}
    return out


def openai_tools() -> list[dict]:
    """The tool list in the OpenAI-compatible format that Gemini and Ollama understand."""
    tools = []
    for t in TOOLS:
        fn = {"name": t["name"], "description": t["description"]}
        if t["input_schema"]["properties"]:  # tools without inputs leave "parameters" out
            fn["parameters"] = _portable(t["input_schema"])
        tools.append({"type": "function", "function": fn})
    return tools


def run_tool(ledger: Ledger, name: str, tool_input: Any) -> tuple[str, bool]:
    """Run one tool call. Returns (JSON text for the model, is_error)."""
    try:
        if name not in _ALLOWED_ARGS:
            raise ToolError(f"Unknown tool '{name}'.")
        if not isinstance(tool_input, dict):
            raise ToolError("Tool input must be a JSON object.")
        extra = set(tool_input) - _ALLOWED_ARGS[name]
        if extra:
            raise ToolError(f"Unexpected fields: {', '.join(sorted(extra))}.")
        result = getattr(ledger, name)(**tool_input)
        return json.dumps(result, ensure_ascii=False), False
    except ToolError as exc:
        return json.dumps({"error": str(exc)}), True
    except (TypeError, ValueError) as exc:  # missing argument or a value of the wrong kind
        return json.dumps({"error": f"Invalid input: {exc}"}), True
