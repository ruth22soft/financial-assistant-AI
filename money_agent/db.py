"""SQLite storage: expenses, monthly budgets, and the agent's long-term memory."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    spent_on   TEXT    NOT NULL,              -- YYYY-MM-DD
    amount     REAL    NOT NULL CHECK (amount > 0),
    category   TEXT    NOT NULL,
    merchant   TEXT,
    note       TEXT,
    source     TEXT    NOT NULL DEFAULT 'chat', -- chat | receipt | sms | seed
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses (spent_on);

CREATE TABLE IF NOT EXISTS budgets (
    category      TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL CHECK (monthly_limit > 0)
);

CREATE TABLE IF NOT EXISTS memory (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: Path | str, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (and create if needed) the database. Use ":memory:" for tests.

    The web app passes check_same_thread=False because Streamlit may serve one
    browser session from different threads.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
