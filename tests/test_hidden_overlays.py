"""
Tests for truly hiding overlays.

The modal and the confirm dialog were hidden with `opacity: 0` only. Opacity hides
from the eye, not from the keyboard or the screen reader. Both sit in base.html,
i.e. on every page in the app, so:

· Pressing Tab past the bottom menu moved focus into the invisible transaction
  form, for about 15 presses, with a focus ring you can't see.
· VoiceOver read out the whole transaction form on every screen ("add
  transaction, expense, income, savings, amount in shekels…"), before the page's
  real content.

I.e. the app wasn't usable with a screen reader.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS  = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", _CSS)
    assert m, f"rule {selector} not found"
    return m.group(1)


# ─── Hiding ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("selector,what", [
    (".modal-overlay",   "טופס העסקה המלא, כ-15 פקדים"),
    (".confirm-overlay", "דיאלוג אישור עם שני כפתורים"),
])
def test_a_closed_overlay_is_out_of_the_tab_order_and_the_screen_reader(selector, what):
    body = _rule(selector)

    assert "visibility: hidden" in body, (
        f"{selector} is hidden by opacity only: {what} stay reachable by keyboard "
        "and screen reader on every page"
    )


@pytest.mark.parametrize("selector", [".modal-overlay", ".confirm-overlay"])
def test_an_open_overlay_comes_back(selector):
    """Control: hiding that is never undone is a modal that doesn't open."""
    assert "visibility: visible" in _rule(selector + ".open")


@pytest.mark.parametrize("selector", [".modal-overlay", ".confirm-overlay"])
def test_the_fade_out_still_looks_right(selector):
    """`visibility` switches all at once, so it must be in the transition;
    otherwise the layer vanishes immediately instead of fading out."""
    assert re.search(r"transition:\s*opacity[^;]*visibility", _rule(selector))


# ─── What the hiding would break ────────────────────────────────────────────

@pytest.mark.parametrize("path,fn", [
    ("frontend/static/js/transactions.js", "openModal"),
    ("frontend/static/js/core.js",         "appConfirm"),
])
def test_focus_waits_for_the_element_to_become_visible(path, fn):
    """An element inside a hidden parent can't be focused. Focus on the same frame
    the class was added was silently swallowed: the modal opened with no
    keyboard, and the dialog with no focus."""
    js = (_ROOT / path).read_text(encoding="utf-8")
    block = js[js.index(fn):][:1400]

    assert "requestAnimationFrame" in block, f"{fn} focuses before the hiding was undone"


# ─── What stays on purpose ──────────────────────────────────────────────────

def test_the_toast_stays_in_the_accessibility_tree():
    """It is `role="status" aria-live="polite"`: a live region must stay in the tree
    for an announcement to be heard. It is empty when not shown, so there is
    nothing to read out, and nothing in it is focusable."""
    assert "visibility: hidden" not in _rule(".toast")

    base = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
    assert 'id="appToast" role="status" aria-live="polite"' in base
