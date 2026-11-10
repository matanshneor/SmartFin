"""Filtering "all transactions" by category (Matan, 30.9, option A).

Tap "expenses" and a row opens: "all expenses" followed by every category that
had expenses this month, with the amount, largest first. Every transaction gets
a category key the browser filters by.
"""
import pytest

from backend import supabase_config as db

pytestmark = pytest.mark.unit


def _tx(type_, amount, cat=None, name="", icon="📦", project_cat=None):
    return {"type": type_, "amount": amount, "category_id": cat, "category_name": name,
            "category_icon": icon, "project_category_id": project_cat}


def test_chips_per_type_biggest_first_with_totals():
    txs = [_tx("expense", 100, "food", "סופר", "🛒"), _tx("expense", 250, "fuel", "דלק", "⛽"),
           _tx("expense", 64.5, "food", "סופר", "🛒"), _tx("income", 8000, "sal", "משכורת", "💼")]

    chips = db.category_filter_chips(txs)

    assert [(c["key"], c["total"]) for c in chips["expense"]] == [("fuel", 250), ("food", 164.5)]
    assert chips["expense"][1]["name"] == "סופר" and chips["expense"][1]["icon"] == "🛒"
    assert [c["key"] for c in chips["income"]] == ["sal"]
    assert "savings" not in chips


def test_every_transaction_gets_the_key_it_is_filtered_by():
    txs = [_tx("expense", 10, "food", "סופר"), _tx("expense", 20, None, "חומרים", project_cat="pc1"),
           _tx("expense", 30, None, "ללא קטגוריה")]

    chips = db.category_filter_chips(txs)

    assert [t["cat_key"] for t in txs] == ["food", "pc-pc1", "none"]
    # Two different project categories with the same name don't merge, and a
    # regular category doesn't mix with a project's
    assert {c["key"] for c in chips["expense"]} == {"food", "pc-pc1", "none"}
