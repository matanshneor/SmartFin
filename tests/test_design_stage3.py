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


# ─── 2. Totals in whole shekels ───────────────────────────────────────

@pytest.mark.parametrize("tpl,snippet", [
    ("index.html", '<p class="card-amount">₪{{ summary.expense | round | int | money }}</p>'),
    ("month.html", '₪{{ summary.expense | round | int | money }}</p>'),
    ("month.html", '<p class="savings-total-value">₪{{ savings_total | round | int | money }}</p>'),
    ("project_detail.html", '₪{{ type_totals[typ] | round | int | money }}</p>'),
])
def test_totals_are_whole_shekels(tpl, snippet):
    """Totals at a glance in whole shekels, like the home balance; the rows keep
    their agorot (`format_money`), so every amount you can tap is exact"""
    assert snippet in _tpl(tpl)


# ═══ Stage 4: calmer screens ═══════════════════════════════════════════

def test_a_new_family_sees_one_way_to_start():
    """Three ₪0 tiles and an empty list under "בואו נתחיל" (11.11)"""
    tpl = _tpl("index.html")
    for marker in ('<section class="summary-cards"', '<section class="transactions-section">'):
        before = tpl[:tpl.index(marker)]
        assert before.rstrip().endswith("{% if not is_new_family %}") or \
            before[before.rindex("{% if not is_new_family %}"):].count("{% endif %}") == 0, marker


def test_the_week_card_is_lower():
    tpl = _tpl("_week_card.html")
    assert "d.total / week.max * 52" in tpl
    assert "height: 90px" in _rule(".week-bars")


def test_an_empty_day_is_one_line_without_zero():
    tpl = _tpl("_week_card.html")
    assert "data-empty" in tpl and "{% if d.total %}<span>₪{{ d.total | money }}</span>{% endif %}" in tpl
    assert "display: none" in _rule(".week-detail[hidden]")


def test_home_shows_one_announcement_per_visit():
    core = (_ROOT / "frontend/static/js/core.js").read_text(encoding="utf-8")
    i = core.index("One announcement per visit on home")
    block = core[i:core.index('"Project finished?"', i)]
    assert "['#memberJoined', '.recap-wrap', '.year-card-wrap']" in block
    # Waiting is not "seen": nothing here writes to storage
    assert "localStorage" not in block
