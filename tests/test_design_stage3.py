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
