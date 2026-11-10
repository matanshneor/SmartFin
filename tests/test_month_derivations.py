"""
The month page derived the same rows again and again.

Seven separate queries read the same date range of the same family, and each
rewrote the household rules for itself: a project doesn't go into the monthly
balance, a transaction with no owner is "shared", someone else's personal project
isn't shown by name. A rule written seven times is a rule that will change in six
of them, and there was already such a bug.

Now there is one query and derivations from it. The tests here lock the rules in
one place: exactly the same numbers, from the same rows.
"""
import pytest

from backend import supabase_config as db

pytestmark = pytest.mark.unit

_ME  = "11111111-1111-1111-1111-111111111111"
_YOU = "22222222-2222-2222-2222-222222222222"


# The breakdown is grouped by the category's **id**, not its name, so a row must
# carry `category_id` exactly as the real query returns it.
_CAT_IDS = {
    "סופר":   "c0000000-0000-0000-0000-000000000001",
    "דלק":    "c0000000-0000-0000-0000-000000000002",
    "חשמל":   "c0000000-0000-0000-0000-000000000003",
    "משכורת": "c0000000-0000-0000-0000-000000000004",
    "קרן":    "c0000000-0000-0000-0000-000000000005",
    "שיפוץ":  "c0000000-0000-0000-0000-000000000006",
}


def _row(type_, amount, *, cat=None, user=None, name=None, project=None):
    return {
        "id": f"{type_}-{amount}", "type": type_, "amount": amount,
        "date": "2026-09-10", "note": "",
        "user_id": user,
        "project_id": "p1" if project else None,
        "category_id": _CAT_IDS.get(cat),
        "categories": {"name": cat, "icon": "🧾"} if cat else None,
        "project_categories": None,
        "profiles": {"name": name, "workplace": None} if name else None,
        "projects": project,
    }


ROWS = [
    _row("income",  10000, cat="משכורת", user=_ME,  name="מתן שניאור"),
    _row("expense",   400, cat="סופר",   user=_ME,  name="מתן שניאור"),
    _row("expense",   250, cat="סופר"),                      # shared
    _row("expense",   100, cat="דלק",    user=_YOU, name="אור לוי"),
    _row("savings",  1000, cat="קרן",    user=_ME,  name="מתן שניאור"),
    # A project transaction: large on purpose, so it is clearly visible if it
    # leaks into the summary
    _row("expense", 50000, cat="שיפוץ", user=_ME, name="מתן שניאור",
         project={"owner_id": _YOU, "name": "הפרויקט של אור", "icon": "🔨"}),
]

CATEGORIES = [
    {"id": _CAT_IDS["סופר"],   "name": "סופר", "icon": "🛒", "type": "expense"},
    {"id": _CAT_IDS["דלק"],    "name": "דלק",  "icon": "⛽", "type": "expense"},
    {"id": _CAT_IDS["חשמל"],   "name": "חשמל", "icon": "💡", "type": "expense"},   # no expense this month
    {"id": _CAT_IDS["משכורת"], "name": "משכורת", "icon": "💰", "type": "income"},
]


# ─── The rule all summaries share: a project isn't part of the normal month ─

def test_a_project_expense_does_not_touch_the_monthly_balance():
    s = db.summary_from_rows(ROWS)
    assert s["income"]    == 10000
    assert s["expense"]   == 750      # 400 + 250 + 100, without the project's 50,000
    assert s["savings"]   == 1000
    assert s["balance"]   == 9250
    assert s["remaining"] == 8250


def test_a_project_expense_does_not_touch_the_category_breakdown():
    names = {c["name"] for c in db.category_breakdown_from_rows(ROWS, CATEGORIES, "expense")}
    assert "שיפוץ" not in names, "a project expense was counted in the category breakdown"


def test_a_project_expense_does_not_touch_the_member_breakdown():
    total = sum(m["expense"] for m in db.member_breakdown_from_rows(ROWS, "expense"))
    assert total == 750


def test_a_project_expense_is_still_shown_in_the_list():
    """It isn't in the balance, but it did happen: whoever looks at the month's list
    needs to see it."""
    ids = {t["id"] for t in db.month_transactions_from_rows(ROWS, None, _YOU)}
    assert "expense-50000" in ids


# ─── Privacy: someone else's personal project ───────────────────────────────

def test_a_personal_project_of_someone_else_stays_out_of_the_list():
    ids = {t["id"] for t in db.month_transactions_from_rows(ROWS, None, _ME)}
    assert "expense-50000" not in ids, "a transaction from someone else's personal project was shown"


def test_but_its_owner_does_see_it():
    ids = {t["id"] for t in db.month_transactions_from_rows(ROWS, None, _YOU)}
    assert "expense-50000" in ids


# ─── The category breakdown ─────────────────────────────────────────────────

def test_a_category_with_no_spending_this_month_is_still_listed_at_zero():
    """Otherwise a category vanishes from the page exactly in the month you spent
    nothing on it, and it looks as if it was deleted."""
    rows = {c["name"]: c for c in db.category_breakdown_from_rows(ROWS, CATEGORIES, "expense")}
    assert rows["חשמל"]["total"] == 0


def test_categories_are_ordered_by_size():
    out = db.category_breakdown_from_rows(ROWS, CATEGORIES, "expense")
    assert [c["name"] for c in out] == ["סופר", "דלק", "חשמל"]
    assert out[0]["total"] == 650      # 400 + 250, two rows in the same category
    assert out[0]["pct"]   == 87       # 650 of 750


def test_income_and_expense_do_not_mix():
    out = db.category_breakdown_from_rows(ROWS, CATEGORIES, "income")
    assert [c["name"] for c in out] == ["משכורת"]
    assert out[0]["total"] == 10000


# ─── Split between family members ───────────────────────────────────────────

def test_a_transaction_with_no_owner_is_shared():
    out = db.member_breakdown_from_rows(ROWS, "expense")
    assert {m["name"]: m["expense"] for m in out} == {
        "מתן": 400.0, "משותפת": 250.0, "אור": 100.0}


def test_members_are_ordered_by_size():
    assert [m["name"] for m in db.member_breakdown_from_rows(ROWS, "expense")] == \
        ["מתן", "משותפת", "אור"]


# ─── An empty month ─────────────────────────────────────────────────────────

def test_an_empty_month_is_zero_and_not_a_crash():
    s = db.summary_from_rows([])
    assert s == {"income": 0.0, "expense": 0.0, "savings": 0.0,
                 "balance": 0.0, "remaining": 0.0, "expense_pct": 0}
    assert db.member_breakdown_from_rows([], "expense") == []
    assert db.month_transactions_from_rows([], None, _ME) == []
