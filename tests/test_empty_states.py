"""
Tests for empty states.

A new family lands on the month page straight from the dashboard and sees five
empty chart cards one after another, with no explanation: a screen that looks
broken. It happens once per family, and that once decides whether they stay.

Every other page already handles this state; the month page was the only one
forgotten. The last test here is the one that will catch the next page forgotten.
"""
from pathlib import Path

import pytest

from backend.app import app
from backend import supabase_config as db

pytestmark = pytest.mark.unit

_TPL = Path(__file__).resolve().parent.parent / "frontend/templates"

_TX = {
    "id": "1", "type": "expense", "amount": 50, "date": "2026-09-01",
    "description": "x", "category_name": "מזון", "category_icon": "🍔",
    "user_id": None, "user_name": "משותף", "is_recurring": False,
    "project_id": None, "has_receipt": False, "recurring_parent_id": None,
    "workplace": None, "recurring_frequency": None, "project_name": None,
    "project_icon": None, "category_id": None, "project_category_id": None,
    "recurring_end_date": None,
}


def _render_month(transactions, is_current=True):
    ctx = dict(
        active_page="month", user={"id": "x", "name": "מתן", "avatar_initial": "מ"},
        summary={"income": 0, "expense": 0, "savings": 0, "balance": 0,
                 "remaining": 0, "expense_pct": 0},
        expense_breakdown=[], income_breakdown=[], savings_breakdown=[],
        member_breakdowns=[], member_colors={},
        month_label="ספטמבר 2026", year=2026, month=9, is_current=is_current,
        strip_months=[], hebrew_months=["", "ינואר"],
        project_month={"transactions": [], "expense": 0, "income": 0},
        summary_data={}, expense_data={}, members_data={},
        family_settings=dict(db.DEFAULT_FAMILY_SETTINGS),
    )
    with app.test_request_context("/month"):
        return app.jinja_env.get_template("month.html").render(
            month_transactions=transactions, **ctx)


def test_a_month_with_nothing_in_it_explains_itself():
    html = _render_month([])

    assert "empty-state" in html
    assert "אין עדיין עסקאות החודש" in html


def test_it_does_not_render_five_blank_charts():
    """This is what the user actually saw: five empty cards one after another."""
    html = _render_month([])

    assert html.count('"chart-card"') == 1


def test_an_empty_month_does_not_download_chart_js():
    """70KB compressed with nothing to draw."""
    assert "chart.umd" not in _render_month([])


def test_a_past_month_offers_the_way_back():
    """'No transactions recorded in August' with no way back is a dead end."""
    html = _render_month([], is_current=False)

    assert "לחזור לחודש הנוכחי" in html


def test_a_month_with_data_is_untouched():
    """Control: the charts and the library must come back as soon as there is
    something to show."""
    html = _render_month([_TX])

    assert "empty-state" not in html
    # "Expenses vs savings" was removed (30.9). A type with no transactions: one
    # row and not a card (Matan, 11.10): here there is only an expense, so income
    # and savings are rows.
    assert html.count('"chart-card"') == 2
    assert html.count('class="empty-row"') == 2
    assert "chart.umd" in html


def test_every_page_that_can_be_empty_says_so():
    """This is the test that will catch the next page forgotten."""
    for page in ("index.html", "month.html", "months.html",
                 "projects.html", "project_detail.html", "settings.html"):
        html = (_TPL / page).read_text(encoding="utf-8")
        # On projects the empty state is an invitation to start, with ideas
        # (Matan, 5.10)
        marker = "project-ideas-title" if page == "projects.html" else "empty-state"
        assert marker in html, f"{page} has no empty state"
