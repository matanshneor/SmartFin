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


# ─── Stage 8: buttons, tags, chips ────────────────────────────────────

def test_buttons_come_in_three_heights():
    for name, value in (("btn-h-sm", "36px"), ("btn-h-md", "44px"), ("btn-h-lg", "54px")):
        assert f"--{name}: {value};" in _CSS
    i = _CSS.index("/* ─── Buttons: three heights")
    block = _CSS[i:i + 2500]
    # The general rule carries no weight, so a button that needs 44 keeps it
    assert ":where(.btn-sm) { min-height: var(--btn-h-sm); }" in block
    for sel in (".logout-btn", ".project-save-btn", ".project-save-cancel", ".delete-tx-btn"):
        assert sel in block
    assert ".hero-start-btn {\n    min-height: var(--btn-h-lg);" in block


def test_tags_come_in_one_size():
    i = _CSS.index("/* ─── Tags: one size")
    block = _CSS[i:_CSS.index("}", i)]
    for sel in (".owner-pill", ".project-tag", ".projected-tag", ".me-badge", ".manager-badge"):
        assert sel in block
    for decl in ("font-size: 0.72rem", "font-weight: 600", "padding: 2px 8px", "var(--radius-pill)"):
        assert decl in block


def test_chips_come_in_one_size_and_selected_is_bold():
    """Selected was told apart almost only by hue; now also by weight"""
    sels = (".tx-type-chip", ".tx-cat-chip", ".tx-sort-chip", ".range-chip")
    i = _CSS.index(", ".join(sels) + " {")
    rule = _CSS[i:_CSS.index("}", i)]
    for decl in ("min-height: 36px", "font-size: 0.82rem", "padding: 0 14px"):
        assert decl in rule
    active = ", ".join(s + ".active" for s in sels) + " {"
    j = _CSS.index(active)
    assert "font-weight: 700" in _CSS[j:_CSS.index("}", j)]


def test_the_hero_starts_below_the_blurred_band_under_the_clock():
    """iOS 26 blurs a band a little taller than the safe area (11.11)"""
    i = _CSS.index(".page-hero {")
    assert "padding: calc(28px + env(safe-area-inset-top, 0px)) 20px 46px;" in _CSS[i:_CSS.index("}", i)]


def test_home_has_no_letter_circle_and_search_sits_lower():
    """Matan, 11.11: the letter circle goes; search moves down a little"""
    tpl = (_ROOT / "frontend/templates/index.html").read_text(encoding="utf-8")
    assert "avatar-circle" not in tpl and "avatar-circle" not in _CSS
    assert 'id="searchOpen"' in tpl
    assert ".hero-top > .hero-actions { align-self: center; }" in _CSS
