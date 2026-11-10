"""Light, comfortable swipes, in real time (Matan, 11.10).

All swipes on the site work the same way: what is dragged moves with the finger one
to one, the decision on release is by where the movement is heading (distance +
momentum) and not by distance alone, and the return or exit continues the finger's
speed. In the browser: measured with real touch (CDP).
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_JS = Path(__file__).resolve().parent.parent / "frontend/static/js"
_CSS = (Path(__file__).resolve().parent.parent / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _read(name):
    return (_JS / name).read_text(encoding="utf-8")


def test_the_week_card_follows_the_finger_one_to_one():
    """Until 11.10 the card moved at half the finger's speed (dx * 0.45)."""
    core = _read("core.js")
    block = core[core.index("let sx = null, sy = 0, axis = null, drag = null, x = 0, stopSpring = null;"):]
    block = block[:block.index("})();")]
    assert "0.45" not in block
    assert "placeCard(drag, canGo(drag, raw < 0) ? raw : window.sfRubber(raw, drag.offsetWidth));" in block
    assert "window.sfSpring(" in block                      # a spring return, not a fixed transition


def test_the_next_week_is_fetched_while_swiping():
    core = _read("core.js")
    assert "if (canGo(drag, older)) fetchWeek(Number(drag.dataset.offset) + (older ? 1 : -1));" in core


def test_the_month_follows_the_finger_and_decides_by_aim():
    month = _read("month.js")
    block = month[month.index("const SLIDE ="):]
    assert "place(t.clientX - sx);" in block
    assert "const aim = x + window.sfProject(v, 0.99);" in block
    assert "(aim < 0) === (x < 0)" in block                 # changing your mind doesn't switch the month


def test_a_finished_entrance_animation_lets_go_of_the_card():
    """anim-rise with forwards held transform, and a swipe didn't move the "השבוע"
    card until it was replaced."""
    motion = _read("motion.js")
    assert "el.classList.remove('anim-rise');" in motion
    assert "e.animationName !== 'rise-in'" in motion


def test_swiping_a_row_does_not_scroll_the_page_under_it():
    tx = _read("transactions.js")
    move = tx[tx.index("drag.deltaX = over <= 0 ? raw"):]
    move = move[:move.index("}, { passive: false });")]
    assert "if (e.cancelable) e.preventDefault();" in move
    assert "document.addEventListener('touchend'" not in move     # the same listener


def test_the_browser_knows_sideways_belongs_to_the_page():
    assert re.search(r"\.transaction-item, \.cat-tx-row, \.recurring-row, \.week-card \{ touch-action: pan-y; \}", _CSS)


# ─── Settings fit in one screen; the add window stretches up (Matan, 11.10) ─

_SETTINGS = (Path(__file__).resolve().parent.parent / "frontend/templates/settings.html").read_text(encoding="utf-8")


def test_settings_does_not_keep_room_for_a_plus_button_it_does_not_have():
    """96 pixels at the bottom for a + button that doesn't exist in settings: after a
    swipe to the bottom the spring stopped there and the "הגדרות" heading stayed off
    screen. In the browser: measured, the page isn't taller than the screen when
    everything is closed (also with the installed app's iPhone areas)."""
    assert "{% block body_class %}no-fab settings-page{% endblock %}" in _SETTINGS
    rule = _CSS[_CSS.index("body.settings-page .main-content {"):]
    rule = rule[:rule.index("}")]
    assert "96px" not in rule and "env(safe-area-inset-bottom, 0px)" in rule


def test_the_add_window_stretches_up_and_springs_back():
    tx = _read("transactions.js")
    move = tx[tx.index("overlay.addEventListener('touchmove'"):]
    move = move[:move.index("}, { passive: false });")]
    assert "(dy < 0 && !atEnd)" in move                     # only when the content is already at its end
    assert "paint(raw >= 0 ? raw : sfRubber(raw, sheet.offsetHeight));" in move
    # and no empty space under the window when it stretches
    assert "box-shadow: 0 160px 0 0 var(--color-bg-elevated)" in _CSS
