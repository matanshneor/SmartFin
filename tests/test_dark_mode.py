"""Dark mode: manual only, from settings → appearance (Matan, 30.9, round 6, item 4)."""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
_PAGES = ["base", "error", "landing", "onboarding", "login", "terms", "reset_password", "privacy"]


@pytest.mark.parametrize("page", _PAGES)
def test_every_page_sets_the_theme_before_it_paints(page):
    """In <head>, after the CSS and before the body; otherwise a white flash on
    every load."""
    html = (_ROOT / f"frontend/templates/{page}.html").read_text(encoding="utf-8")
    head = html[:html.index("</head>")]
    assert "js/theme.js" in head


def test_the_theme_is_manual_only():
    """Matan's decision: not by the phone's setting, only what was chosen."""
    js = (_ROOT / "frontend/static/js/theme.js").read_text(encoding="utf-8")
    assert "localStorage.getItem('sf_theme') === 'dark'" in js
    assert "prefers-color-scheme" not in js
    assert "prefers-color-scheme: dark" not in _CSS


def _dark_block():
    start = _CSS.index(':root[data-theme="dark"] {')
    return _CSS[start:_CSS.index("}", start)]


@pytest.mark.parametrize("token", ["--color-bg", "--color-card", "--color-input", "--color-text",
                                   "--color-text-muted", "--color-income", "--color-expense",
                                   "--ink-rgb", "--dock-bg", "--alert-danger-bg", "--border-subtle"])
def test_the_dark_palette_covers_the_surfaces(token):
    assert f"{token}:" in _dark_block()


def test_no_ink_tint_is_left_hardcoded():
    """A line or subtle background in the ink colour, written directly: disappears
    on a dark background."""
    assert not re.findall(r"rgba\(28, ?25, ?23", _CSS)
    assert "background: rgba(255, 255, 255, 0.97)" not in _CSS


def test_charts_follow_the_theme():
    js = (_ROOT / "frontend/static/js/chart-setup.js").read_text(encoding="utf-8")
    assert "getAttribute('data-theme') === 'dark'" in js
    for f in ("month.js", "project-detail.js"):
        src = (_ROOT / "frontend/static/js" / f).read_text(encoding="utf-8")
        assert "borderColor:     '#FFFFFF'" not in src, f


def test_the_settings_switch():
    tpl = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    assert 'data-theme-choice="light"' in tpl and 'data-theme-choice="dark"' in tpl
    js = (_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8")
    assert "localStorage.setItem('sf_theme', theme)" in js


# ── Text size (round 6, item 9): in the same "appearance" setting and the same
# loading file ──

def test_text_size_is_set_before_paint_and_scales_everything():
    js = (_ROOT / "frontend/static/js/theme.js").read_text(encoding="utf-8")
    assert "localStorage.getItem('sf_text_size')" in js
    assert 'html[data-text-size="large"]  { font-size: 112.5%; }' in _CSS
    assert 'html[data-text-size="xlarge"] { font-size: 125%; }' in _CSS
    # a size in pixels doesn't grow with the rest
    assert not re.findall(r"font-size:\s*\d+px", _CSS)


def test_the_text_size_switch():
    tpl = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    for size in ("normal", "large", "xlarge"):
        assert f'data-size="{size}"' in tpl
    js = (_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8")
    assert "localStorage.setItem('sf_text_size', size)" in js


def test_the_settings_titles_say_what_is_inside():
    """Matan (11.10): no line under the headings: the heading itself says what is
    inside. "Family settings" didn't, so it is now called "transaction
    attribution"."""
    tpl = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    assert 'class="group-sub"' not in tpl
    assert "בטלפון הזה בלבד" not in tpl
    assert '<span class="group-title">שיוך עסקאות</span>' in tpl
    assert '<span class="group-title">העדפות משפחה</span>' not in tpl


def test_there_is_no_sound_and_so_no_sound_toggle():
    """Matan (11.10): the sound on save was removed, and only a silent vibration
    remains, so the "sound and vibration" switch was removed from "appearance"
    too. No sound is left that can't be turned off."""
    tpl = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    assert 'id="feedbackToggle"' not in tpl
    core = (_ROOT / "frontend/static/js/core.js").read_text(encoding="utf-8")
    assert "AudioContext" not in core and "createOscillator" not in core
    assert "navigator.vibrate" in core
