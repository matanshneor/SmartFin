"""Round 4: "מה חדש" after an update, and a family member who joined (Matan, 12.10)."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "frontend/static/js/whats-new.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "frontend/templates/index.html").read_text(encoding="utf-8")


def test_whats_new_shows_once_per_version_and_not_to_new_families():
    assert "const VERSION = '" in _JS
    assert "page.dataset.newFamily === 'true'" in _JS
    assert "seen !== VERSION" in _JS


def test_the_home_page_carries_what_the_script_needs():
    assert 'id="sfHomeInfo"' in _INDEX and "(home_members or []) | tojson" in _INDEX
    assert 'id="memberJoined"' in _INDEX and "js/whats-new.js" in _INDEX


def test_member_joined_skips_me_and_the_first_visit_on_a_phone():
    assert "m.id !== page.dataset.me" in _JS
    assert "if (known === null) return;" in _JS
    assert "textContent" in _JS and "innerHTML" not in _JS      # a family member's name: text only


def test_home_and_month_log_how_long_each_query_took():
    """Load speed (Matan, 12.10): first measure, and only then fix."""
    app = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    assert 'logger.info("timing %s total=%.0fms %s"' in app
    assert "return {name: _timed(name, fn) for name, fn in tasks.items()}" in app
    for label in ('"recurring"', '"recent"', '"week"', '"alerts"', '"month_page"'):
        assert f"_timed({label}" in app, label


def test_a_slice_tap_opens_the_row_through_its_own_click():
    """toggleExpand is defined in the interactivity block; from the charts block it
    doesn't exist, and touching a slice threw a ReferenceError and opened nothing
    (found in a screenshot, 12.10)."""
    js = (_ROOT / "frontend/static/js/month.js").read_text(encoding="utf-8")
    body = js[js.index("function openSlice"):]
    body = body[:body.index("\n}\n")]
    assert "toggleExpand(" not in body and "trigger.click();" in body


def test_the_quiet_project_banner_is_readable_over_the_hero():
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    rule = css[css.index(".project-quiet-banner {"):]
    rule = rule[:rule.index("}")]
    assert "background: var(--color-card)" in rule
