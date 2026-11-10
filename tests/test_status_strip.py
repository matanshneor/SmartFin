"""The strip behind the clock and battery (Matan, 10.11.2026).

iOS 26 tints the status bar area from the first full-width fixed element at the
top of the page, or else from the body's background (our light cream), which
left a faded cream band over the dark hero. The strip is that fixed element.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
_BASE = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")


def _rule():
    m = re.search(r"^\.status-strip \{(.*?)\}", _CSS, re.M | re.S)
    assert m, "no .status-strip rule"
    return m.group(1)


def test_every_app_page_has_the_strip_first():
    """First thing in the body: the browser looks for the element pinned at the top."""
    body = _BASE[_BASE.index("<body"):]
    assert body.index('class="status-strip"') < body.index('class="page-hero"')


def test_it_is_a_full_width_fixed_strip_the_height_of_the_clock_area():
    rule = _rule()
    assert "position: fixed" in rule and "top: 0" in rule
    assert "left: 0" in rule and "right: 0" in rule
    assert "height: env(safe-area-inset-top" in rule


def test_it_is_the_hero_colour_and_opaque():
    """A background-color, not a gradient or transparency: that is what is sampled."""
    rule = _rule()
    assert "background-color: var(--hero-bg-2)" in rule
    assert "rgba" not in rule and "gradient" not in rule


def test_it_never_takes_a_tap():
    assert "pointer-events: none" in _rule()
