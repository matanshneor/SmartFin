"""Two rules no test enforced, and both broke on the same day.

**An inline handler.** An `onclick` in a template has no sign that it doesn't
work: it looks right, it passes every text check, and the browser silently
blocks it because of the CSP (`script-src 'self'` without `unsafe-inline`). It
landed on the refresh button on the waiting screen: the only screen in the app
with no way out. The button simply didn't respond.

**A soft refresh without a guard.** `softReload` replaces the whole `main`, and
that is safe only on a page that declared itself as such. One unguarded call on
the month page left every Chart.js instance on detached canvases: the charts
vanished, the search died, and the management button stopped responding midway.

The tests here don't describe the two bugs: they enforce the two rules.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TPL = _ROOT / "frontend/templates"
_JS = _ROOT / "frontend/static/js"

_INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.I)


# ─── No inline handlers ─────────────────────────────────────────────────────

@pytest.mark.parametrize("template", sorted(p.name for p in _TPL.glob("*.html")))
def test_no_template_uses_an_inline_event_handler(template):
    """The CSP blocks them, and the blocking is silent: no JS error, just a button
    that does nothing. That is the most expensive way to find out."""
    html = (_TPL / template).read_text(encoding="utf-8")
    found = [m.group(0).strip() for m in _INLINE_HANDLER.finditer(html)]

    assert not found, (
        f"{template} contains an inline handler {found}: the CSP will silently block it. "
        f"The place for the listener is the page's JS file."
    )


def test_the_policy_that_makes_that_true_is_still_in_place():
    """Control: if someone allows `unsafe-inline`, the test above stays green and
    guards a rule that is no longer needed, while the real protection is gone."""
    from backend.app import _CSP

    directives = dict(
        (d.split(" ", 1) + [""])[:2] if " " in d else (d, "")
        for d in (part.strip() for part in _CSP.split(";"))
    )
    script_src = directives.get("script-src", "")

    assert script_src == "'self'", f"script-src was opened: {script_src!r}"
    assert "unsafe-inline" not in script_src


def test_the_waiting_screen_button_is_wired_before_the_early_exit():
    """The waiting screen is served instead of the wizard, and `onboarding.js`
    returns early when there is no wizard. The only button there must be bound
    before that return."""
    js = (_JS / "onboarding.js").read_text(encoding="utf-8")

    assert "waitingRefreshBtn" in js, "the refresh button isn't connected to any listener"
    assert js.index("waitingRefreshBtn") < js.index("if (!step0) return;"), \
        "the listener is registered after the early return, i.e. never, on the screen where it is needed"


def test_the_wizard_has_a_way_out():
    """Without this, whoever signed up with a wrong email, or joined the wrong
    family, is stuck: the wizard has no navigation, no menu and not a single link."""
    html = (_TPL / "onboarding.html").read_text(encoding="utf-8")

    assert "url_for('logout')" in html, "no way out of the wizard"


# ─── Every soft refresh is guarded ──────────────────────────────────────────

def test_every_soft_reload_call_checks_the_page_opted_in():
    """The rule, not the case. `softReload` replaces the whole `main`: a call on a
    page that didn't declare itself safe leaves a half-connected DOM behind."""
    js = (_JS / "transactions.js").read_text(encoding="utf-8")

    for match in re.finditer(r"window\.softReload\(", js):
        window = js[max(0, match.start() - 600):match.start()]
        assert "data-soft-reload" in window, (
            f"a call to softReload at character {match.start()} without checking "
            f"that the page opted into soft refresh"
        )


def test_the_month_page_repaints_its_charts_after_a_refresh():
    """The page opted into soft refresh, so every canvas is replaced. Without a
    redraw the centre labels are left floating over empty squares."""
    js = (_JS / "month.js").read_text(encoding="utf-8")

    assert "function paint()" in js, "no drawing function that can be run again"
    assert re.search(r"addEventListener\(\s*'sf:refreshed'\s*,\s*paint\s*\)", js), \
        "the charts aren't redrawn after a soft refresh"
    assert "destroy()" in js, \
        "previous instances aren't destroyed: new Chart on a canvas in use throws"


def test_the_month_page_redraws_its_charts_after_a_refresh():
    """The fresh HTML brings empty `<canvas>` elements: without a redraw the cards
    stay empty."""
    js = (_JS / "month.js").read_text(encoding="utf-8")

    assert re.search(r"addEventListener\(\s*'sf:refreshed'\s*,\s*paint\s*\)", js)