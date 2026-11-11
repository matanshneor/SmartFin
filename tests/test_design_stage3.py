"""Stage 3 of the design pass (11.11.2026): consolidation, seen in screenshots.

Each change here is one Matan looked at side by side before it stayed.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _tpl(name):
    return (_ROOT / "frontend/templates" / name).read_text(encoding="utf-8")


def _rule(selector):
    i = _CSS.index(selector + " {")
    return _CSS[i:_CSS.index("}", i)]


# ─── 1. One balance, one look ─────────────────────────────────────────

def test_the_month_balance_matches_home():
    """₪14,856 in gold on home, ₪14,856.39 in green on the month page"""
    tpl = _tpl("month.html")
    i = tpl.index('<p class="month-net-value"\n')
    value = tpl[i:tpl.index("</p>", i)]
    assert "| round | int | money" in value
    assert "var(--gradient-gold-text)" in _rule(".month-net.surplus .month-net-value")
    assert "var(--color-gold-bright)" in _rule(".month-sticky-value.surplus")


# ─── 3. The expense total in the expense colour ───────────────────────

def test_the_expense_donut_total_is_red_not_gold():
    assert "var(--color-expense)" in _rule(".doughnut-total.expense")
    assert '<p class="doughnut-total expense">' in _tpl("month.html")
    assert '<p class="doughnut-total {{ typ }}">' in _tpl("project_detail.html")


# ─── 4. One segmented control ─────────────────────────────────────────

def test_the_chosen_tab_is_a_white_pill_everywhere():
    rule = _rule(".toggle-btn.active")
    assert "background: var(--color-card)" in rule
    assert "gradient" not in rule


@pytest.mark.parametrize("mode,color", [("income", "--color-income"), ("expense", "--color-expense"),
                                        ("savings", "--color-savings")])
def test_the_type_keeps_its_colour_in_the_text(mode, color):
    i = _CSS.index(f".modal-sheet.{mode}-mode .toggle-btn.active")
    rule = _CSS[i:_CSS.index("}", i)]
    assert f"color: var({color})" in rule and "background" not in rule


# ─── 5. "By family member" as rows ────────────────────────────────────

def test_members_are_rows_a_screen_reader_can_read():
    tpl = _tpl("month.html")
    assert 'class="member-bars"' in tpl and "{{ r.name }}" in tpl and "₪{{ r.expense | money }}" in tpl
    assert "membersChart" not in tpl
    js = (_ROOT / "frontend/static/js/month.js").read_text(encoding="utf-8")
    assert "membersChart" not in js
