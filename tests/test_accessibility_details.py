"""
Tests for three small accessibility defects.

Touch targets: the category reorder arrows are 30×15. A thumb touches an area of
~45, and the standard asks for at least 24. A miss on a phone triggers the
opposite arrow or the row itself, i.e. reordering categories is a guessing game.

Labels: six fields without a linked label. A screen reader reads "text field"
and nothing more. In the transaction editor there is "סכום (₪)" text on screen,
but it isn't linked to the field: visually fine, and to the screen reader two
separate things.

Zoom: on iOS, focusing a field with a font smaller than 16px zooms the whole
page, **and doesn't zoom back**. The bottom menu and the + button stay off
screen until pinched by hand.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS  = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", _CSS)
    assert m, f"{selector} not found"
    return m.group(1)


def _px(body, prop):
    m = re.search(rf"(?<![-\w]){prop}:\s*([\d.]+)px", body)
    return float(m.group(1)) if m else None


# ─── Touch targets ──────────────────────────────────────────────────────────

def test_the_reorder_arrows_have_a_real_tap_target():
    body = _rule(".cat-move-btn::before")

    assert _px(body, "width")  >= 24, "the touch area is too narrow"
    assert _px(body, "height") >= 24, "the touch area is too short"


def test_the_two_arrows_grow_away_from_each_other():
    """They sit right on top of each other. A large symmetric area would overlap,
    and a miss would trigger the opposite direction, which is worse than not
    hitting at all."""
    assert "bottom: 0" in _rule(".cat-move-up::before")
    assert "top: 0"    in _rule(".cat-move-down::before")


def test_the_arrows_themselves_did_not_grow():
    """Control: the fix is in the touch area, not the design. Large arrows would
    push the category name out."""
    body = _rule(".cat-move-btn")

    assert _px(body, "width") == 30 and _px(body, "height") == 15


# ─── Labels ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,label", [
    ("familyNameInput", "שם המשפחה"),
    ("joinCodeInput",   "קוד הזמנה"),
])
def test_settings_fields_have_a_label_a_screen_reader_can_read(field, label):
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")

    assert f'for="{field}"' in html, f"{field} without a linked label"
    assert label in html


def test_the_visually_hidden_class_is_still_read_aloud():
    """`display:none` would hide it from the screen reader too, i.e. a label that
    does nothing."""
    body = _rule(".sr-only")

    assert "display: none" not in body
    assert "clip-path" in body and "position: absolute" in body


# ─── Zoom on iOS ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("selector", [
    ".tx-search-input",
    ".category-row.editing .form-input",
])
def test_no_input_is_small_enough_to_trigger_ios_zoom(selector):
    body = _rule(selector)
    m = re.search(r"font-size:\s*([\d.]+)rem", body)

    assert m, f"{selector}: no font-size"
    assert float(m.group(1)) >= 1.0, (
        f"{selector} is {m.group(1)}rem: iOS will zoom and not zoom back"
    )


def test_zoom_is_not_disabled_as_a_shortcut():
    """The easy fix was `maximum-scale=1`, and that blocks zooming even for those
    who really need it."""
    base = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")

    assert "maximum-scale" not in base
    assert "user-scalable=no" not in base


def test_wide_small_controls_get_a_44px_tall_target():
    """Matan (5.10): the range buttons on comparison (27px) and "back to the
    current month" (17px)."""
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    block = css[css.index(".range-chip::before,"):][:260]
    assert ".back-to-current::before" in block and "height: 44px;" in block
    assert "left: -3px;" in block and "right: -3px;" in block, "don't overlap the neighbouring button (6px gap)"
