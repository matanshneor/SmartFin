"""The + next to the bar, and tabs that don't go into history (Matan, 11.10).

The + floated over the content, and at a certain scroll point it sat exactly under
"for the whole month" and looked like it belonged to it. Now it is a gold circle
to the left of the bar, on the same row.

"When I scroll the whole page, it moves me between pages": on iPhone a swipe from
the edge is the system's "back", and every tap on the bar added a page to
history. Now the tabs replace each other. In the browser: after 5 taps on the bar
the history doesn't grow, and smooth month switching keeps working.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
_CORE = (_ROOT / "frontend/static/js/core.js").read_text(encoding="utf-8")


def _rule(selector):
    body = _CSS[_CSS.index(selector + " {"):]
    return body[:body.index("}")]


def test_the_plus_is_a_gold_circle_beside_the_bar():
    fab = _rule(".fab")
    assert "border-radius: 50%;" in fab
    assert "width: var(--fab-size);" in fab
    assert "(var(--dock-height) - var(--fab-size)) / 2" in fab      # at the bar's mid-height
    nav = _rule(".bottom-nav")
    assert "left: calc(var(--dock-side) + var(--fab-size) + 14px);" in nav


def test_pages_without_a_plus_get_the_whole_bar():
    assert "body.no-fab .bottom-nav { left: var(--dock-side); }" in _CSS


def test_the_page_no_longer_keeps_room_for_a_floating_plus():
    assert "96px" not in _rule(".main-content")


def test_switching_tabs_does_not_add_to_history():
    block = _CORE[_CORE.index("const TABS = ['/', '/month', '/months', '/projects', '/settings'];"):]
    block = block[:block.index("})();")]
    assert "location.replace(url.href);" in block
    assert "window.addEventListener('click'" in block          # after month.js
    assert "if (e.defaultPrevented" in block


def test_android_sideways_back_is_off():
    assert "overscroll-behavior-x: none;" in _rule("html")
