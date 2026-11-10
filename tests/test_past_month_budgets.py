"""A month that ended showed a budget as if it were still open.

Matan asked: "on the past months page, should 'set a budget for the category'
appear for each category? I don't think you set a budget for past months."

He was right, and the root beneath it is bigger than the link: **the budget is
stored as a global map by category** (families.settings.limits), with no month
and no history. Hence two separate faults on one page:

1. The link promised something that doesn't happen. Tapping from March opens
   settings and sets an amount that applies **from today on**; March won't
   change. The screen implies you are budgeting the month you are looking at.

2. And even when there already is a budget, a past month said "נשאר ₪500 מתוך
   ₪2,000". A month that ended has no "remaining": there is nothing to spend.
   The ₪2,000 is also today's budget, not necessarily what it was then.

The tests here render the real template and check what is seen on screen, not the
code that is supposed to produce it.
"""

import pytest

from backend.app import app
from backend import supabase_config as db

pytestmark = pytest.mark.unit

_CAT = "cat-groceries"


# The template reads item.name/icon and filters to total > 0.
def _row(spent):
    return {"category_id": _CAT, "name": "מכולת", "icon": "🛒",
            "total": spent, "pct": 40, "is_project": False}


def _breakdown(spent, budget=2000):
    """A real breakdown row, through the same function production runs."""
    settings = {"limits": {_CAT: {"amount": budget, "alert": True}}}
    return db.apply_budgets([_row(spent)], settings)


def _render(breakdown, is_current):
    ctx = dict(
        active_page="month", user={"id": "x", "name": "מתן", "avatar_initial": "מ"},
        summary={"income": 10000, "expense": 5000, "savings": 0, "balance": 5000,
                 "remaining": 5000, "expense_pct": 50},
        expense_breakdown=breakdown, income_breakdown=[], savings_breakdown=[],
        member_breakdowns=[], member_colors={},
        month_label="מרץ 2026", year=2026, month=3, is_current=is_current,
        strip_months=[], hebrew_months=["", "ינואר"],
        project_month={"transactions": [], "expense": 0, "income": 0},
        summary_data={}, expense_data=[], members_data=[],
        family_settings=dict(db.DEFAULT_FAMILY_SETTINGS),
    )
    tx = {"id": "1", "type": "expense", "amount": 1240, "date": "2026-03-04",
          "description": "קניות", "category_name": "מכולת", "category_icon": "🛒",
          "user_id": None, "user_name": "משותף", "is_recurring": False,
          "project_id": None, "has_receipt": False, "recurring_parent_id": None,
          "workplace": None, "recurring_frequency": None, "project_name": None,
          "project_icon": None, "category_id": _CAT, "project_category_id": None,
          "recurring_end_date": None}
    with app.test_request_context("/month"):
        return app.jinja_env.get_template("month.html").render(
            month_transactions=[tx], **ctx)


# ─── The link ────────────────────────────────────────────────────────────────

def test_a_closed_month_does_not_offer_to_budget_it():
    """Matan's question."""
    rows = [_row(1240)]

    html = _render(rows, is_current=False)
    assert 'id="budgetsOpen"' not in html and "קביעת תקציב לקטגוריה" not in html


def test_the_current_month_still_offers_it():
    """This is the moment a person realises they need a budget: they see ₪1,240 on
    groceries. Removing it from the current month would bury the central feature
    back behind four screens."""
    rows = [_row(1240)]

    # one button at the top of the card (Matan, 30.9), not a link after each row
    html = _render(rows, is_current=True)
    assert 'id="budgetsOpen"' in html and 'id="sf-budgets"' in html


# ─── The wording ─────────────────────────────────────────────────────────────

def _budget_text(html):
    """Only the budget row. The word "נשאר" also appears in "נשאר בעו״ש החודש"
    elsewhere on the page, and a check over all the HTML would fail on it."""
    import re
    m = re.search(r'<p class="cat-budget-text">(.*?)</p>', html, re.S)
    assert m, "the budget row wasn't rendered at all"
    return " ".join(m.group(1).split())


def test_a_closed_month_does_not_say_what_is_left_to_spend():
    """The heart. "נשאר ₪500" on March is a sentence about today, stuck onto the
    past."""
    text = _budget_text(_render(_breakdown(spent=1500), is_current=False))

    assert "נשאר" not in text, f"still present tense: {text}"
    assert text == "₪1,500 מתוך תקציב של ₪2,000"


def test_the_current_month_still_says_what_is_left():
    html = _render(_breakdown(spent=1500), is_current=True)

    assert _budget_text(html) == "נשאר ₪500 מתוך ₪2,000"


def test_an_overrun_reads_the_same_either_way():
    """"חריגה של ₪300" is true in both tenses, and there is no reason to word it
    twice."""
    over_now = _render(_breakdown(spent=2300), is_current=True)
    over_then = _render(_breakdown(spent=2300), is_current=False)

    for html in (over_now, over_then):
        assert _budget_text(html) == "חריגה של ₪300 מתוך ₪2,000"


def test_a_closed_month_shows_the_sentence_without_a_bar():
    """Matan (13.10): "the line isn't useful and isn't informative": only the
    sentence. In a month that ended the sentence still says how much went out
    against the budget, so you can look back."""
    html = _render(_breakdown(spent=1500), is_current=False)
    assert "cat-budget-track" not in html and "cat-budget-fill" not in html
    assert _budget_text(html) == "₪1,500 מתוך תקציב של ₪2,000"


def test_the_number_shown_is_what_was_actually_spent():
    """budget_left is what remains, not what went out. Using it in the new wording
    would turn "הוצאת ₪1,500" into "הוצאת ₪500"."""
    text = _budget_text(_render(_breakdown(spent=1500, budget=2000), is_current=False))

    assert "₪1,500 מתוך" in text
    assert "₪500 מתוך" not in text
