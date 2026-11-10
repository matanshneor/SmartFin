"""Settings: the family name on the left side of the page header (Matan, 1.10:
option E)."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent


def test_the_family_name_sits_on_the_left_of_the_title():
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    top = html[html.index('<div class="hero-top settings-hero-top">'):html.index('<div class="hero-profile">')]
    assert top.index('<h1 class="hero-title">הגדרות</h1>') < top.index('<p class="settings-family-name">')
    assert "hero-subtitle" not in top
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    rule = css[css.index(".settings-family-name {"):]
    rule = rule[:rule.index("}")]
    assert "text-align: left;" in rule and "font-size: 1.1rem;" in rule and "min-width: 0;" in rule


def test_renaming_updates_the_name_at_the_top():
    js = (_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8")
    assert "document.querySelectorAll('.settings-family-name').forEach(el => { el.textContent = newName; });" in js


def test_the_family_box_title_is_just_the_title():
    """Matan (1.10): the box heading has only "המשפחה שלי": without the family name
    (already at the top of the page) and without the member count."""
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    header = html[html.index('<span class="group-title">המשפחה שלי</span>'):]
    header = header[:header.index("</button>")]
    assert "group-sub" not in header


def test_the_top_shows_my_email_not_the_member_count():
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    start = html.index('<div class="hero-profile">')
    import re
    hero = re.sub(r"\{#.*?#\}", "", html[start:html.index("{% endblock %}", start)], flags=re.S)
    assert "{{ account.email }}" in hero
    assert "חשבון משפחתי" not in hero and "count_of" not in hero


def test_renaming_myself_updates_the_top():
    js = (_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8")
    part = js[js.index("function submitProfile"):js.index("if (saveProfileBtn)")]
    assert "document.getElementById('heroProfileName')" in part
    assert "heroName.textContent = d.full_name;" in part
