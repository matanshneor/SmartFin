"""Switching tabs without a full load (Matan, 22.11; all five tabs since 23.11).

The behaviour itself is checked in a browser; here, what must not be lost
quietly: the scope, the holder the page scripts sit in, and every way back to
the full load there was before.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CORE = (_ROOT / "frontend/static/js/core.js").read_text(encoding="utf-8")
_BASE = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
_SW = (_ROOT / "frontend/static/sw.js").read_text(encoding="utf-8")


def test_all_five_tabs_switch_softly():
    """The first day: home ↔ month only. Since 23.11 all five, where the page
    arriving allows it (`sfApplyHtml`)."""
    assert "const SOFT_TABS = TABS;" in _CORE
    block = _CORE[_CORE.index("const SOFT_TABS"):]
    block = block[:block.index("\n})();")]
    # Both ends must be soft tabs, and the full load stays the default
    assert "SOFT_TABS.indexOf(url.pathname) !== -1 && SOFT_TABS.indexOf(location.pathname) !== -1" in block
    assert "location.replace(url.href);" in block


def test_page_scripts_sit_in_a_holder_the_switch_can_read():
    assert '<div id="sfPageExtras" class="page-extras">{% block scripts %}{% endblock %}</div>' in _BASE
    # After the scripts every page shares, before the ones that close the page
    assert _BASE.index("js/core.js") < _BASE.index('id="sfPageExtras"') < _BASE.index("js/transactions.js")


def test_a_script_already_run_is_not_run_again():
    apply = _CORE[_CORE.index("function sfApplyHtml"):]
    apply = apply[:apply.index("\nfunction sfFetchPage")]
    assert "if (!have[path(src)]) missing.push(src);" in apply
    assert "return tab ? { missing: missing } : 'ok';" in apply


def test_every_doubt_falls_back_to_the_full_load():
    apply = _CORE[_CORE.index("function sfApplyHtml"):]
    apply = apply[:apply.index("\nfunction sfFetchPage")]
    assert "if (!extras || !newExtras) return 'full';" in apply
    # A page not marked safe to swap is entered softly only the first time
    assert "if (!target.hasAttribute('data-soft-reload') && !missing.length) return 'full';" in apply
    assert "window.sfCharts && !window.sfCharts.ready) return 'full';" in apply
    switch = _CORE[_CORE.index("window.sfSwitchTab = function"):]
    switch = switch[:switch.index("\n})();")]
    assert "if (done === 'full') return full();" in switch
    assert ".catch(full)" in switch
    # An error right after the swap: reload, and soft switching off for the session
    assert "sessionStorage.setItem(OFF_KEY, '1')" in _CORE
    assert "window.sfSoftTabsOn()" in _CORE


def test_the_page_fetched_on_touch_serves_the_soft_switch_too():
    take = _SW[_SW.index("function takePrefetched"):]
    take = take[:take.index("\n}\n")]
    assert "request.headers.get('X-Requested-With') !== 'sf-soft-reload'" in take


def test_a_page_script_left_in_the_document_ignores_another_pages_data():
    """Found in the browser (23.11): `month.js` stays loaded, `sf:refreshed` reaches
    its `paint` on the comparison page too, and that page's data block of the same
    id has no `expense`: a TypeError, and the safety net switched soft tabs off."""
    month = (_ROOT / "frontend/static/js/month.js").read_text(encoding="utf-8")
    paint = month[month.index("function paint() {"):]
    assert paint.index("if (!SF_VIEW || !Array.isArray(SF_VIEW.expense)) return;") < paint.index("const expenseData = SF_VIEW.expense;")
    # And the two files don't declare the same top-level name
    months = (_ROOT / "frontend/static/js/months.js").read_text(encoding="utf-8")
    assert "const SF_MONTHS_VIEW = window.sfData('sf-view-data');" in months
    assert "const SF_VIEW" not in months


def test_the_projects_list_marks_itself_safe_to_swap():
    tpl = (_ROOT / "frontend/templates/projects.html").read_text(encoding="utf-8")
    assert "{% block soft_reload %} data-soft-reload{% endblock %}" in tpl
    assert "{% block scripts %}" not in tpl, "a script of its own would have to redraw on sf:refreshed"
