"""Stage 7 of the design pass (11.11.2026): what was left."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def test_the_comparison_table_colours_only_the_balance():
    i = _CSS.index(".months-table .inc,")
    rule = _CSS[i:_CSS.index("}", i)]
    assert ".months-table .exp" in rule and ".months-table .sav" in rule
    assert "var(--color-text-soft)" in rule
    assert ".months-table .bal.surplus { color: var(--color-income); }" in _CSS


def test_chart_legends_use_round_dots_like_the_rest_of_the_app():
    js = (_ROOT / "frontend/static/js/months.js").read_text(encoding="utf-8")
    assert js.count("usePointStyle: true, pointStyle: 'circle'") == 2


def test_a_fade_above_the_dock_that_takes_no_taps():
    i = _CSS.index("body:has(> .bottom-nav)::after {")
    rule = _CSS[i:_CSS.index("}", i)]
    assert "pointer-events: none" in rule
    assert "var(--color-bg)" in rule
    assert "z-index: calc(var(--z-dock) - 1)" in rule
