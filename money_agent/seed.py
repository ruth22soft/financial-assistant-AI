"""Fill the database with about six weeks of realistic sample spending, for demos.

Merchant names are made up. The data is random but repeatable (fixed seed), and it
always ends today, so the dashboard and alerts look current whenever you run it.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from .tools import Ledger

BUDGETS = {
    "Food & Drinks": 3600,
    "Transport": 2500,
    "Groceries": 4000,
    "Airtime & Internet": 700,
    "Entertainment": 1000,
    "Rent & Utilities": 13000,
}


def _expenses_for(day: date, rng: random.Random):
    weekday = day.weekday() < 5
    if day.day == 1:
        yield 12000, "Rent & Utilities", "Landlord", "monthly rent"
    if day.day == 10:
        yield 450, "Rent & Utilities", "Electricity bill", None
    if day.day in (3, 18):
        yield 300, "Airtime & Internet", "Mobile data bundle", None
    if rng.random() < 0.5:
        yield rng.choice([60, 70, 80, 90]), "Food & Drinks", rng.choice(["Bole Cafe", "Piazza Coffee House"]), None
    if weekday and rng.random() < 0.45:
        yield (rng.randrange(150, 320, 10), "Food & Drinks",
               rng.choice(["Habesha Restaurant", "Kitfo House", "Office canteen"]), "lunch")
    if rng.random() < 0.22:
        yield rng.randrange(160, 280, 10), "Transport", rng.choice(["Ride to Bole", "Ride home"]), None
    if weekday and rng.random() < 0.6:
        yield rng.choice([20, 25, 30, 40]), "Transport", "Minibus taxi", None
    if day.weekday() == 5:
        yield rng.randrange(600, 1400, 50), "Groceries", rng.choice(["Mercato market", "Corner supermarket"]), None
    if day.weekday() == 6 and rng.random() < 0.4:
        yield rng.choice([200, 250, 300]), "Entertainment", "Cinema", None
    if rng.random() < 0.03:
        yield rng.randrange(120, 400, 10), "Health", "Pharmacy", None


def seed(ledger: Ledger, days: int = 45, reset: bool = False) -> int:
    conn = ledger.conn
    if reset:
        conn.executescript("DELETE FROM expenses; DELETE FROM budgets; DELETE FROM memory;")
    rng = random.Random(42)
    today = ledger.today
    count = 0
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        for amount, category, merchant, note in _expenses_for(day, rng):
            conn.execute(
                "INSERT INTO expenses (spent_on, amount, category, merchant, note, source) VALUES (?, ?, ?, ?, ?, 'seed')",
                (day.isoformat(), amount, category, merchant, note))
            count += 1
    for category, limit in BUDGETS.items():
        conn.execute("INSERT OR REPLACE INTO budgets (category, monthly_limit) VALUES (?, ?)", (category, limit))
    count += _set_up_demo_story(ledger)
    conn.commit()
    return count


def _set_up_demo_story(ledger: Ledger) -> int:
    """Make sure Food & Drinks is on pace to go over, like the "Day 18" story in the slides.

    Target: the share of the food budget a person would have used by day 18 at 85%,
    scaled to today's date. One "Friends' dinner" fills any gap.
    """
    today = ledger.today
    limit = BUDGETS["Food & Drinks"]
    target = round(limit * 0.85 * min(1.0, today.day / 18), -1)
    spent = ledger.get_spending("this_month", category="Food & Drinks")["total"]
    gap = round(target - spent, -1)
    if gap < 50:
        return 0
    ledger.conn.execute(
        "INSERT INTO expenses (spent_on, amount, category, merchant, note, source) "
        "VALUES (?, ?, 'Food & Drinks', 'Lucy Lounge', 'friends'' dinner', 'seed')",
        (today.isoformat(), gap))
    return 1
