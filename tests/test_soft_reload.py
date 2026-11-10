"""
Tests for the soft refresh.

Every action in the app ended with location.reload(). Adding a transaction cost
about two seconds from tap to a stable screen: a deliberate 380ms delay so the
sound finishes, a round trip to the server, re-parsing 99KB of CSS and 77KB of JS,
and a one-second count-up animation on the very number you wanted to see. Along the
way the temporary row already shown was deleted too, and the scroll position.

The risk in such a change isn't speed but what breaks silently: a page with
Chart.js instances or direct listeners doesn't survive a DOM swap. So opting in is
explicit per page, and whoever didn't opt in keeps getting a full refresh: the
existing behaviour.
"""
from pathlib import Path

import re

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TPL  = _ROOT / "frontend/templates"
_JS   = _ROOT / "frontend/static/js"


def _read(path):
    return path.read_text(encoding="utf-8")


# ─── Who opts in and who doesn't ─────────────────────────────────────────────

def test_the_dashboard_opts_in():
    assert "{% block soft_reload %} data-soft-reload{% endblock %}" in _read(_TPL / "index.html")


# The old rule was "a page with charts doesn't opt in", because swapping the DOM
# leaves Chart.js instances hanging on elements no longer in the document. That is
# true, but it is a consequence and not a law: a page that redraws on sf:refreshed is
# completely safe. The month page did exactly that, and the old rule turned it into
# a failure.
#
# The rule is now phrased on the property itself: whoever opts in must know how to
# redraw.
@pytest.mark.parametrize("page,script", [
    ("month.html", "month.js"),
    ("months.html", "months.js"),
    ("project_detail.html", "project-detail.js"),
])
def test_a_page_with_charts_that_opts_in_repaints_them(page, script):
    if "data-soft-reload" not in _read(_TPL / page):
        pytest.skip(f"{page} אינו מצטרף לרענון רך")

    js = _read(_ROOT / "frontend/static/js" / script)
    assert "sf:refreshed" in js, (
        f"{page} opts into the soft refresh but {script} doesn't redraw: "
        f"the charts will stay on detached canvases"
    )


def test_a_page_that_opts_in_binds_nothing_directly_to_an_element(page="month.html",
                                                                  script="month.js"):
    """softReload replaces the whole main. Every getElementById(...).addEventListener
    run on load then points to a detached element, and the control looks alive and
    is dead on tap."""
    js = _read(_ROOT / "frontend/static/js" / script)

    direct = re.findall(r"document\.getElementById\([^)]+\)\.addEventListener", js)
    assert not direct, f"{script} binds directly: {direct}"


def test_the_base_template_defaults_to_not_opting_in():
    """The default is the existing behaviour. A new page doesn't break silently."""
    base = _read(_TPL / "base.html")

    # The block is empty by default. Other main blocks may follow it (main_attrs:
    # the month + starts in), without changing who opts in.
    assert '<main class="main-content"{% block soft_reload %}{% endblock %}' in base


# ─── The behaviour ────────────────────────────────────────────────────────────

def test_the_caller_falls_back_to_a_full_reload_when_not_opted_in():
    js = _read(_JS / "transactions.js")
    # to the end of the function and not a fixed character count: finish has grown
    # twice already (the "עדכן להבא" message, and releasing the save button) and each
    # time the cut missed its end
    block = js[js.index("function finish()"):]
    block = block[:block.index("const sideEffects")]

    assert "main[data-soft-reload]" in block, "the soft refresh runs without checking opt-in"
    assert "window.location.reload()" in block, "no fallback to a full refresh"


def test_a_failed_soft_reload_falls_back_rather_than_leaving_a_stale_screen():
    """In the worst case the behaviour is identical to today: that is what makes the
    change safe."""
    js = _read(_JS / "core.js")
    block = js[js.index("window.softReload ="):]

    assert ".catch(" in block and "window.location.reload()" in block


def test_the_soft_reload_does_not_replay_the_count_up_animation():
    """Counting from 0 after an update makes the number jump backwards before your
    eyes, and the fresh HTML already contains the final value as text."""
    js = _read(_JS / "core.js")
    block = js[js.index("window.softReload ="):]

    assert "runCountUps" not in block
    assert "anim-rise" not in block


def test_jinja_stays_the_only_renderer():
    """We fetch the same page and take ready HTML from it. Duplicating display logic in
    JS is exactly the source of bugs this change avoids."""
    js = _read(_JS / "core.js")
    block = js[js.index("window.softReload ="):]

    assert "sfFetchPage(window.location.href)" in block
    assert "DOMParser" in js[js.index("function sfApplyHtml"):]
