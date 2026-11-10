"""The touch layer: what separates "a website on a phone" from an app (Matan,
7.10, touch round).

Checked in the browser (Chromium with touch): zero controls with a tap flash, all
`manipulation`, no button selectable, and the bar disappears while typing. The
feel itself (flash, zoom, keyboard) is checked on a real phone.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
_TEMPLATES = sorted((_ROOT / "frontend/templates").glob("*.html"))


def _rule(selector):
    i = _CSS.index(selector + " {")
    return _CSS[i:_CSS.index("}", i)]


def test_no_gray_flash_on_tap_anywhere():
    assert "-webkit-tap-highlight-color: transparent;" in _rule("html")


def test_controls_skip_double_tap_zoom_and_text_selection():
    assert "touch-action: manipulation;" in _rule('button, a, [role="button"], label, summary, select')
    sel = _rule('button, [role="button"], summary, .nav-item, a.btn-sm, a.submit-btn')
    assert "user-select: none;" in sel and "-webkit-user-select: none;" in sel
    assert not re.search(r"(^|\n)body\s*\{[^}]*user-select:\s*none", _CSS), "ordinary text must stay copyable"


def test_every_hover_waits_for_a_mouse():
    """`:hover` without a mouse sticks after a touch: the main button on the landing
    page stayed raised."""
    ungated = []
    depth, gated = 0, []
    for line in _CSS.splitlines():
        if "@media" in line and "hover: hover" in line:
            gated.append(depth)
        if ":hover" in line and not gated:
            ungated.append(line.strip())
        depth += line.count("{") - line.count("}")
        while gated and depth <= gated[-1]:
            gated.pop()
    assert not ungated, ungated


@pytest.mark.parametrize("tpl", _TEMPLATES, ids=lambda p: p.name)
def test_the_keyboard_resizes_the_page_on_android(tpl):
    html = tpl.read_text(encoding="utf-8")
    if 'name="viewport"' in html:
        assert "interactive-widget=resizes-content" in html
        assert "user-scalable=no" not in html and "maximum-scale" not in html


def test_the_dock_steps_aside_while_typing():
    i = _CSS.index("@media (pointer: coarse) {\n    body:has(")
    block = _CSS[i:_CSS.index("\n}\n", i)]
    assert ".bottom-nav" in block and ".fab" in block and "visibility: hidden;" in block


@pytest.mark.parametrize("selector", [".search-results", ".tx-screen-body", ".modal-sheet", ".icon-picker-card",
                                      ".budget-sheet-list", ".cat-month-list"])
def test_scrolling_inside_a_window_stays_inside(selector):
    assert "overscroll-behavior: contain;" in _rule(selector)
