"""Stage 7 of the design pass (11.11.2026): what was left."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def test_chart_legends_use_round_dots_like_the_rest_of_the_app():
    js = (_ROOT / "frontend/static/js/months.js").read_text(encoding="utf-8")
    assert js.count("usePointStyle: true, pointStyle: 'circle'") == 2


def test_a_fade_above_the_dock_that_takes_no_taps():
    i = _CSS.index("body:has(> .bottom-nav)::after {")
    rule = _CSS[i:_CSS.index("}", i)]
    assert "pointer-events: none" in rule
    assert "var(--color-bg)" in rule
    assert "z-index: calc(var(--z-dock) - 1)" in rule


def test_the_page_is_dark_behind_the_clock_without_an_added_strip():
    """iOS 26 tints the status bar from the page colour (11.11, an experiment)"""
    i = _CSS.index("body:has(> .page-hero) {")
    rule = _CSS[i:_CSS.index("}", i)]
    assert "background-color: var(--hero-bg-2)" in rule
    assert "background-position: 0 120px" in rule
    assert ".status-strip" not in _CSS          # the first try stays reverted
