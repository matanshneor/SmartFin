"""
The landing page promised two things the product doesn't deliver.

**A hard-coded month.** "ספטמבר 2026" was written fixed in the mockup. Right on
the day it was written, and wrong from October 1st, and an old date on a
marketing page reads like an abandoned project.

**An alert at a sensitivity that doesn't exist.** The main example showed "18%
above average", while the real alert fires only from 50% above the three-month
average **and** ₪300 above it. I.e. we demonstrated a feature that won't work, and
whoever signed up because of it would wait for an alert that never comes.
"""
import re
from pathlib import Path

import pytest

from backend import clock, supabase_config as db
from backend.app import app, _month_label

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_HTML = (_ROOT / "frontend/templates/landing.html").read_text(encoding="utf-8")


@pytest.fixture
def anon():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ─── The month ──────────────────────────────────────────────────────────────

def test_the_month_is_not_hard_coded():
    assert "ספטמבר 2026" not in _HTML
    assert "{{ demo_month }}" in _HTML


def test_the_landing_page_shows_the_current_month(anon):
    """Derived from the same Israel clock as the rest of the app."""
    html = anon.get("/?intro=1").get_data(as_text=True)
    now = clock.now()

    assert _month_label(now.year, now.month) in html


# ─── The alert ──────────────────────────────────────────────────────────────
#
# Since 11.10 an alert about a category is only a budget overage (the "above
# average" alert was removed from the product). The mockup must show an alert the
# product really fires, with its numbers.

def _demo_numbers():
    """The numbers in the mockup's alert: the expense, the budget and the overage.
    (Until 12.10 the mockup also had a "מזון" bar with the same amount; it was
    replaced with the "this week" card, as on the home page.)"""
    m = re.search(r"₪([\d,]+) מתוך תקציב של ₪([\d,]+) — חריגה של ₪([\d,]+)", _HTML)
    spend, budget, excess = (int(g.replace(",", "")) for g in m.groups())
    return spend, spend, budget, excess


def test_the_example_would_actually_fire():
    spend, _, budget, excess = _demo_numbers()
    assert spend > budget and excess == spend - budget


def test_the_alert_text_is_the_products_own_wording():
    """The same wording as `db.budget_alerts`, not marketing wording the product
    doesn't show."""
    spend, _, budget, _ = _demo_numbers()
    row = {"category_id": "c", "name": "מזון", "icon": "🛒", "total": spend}
    text = db.budget_alerts(db.apply_budgets([row], {"limits": {"c": {"amount": budget}}}))[0]["text"]
    assert text.split(": ", 1)[1] in _HTML


def test_nothing_promises_the_removed_average_alert():
    visible = re.sub(r"\{#.*?#\}", "", _HTML, flags=re.S)
    assert "מעל הממוצע" not in visible and "מהממוצע" not in visible
