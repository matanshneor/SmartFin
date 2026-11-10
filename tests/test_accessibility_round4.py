"""Accessibility, round 4, E5. The full check in the browser:
tests/browser/accessibility.py.

What is checked here is that the markup doesn't disappear in a future edit: a
switch with no name was read as "checkbox, checked" ×6; the menu was read
differently from what is written and didn't say where you are; a chart wasn't
announced; "3 more categories" didn't say whether it is open.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_T = Path(__file__).resolve().parent.parent / "frontend/templates"
_CSS = (Path(__file__).resolve().parent.parent / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _html(name):
    return re.sub(r"\{#.*?#\}", "", (_T / name).read_text(encoding="utf-8"), flags=re.S)


@pytest.mark.parametrize("page", ["settings.html", "onboarding.html"])
def test_every_switch_has_a_name(page):
    html = _html(page)
    for m in re.finditer(r'<label class="pref-switch">\s*(<input[^>]*>)', html):
        assert "aria-label=" in m.group(1), m.group(1)[:80]


def test_the_nav_says_where_you_are_and_reads_what_it_shows():
    base = _html("base.html")
    nav = base[base.index('<nav class="bottom-nav"'):base.index("</nav>")]
    assert nav.count('aria-current="page"') == 5
    assert 'class="nav-item' in nav and not re.search(r'class="nav-item[^>]*aria-label=', nav, re.S)


@pytest.mark.parametrize("page", ["month.html", "months.html", "project_detail.html"])
def test_charts_are_announced_as_images(page):
    for canvas in re.findall(r"<canvas[^>]*>", _html(page)):
        assert 'role="img"' in canvas and "aria-label=" in canvas, canvas


def test_the_hidden_categories_toggle_says_if_it_is_open():
    toggles = re.findall(r'<li class="zero-toggle"[^>]*>', _html("month.html"))
    assert toggles and all('aria-expanded="false"' in t for t in toggles)
    js = (_T.parent / "static/js/month.js").read_text(encoding="utf-8")
    assert "setAttribute('aria-expanded', String(shown))" in js


def test_a_mixed_project_list_says_income_and_expense_not_only_in_colour():
    html = _html("project_detail.html")
    assert "{% if tx.type == 'income' %}+{% elif tx.type == 'expense' %}-{% endif %}₪" in html


def test_the_toast_moves_up_while_the_transaction_sheet_is_open():
    assert "body:has(.modal-overlay.open) .toast" in _CSS
