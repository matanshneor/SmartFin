"""
A failure loading Chart.js disabled the whole page, not just the charts.

Eight lines of `Chart.defaults` sat at the top of the block that also held
opening categories, searching transactions and the Enter key. If the library
didn't arrive (a blocker, a bad network, a corrupt cached file), the first line
threw a ReferenceError and the rest never ran. The page looked perfectly fine
and simply didn't respond to taps, and that is the worst kind of confusion:
there is nothing even to report.

And it wasn't theoretical. `month.html` loads the library only when there are
transactions, so every empty month (including every new family's first) raised
this error.

The same eight lines were duplicated in three files, i.e. the bug was tripled too.
"""
import json
import shutil
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS   = _ROOT / "frontend/static/js"
_TPL  = _ROOT / "frontend/templates"

_CHART_PAGES = ["month", "months", "project-detail"]


def _read(p):
    return p.read_text(encoding="utf-8")


# ─── The rule in one place ──────────────────────────────────────────────────

def test_the_chart_defaults_live_in_exactly_one_file():
    """Three copies were also three bugs."""
    owners = [f.name for f in _JS.glob("*.js") if "Chart.defaults" in _read(f)]
    assert owners == ["chart-setup.js"], f"Chart.defaults found in: {owners}"


@pytest.mark.parametrize("page", _CHART_PAGES)
def test_every_chart_block_is_behind_the_flag(page):
    js = _read(_JS / f"{page}.js")
    assert "if (!window.sfCharts.ready) return;" in js, \
        f"{page}.js draws without checking there is a library"


@pytest.mark.parametrize("page", _CHART_PAGES)
def test_the_drawing_sits_after_the_guard_and_not_before(page):
    """Control: a flag checked after drawing is worth nothing."""
    js = _read(_JS / f"{page}.js")
    assert js.index("if (!window.sfCharts.ready) return;") < js.index("new Chart("), \
        f"{page}.js draws before the check"


# ─── The pages load the file ────────────────────────────────────────────────

@pytest.mark.parametrize("page", ["month.html", "months.html", "project_detail.html"])
def test_every_page_with_charts_loads_the_setup(page):
    assert "js/chart-setup.js" in _read(_TPL / page)


def test_the_month_page_loads_the_setup_even_when_it_skips_the_library():
    """This is the heart: the library loads only `{% if month_transactions %}`, and
    an empty month is exactly the case where someone needs to tell the rest of
    the code "no charts"."""
    html = _read(_TPL / "month.html")
    block = html[html.index("{% block scripts %}"):]
    setup_at = block.index("js/chart-setup.js")
    endif_at = block.index("{% endif %}")
    assert setup_at > endif_at, "chart-setup.js is loaded inside the condition: in an empty month it won't run"


# ─── What actually happens without the library ──────────────────────────────

_HARNESS = r"""
const fs = require('fs');
const listeners = [];
const g = globalThis;

g.document = {
    documentElement: { style: { setProperty() {} }, getAttribute() { return null; } },
    addEventListener: (type) => listeners.push(type),
    getElementById:   () => null,
    querySelectorAll: () => [],
    querySelector:    () => null,
};
g.window = g;
// ‎sfMoney‎ מ-core.js עצמו (נחלץ בזמן ריצה), לא עותק
eval(require('fs').readFileSync(process.env.SF_CORE_JS, 'utf8')
     .match(/window\.sfMoney = function[\s\S]*?\n\};/)[0]);
// דפדפן אמיתי מספק את זה, והכפיל לא — ‎month.js‎ מאזין ל-‎sf:refreshed‎
// כדי לצייר מחדש אחרי רענון רך.
g.addEventListener = g.addEventListener || function () {};
g.sfData = () => ({
    summary:   { expense: 1950, savings: 8000, income: 11527 },
    expense:   [],
    members:   [],
    trend:     [],
    breakdown: {},
});

for (const f of process.argv.slice(2)) {
    (0, eval)(fs.readFileSync(f, 'utf8'));
}
console.log(JSON.stringify({ listeners, ready: g.sfCharts.ready }));
"""


def _run_node(harness_js, *files):
    """Runs the real JS files under a fake DOM, and returns what they did."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    harness = _ROOT / "tests" / "_chart_harness.js"
    harness.write_text(harness_js, encoding="utf-8")
    try:
        out = subprocess.run(
            [node, str(harness)] + [str(_JS / f) for f in files],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "SF_CORE_JS": str(_JS / "core.js")},
        )
    finally:
        harness.unlink(missing_ok=True)
    assert out.returncode == 0, f"the code crashed:\n{out.stderr[:800]}"
    return json.loads(out.stdout)


def _run_without_chart_js(*files):
    return _run_node(_HARNESS, *files)


def test_the_month_page_still_listens_for_clicks_without_the_library():
    """The test that would have failed before the fix: without a global Chart, the
    file crashed before it managed to register a single listener."""
    result = _run_without_chart_js("chart-setup.js", "month.js")

    assert result["ready"] is False
    assert "click" in result["listeners"], "opening a category wasn't registered"
    assert "keydown" in result["listeners"], "the Enter key wasn't registered"


def test_the_project_page_still_listens_for_clicks_without_the_library():
    result = _run_without_chart_js("chart-setup.js", "project-detail.js")

    assert result["ready"] is False
    assert "click" in result["listeners"]
    assert "keydown" in result["listeners"]


def test_the_months_page_survives_without_the_library():
    result = _run_without_chart_js("chart-setup.js", "months.js")

    assert result["ready"] is False


def test_the_legend_colours_are_set_even_without_the_library():
    """Some legends are rendered on the server and coloured from `--chart-color-N`.
    If those are set only next to the charts, a whole legend loses its colours."""
    setup = _read(_JS / "chart-setup.js")
    guard_at = setup.index("if (typeof Chart === 'undefined')")
    assert setup.index("--chart-color-") < guard_at, \
        "the legend colours are set only when there is a library"


# ─── What the user sees instead of the chart ────────────────────────────────

def test_the_empty_chart_says_the_numbers_are_still_right():
    """An empty canvas looks like a failure, and a user who sees a failure on a money
    page stops trusting the numbers next to it."""
    setup = _read(_JS / "chart-setup.js")

    assert "הגרף לא נטען" in setup
    assert "המספרים עצמם מעודכנים ונכונים" in setup


def test_the_doughnut_centre_is_pulled_back_into_flow():
    """The number in the middle of the donut is positioned absolutely over the
    canvas. Without the canvas it has no height to sit in, and it would fall onto
    the text."""
    css = _read(_ROOT / "frontend/static/css/style.css")
    block = css[css.index(".chart-unavailable"):css.index(".chart-unavailable") + 400]

    assert "position: static" in block


# ─── Control: when the library is there, everything is drawn as before ──────

_WITH_CHART = r"""
const fs = require('fs');
const g = globalThis;
const drawn = [], legend = [];
const els = {};
const el = (id) => els[id] || (els[id] = { id, appendChild() {}, setAttribute() {}, addEventListener() {} });

g.Chart = function (ctx, cfg) {
    drawn.push({ el: ctx.id, type: cfg.type, points: cfg.data.datasets[0].data.length });
};
g.Chart.defaults = { font: {}, color: null, animation: {},
                     plugins: { legend: {}, tooltip: {} } };

g.document = {
    documentElement: { style: { setProperty() {} }, getAttribute() { return null; } },
    addEventListener() {},
    createElement: () => ({ className: '', innerHTML: '' }),
    querySelectorAll: () => [],
    querySelector:    () => null,
    getElementById: (id) =>
        (id === 'monthStrip' || id === 'txSearch') ? null :
        (id === 'overviewLegend' ? { appendChild: (li) => legend.push(li.innerHTML) } : el(id)),
};
g.window = g;
// ‎sfMoney‎ מ-core.js עצמו (נחלץ בזמן ריצה), לא עותק
eval(require('fs').readFileSync(process.env.SF_CORE_JS, 'utf8')
     .match(/window\.sfMoney = function[\s\S]*?\n\};/)[0]);
// דפדפן אמיתי מספק את זה, והכפיל לא — ‎month.js‎ מאזין ל-‎sf:refreshed‎
// כדי לצייר מחדש אחרי רענון רך.
g.addEventListener = g.addEventListener || function () {};
g.sfData = () => ({
    summary: { expense: 1950, savings: 8000, income: 11527 },
    expense: [{ name: 'סופר', total: 650, pct: 33 }, { name: 'דלק', total: 1300, pct: 67 }],
    members: [{ type: 'expense', label: 'הוצאות',
                rows: [{ name: 'מתן', expense: 100, color: '#A67C00' }] }],
});

for (const f of process.argv.slice(2)) (0, eval)(fs.readFileSync(f, 'utf8'));
console.log(JSON.stringify({ drawn, legend: legend.length }));
"""


def test_the_month_charts_are_still_drawn_when_the_library_is_there():
    """Splitting into two blocks could have left a chart behind without any test
    noticing. The three charts and the legend, exactly as before."""
    result = _run_node(_WITH_CHART, "chart-setup.js", "month.js")

    # "Expenses vs savings" was removed (Matan, 30.9)
    # "By family member" became rows in the template (design pass, 11.11)
    assert result["drawn"] == [
        {"el": "expenseChart",         "type": "doughnut", "points": 2},
    ]
