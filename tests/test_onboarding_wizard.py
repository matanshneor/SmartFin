"""
Tests for the sign-up wizard: the first screen a new user sees.

The wizard is built from six <section class="onboarding-step"> on the same page,
and the JS switches between them. Hiding was a style="display:none" written by
hand on each step separately, and on one of them it was forgotten, so a new user
got the choice screen and the family-name question on top of each other, at the
most sensitive moment in the product.

The tests here don't check "does step1 have display:none" but the property
itself: exactly one step visible. That is what will also catch the seventh step
someone adds in the future and forgets to hide, and that is why this bug was
possible in the first place.
"""
import re

import pytest

pytestmark = pytest.mark.unit

_TEMPLATE = "frontend/templates/onboarding.html"
_STYLESHEET = "frontend/static/css/style.css"

_STEP = re.compile(
    r'<section[^>]*class="[^"]*\bonboarding-step\b[^"]*"[^>]*>', re.IGNORECASE
)


_CSS_BLOCK = re.compile(r"\.onboarding-step#(\w+)")

_FAM = "11111111-1111-1111-1111-111111111111"


def _render(repo_root, waiting_for):
    """Renders the page as it is actually served.

    The template holds two mutually exclusive opening screens
    ({% if waiting_for %}), so reading the file as text counts both and reports a
    clash that doesn't exist. What needs testing is what reaches the browser, in
    both arrivals."""
    from backend import app as app_module
    from backend.app import app

    app.config["TESTING"] = True
    real = {}
    for name, fake in (
        ("family_needs_onboarding", lambda fid: True),
        ("get_family", lambda fid: {"id": _FAM, "name": "שניאור", "invite_code": "K4F2QX"}),
        ("get_family_members", lambda fid: [{"id": "me", "name": "מתן"}] +
            ([{"id": "other", "name": waiting_for}] if waiting_for else [])),
    ):
        real[name] = getattr(app_module.db, name)
        setattr(app_module.db, name, fake)
    try:
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = "me"; sess["family_id"] = _FAM; sess["user_name"] = "מתן"
            return c.get("/onboarding").get_data(as_text=True)
    finally:
        for name, fn in real.items():
            setattr(app_module.db, name, fn)


def _steps(repo_root, waiting_for=None):
    tags = _STEP.findall(_render(repo_root, waiting_for))
    assert tags, "no wizard step was found: the structure probably changed and the test no longer tests anything"
    return tags


def _shown_by_css(repo_root, tag):
    """A step without inline display:none is still hidden if it has no CSS rule:
    that is how the waiting screen was born completely empty."""
    css = (repo_root / _STYLESHEET).read_text(encoding="utf-8")
    shown = set(_CSS_BLOCK.findall(css))
    ident = re.search(r'id="(\w+)"', tag)
    return bool(ident) and ident.group(1) in shown


@pytest.fixture
def repo_root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


def _is_hidden_inline(tag: str) -> bool:
    return re.search(r'style="[^"]*display\s*:\s*none', tag, re.IGNORECASE) is not None


@pytest.mark.parametrize("waiting_for", [None, "אור"])
def test_exactly_one_step_is_visible_on_arrival(repo_root, waiting_for):
    """The heart: a new user sees one screen, not two on top of each other."""
    visible = [t for t in _steps(repo_root, waiting_for) if not _is_hidden_inline(t)]

    assert len(visible) == 1, (
        f"{len(visible)} steps visible on load instead of one: "
        f"a new user will see them on top of each other. The visible steps: {visible}"
    )


@pytest.mark.parametrize("waiting_for,expected", [(None, "step0"), ("אור", "stepWaiting")])
def test_the_visible_step_is_the_first_one(repo_root, waiting_for, expected):
    """Control: one visible step isn't enough if it is the wrong step."""
    visible = [t for t in _steps(repo_root, waiting_for) if not _is_hidden_inline(t)]

    assert f'id="{expected}"' in visible[0], (
        f"the visible step isn't the opening screen but: {visible[0]}"
    )


@pytest.mark.parametrize("waiting_for", [None, "אור"])
def test_the_visible_step_is_actually_shown_by_the_stylesheet(repo_root, waiting_for):
    """The absence of inline display:none isn't visibility: the CSS default hides
    every step, and one with no rule of its own renders an empty page."""
    visible = [t for t in _steps(repo_root, waiting_for) if not _is_hidden_inline(t)]

    assert _shown_by_css(repo_root, visible[0]), (
        f"no CSS rule shows {visible[0]}: the page will load empty"
    )


def test_hiding_a_step_does_not_depend_on_remembering(repo_root):
    """What prevents the bug from returning: hiding is the CSS default, and not
    something to remember to write on each new step separately."""
    css = (repo_root / _STYLESHEET).read_text(encoding="utf-8")

    assert re.search(r"\.onboarding-step\s*\{[^}]*display\s*:\s*none", css), \
        "no CSS rule hides a step by default: a new step that is forgotten will show up again"
    assert "step0" in _CSS_BLOCK.findall(css), \
        "the opening screen isn't made visible again, so the wizard will open empty"
