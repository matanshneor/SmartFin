"""Comparing a category over months: the "by category" card on the comparison
page (Matan, 30.9).

The same months as the chart above it (up to the last 6 with data), household
expenses only (no projects), a total per category per month. The average and
the most expensive month come from the finished months: the current month isn't
over yet, and it would pull the average down.
"""
import datetime

import pytest

from backend import supabase_config as db
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_TODAY = datetime.date(2026, 9, 30)
_CATS = [{"id": "food", "name": "סופר", "icon": "🛒", "type": "expense"},
         {"id": "fuel", "name": "דלק", "icon": "⛽", "type": "expense"},
         {"id": "gift", "name": "מתנות", "icon": "🎁", "type": "expense"},
         {"id": "sal", "name": "משכורת", "icon": "💼", "type": "income"}]
_MONTHS = [(2026, 7), (2026, 8), (2026, 9)]


def _tx(cat, amount, date, type_="expense", project=None):
    return {"id": f"{cat}{amount}{date}", "family_id": _FAM, "type": type_, "amount": amount,
            "category_id": cat, "date": date, "project_id": project}


def _trend(rows, monkeypatch, months=_MONTHS):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    return db.category_trend(_FAM, months, _CATS, today=_TODAY)


def test_a_total_per_category_per_month(monkeypatch):
    t = _trend([_tx("food", 100, "2026-07-03"), _tx("food", 50, "2026-07-20"),
                _tx("food", 300, "2026-08-10"), _tx("food", 90, "2026-09-02"),
                _tx("fuel", 400, "2026-08-11")], monkeypatch)

    assert [m["label"] for m in t["months"]] == ["יולי", "אוג׳", "ספט׳"]
    food = next(c for c in t["categories"] if c["key"] == "food")
    assert food["values"] == [150, 300, 90]
    assert (food["name"], food["icon"]) == ("סופר", "🛒")
    fuel = next(c for c in t["categories"] if c["key"] == "fuel")
    assert fuel["values"] == [0, 400, 0]


def test_average_and_priciest_come_from_finished_months_only(monkeypatch):
    t = _trend([_tx("food", 100, "2026-07-03"), _tx("food", 300, "2026-08-10"),
                _tx("food", 5, "2026-09-02")], monkeypatch)

    food = t["categories"][0]
    assert food["avg"] == 200            # (100 + 300) / 2: without September
    assert food["max_label"] == "אוגוסט" and food["max"] == 300
    assert food["current"] == 5


def test_project_money_income_and_empty_categories_are_left_out(monkeypatch):
    t = _trend([_tx("food", 100, "2026-07-03"), _tx("food", 9000, "2026-08-10", project="p1"),
                _tx("sal", 8000, "2026-08-01", type_="income")], monkeypatch)

    assert [c["key"] for c in t["categories"]] == ["food"]      # without fuel/gifts (zero), without salary
    assert t["categories"][0]["values"] == [100, 0, 0]


def test_the_biggest_category_comes_first(monkeypatch):
    t = _trend([_tx("food", 100, "2026-07-03"), _tx("fuel", 500, "2026-08-10")], monkeypatch)

    assert [c["key"] for c in t["categories"]] == ["fuel", "food"]


def test_a_single_month_has_no_average_yet(monkeypatch):
    t = _trend([_tx("food", 100, "2026-09-03")], monkeypatch, months=[(2026, 9)])

    assert t["categories"][0]["avg"] is None and t["categories"][0]["max_label"] is None


def test_no_months_no_query(monkeypatch):
    def boom():
        raise AssertionError("a fetch with no months")
    monkeypatch.setattr(db, "get_client", boom)

    assert db.category_trend(_FAM, [], _CATS, today=_TODAY) == {"months": [], "categories": []}


def test_this_month_is_only_this_month(monkeypatch):
    """The current month with no transactions doesn't go into the archive, so the
    last month in the window is August, and showing it as "this month" would be
    a lie."""
    t = _trend([_tx("food", 100, "2026-07-03"), _tx("food", 300, "2026-08-10")], monkeypatch,
               months=[(2026, 7), (2026, 8)])

    assert t["categories"][0]["current"] is None
    assert t["categories"][0]["avg"] == 200


def test_each_month_carries_its_transactions_for_the_window(monkeypatch):
    """Tapping a month's bar opens a sheet with the category's expenses in that
    month (Matan, 30.9), newest first."""
    t = _trend([_tx("food", 100, "2026-07-03"), _tx("food", 50, "2026-07-20"),
                _tx("food", 300, "2026-08-10")], monkeypatch)

    food = t["categories"][0]
    july = food["items"][0]
    assert [(i["date"], i["amount"]) for i in july] == [("2026-07-20", 50), ("2026-07-03", 100)]
    assert [i["amount"] for i in food["items"][1]] == [300]
    assert food["items"][2] == []
