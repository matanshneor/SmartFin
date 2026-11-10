"""
The main number on the dashboard didn't update, and a new family was left with
"let's get started".

The soft refresh replaced `main.main-content` only, and the hero is its
**sibling** in base.html, not its child. So "left in the account this month" (the
number the whole app exists for) stayed stuck after every add and edit, until a
full reload.

And for a new family this was especially bad: the hero shows "tap + to add your
first transaction **and see your balance here**", and that sentence stayed on
screen above the transaction just added.

And on the same screen, a second bug: an empty dashboard has no
`.transactions-list` at all, but `closeModal()` ran anyway, so a save failure was
written into a closed sheet.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS   = _ROOT / "frontend/static/js"
_TPL  = _ROOT / "frontend/templates"


def _read(p):
    return p.read_text(encoding="utf-8")


# ─── The hero is really replaced ────────────────────────────────────────────

def test_the_hero_is_a_sibling_of_main_not_a_child():
    """This is the root. If the structure changes, the assumption below is no longer true."""
    base = _read(_TPL / "base.html")

    assert base.index('<header class="page-hero">') < base.index('<main class="main-content"')
    assert "</header>" in base[:base.index('<main class="main-content"')]


def test_the_soft_reload_replaces_both_regions():
    core = _read(_JS / "core.js")

    assert "'header.page-hero'" in core
    assert "'main.main-content'" in core


def test_a_partial_swap_falls_back_to_a_full_reload():
    """A new hero over an old body is worse than a full reload. So all areas are
    collected before touching any of them."""
    core = _read(_JS / "core.js")
    # The swap itself is in `sfApplyHtml`, shared by the refresh and switching
    # months (11.10)
    apply = core[core.index("function sfApplyHtml"):]
    apply = apply[:apply.index("\n}\n")]
    assert "if (pairs.some(p => !p[0] || !p[1])) return 'full';" in apply
    assert apply.index("pairs.some") < apply.index("pairs.forEach")
    block = core[core.index("window.softReload = function"):]
    block = block[:block.index("\n};")]
    assert "=== 'full') return fullReload();" in block
    assert "window.location.reload()" in block


def test_the_numbers_animate_again_after_a_refresh():
    """The count-up ran only on DOMContentLoaded, so after a soft refresh the new
    numbers appeared with no animation, exactly when they are waited for."""
    motion = _read(_JS / "motion.js")

    assert "window.addEventListener('sf:refreshed'" in motion
    block = motion[motion.index("window.addEventListener('sf:refreshed'"):]
    assert "runCountUps()" in block
    assert "runStagger()" not in block, "an entrance animation on every update is noise"


# ─── Real behaviour, run in node ────────────────────────────────────────────

_HARNESS = r"""
const fs = require('fs');
const g = globalThis;

const replaced = [];
function el(sel, ok) {
    return ok ? { sel, replaceWith(fresh) { replaced.push(fresh.sel); } } : null;
}

// העמוד החי מכיל את שני האזורים; התשובה מהשרת — לפי הארגומנט
const stub = () => ({
    style: {}, dataset: {}, textContent: '', innerHTML: '',
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, appendChild() {}, focus() {}, setAttribute() {},
    querySelector: () => null, querySelectorAll: () => [],
});

const liveHas   = new Set(['header.page-hero', 'main.main-content']);
const freshHas  = new Set(JSON.parse(process.argv[2]));
// תרחיש: בלוקי נתונים בעמוד החי ובתשובה, קנבס בתשובה, והאם Chart.js נטען
const cfg = JSON.parse(process.argv[4] || '{}');
const liveData = {};
Object.entries(cfg.liveData || {}).forEach(([id, text]) =>
    liveData[id] = { id, tagName: 'SCRIPT', textContent: text });
let storedToast = null;

// core.js עושה עוד דברים בטעינה (toast, דיאלוג אישור) — כולם
// מקבלים null ומדלגים על עצמם בשקט, בדיוק כמו בעמוד בלי הרכיבים.
g.document = {
    querySelector: (sel) => el(sel, liveHas.has(sel)),
    querySelectorAll: () => [],
    // core.js מניח שרכיבי הדיאלוג קיימים ולא בודק null, אז מחזירים
    // אובייקט אדיש במקום null
    getElementById: (id) => (id === 'sf-page-data'
        ? Object.assign(stub(), { textContent: '{}' })
        : (liveData[id] || stub())),
    createElement: () => stub(),
    addEventListener() {},
    body: { appendChild() {}, style: {} },
};
g.window = g;
g.CustomEvent = class { constructor(t) { this.type = t; } };
let reloaded = false;
g.location = { href: 'http://x/', reload() { reloaded = true; } };
g.dispatchEvent = () => {};
g.addEventListener = () => {};
g.matchMedia = () => ({ matches: false });
// ‎sessionStorage‎/‎localStorage‎ קיימים כגלובלי רק מ-node 24. ב-CI רץ
// node 22, ושם הם נעדרים — וזה מה שהפיל את הבדיקה הזאת בפעם הראשונה.
g.sessionStorage = { getItem: () => null, setItem(k, v) { if (k === 'sf_toast') storedToast = v; }, removeItem() {} };
g.localStorage   = { getItem: () => null, setItem() {}, removeItem() {} };
g.requestAnimationFrame = (fn) => fn(0);
g.DOMParser = class {
    parseFromString() { return {
        querySelector: (sel) => sel === 'main canvas'
            ? (cfg.freshCanvas ? {} : null)
            : el(sel, freshHas.has(sel)),
        querySelectorAll: (sel) => sel.startsWith('script[type="application/json"]')
            ? Object.entries(cfg.freshData || {}).map(([id, textContent]) => ({ id, textContent }))
            : [],
    }; }
};
if (cfg.chartLoaded) g.Chart = function () {};
g.fetch = () => Promise.resolve({ ok: true, text: () => Promise.resolve('<html></html>') });

(0, eval)(fs.readFileSync(process.argv[3], 'utf8'));

g.softReload(null, cfg.toast).then(function (how) {
    const data = {};
    Object.keys(liveData).forEach(id => data[id] = liveData[id].textContent);
    console.log(JSON.stringify({ replaced, reloaded, how, storedToast, data }));
});
"""


def _run(fresh_regions, **cfg):
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    harness = _ROOT / "tests" / "_hero_harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    try:
        out = subprocess.run(
            [node, str(harness), json.dumps(fresh_regions), str(_JS / "core.js"), json.dumps(cfg)],
            capture_output=True, text=True, timeout=30)
    finally:
        harness.unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_both_regions_are_actually_swapped():
    res = _run(["header.page-hero", "main.main-content"])

    assert res["replaced"] == ["header.page-hero", "main.main-content"]
    assert res["reloaded"] is False


def test_a_missing_region_swaps_nothing_and_reloads():
    """Control: a half swap leaves a screen that contradicts itself."""
    res = _run(["main.main-content"])       # no hero in the server's response

    assert res["replaced"] == [], "one area was replaced even though the other was missing"
    assert res["reloaded"] is True


# ─── The first transaction ──────────────────────────────────────────────────

def _inside_braces(text, opener):
    """The content between the braces of the block starting at `opener`.

    Comparing positions isn't enough here: a line sitting **after** `if (list) {`
    but **outside** its braces passes it, and that is exactly the broken state.
    Verified with a mutation test: the previous version of this test didn't fail
    on it."""
    i = text.index(opener) + len(opener)
    depth, j = 1, i
    while depth:
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
        j += 1
    return text[i:j - 1]


def test_the_modal_only_closes_when_there_is_a_row_to_show():
    """An empty dashboard has no `.transactions-list`, so no temporary row was
    created, but the sheet closed anyway, and every error message was written
    into a closed sheet."""
    js = _read(_JS / "transactions.js")
    block = js[js.index("let placeholderRow = null;"):]
    block = block[:block.index("const url    = editId")]

    guarded = _inside_braces(block, "if (list) {")

    assert "closeModal();" in guarded, "closeModal outside the condition"
    # And the button isn't released either while the sheet is open waiting for
    # the server
    assert "setSubmitBusy(false);" in guarded
    # And a control for the whole test: none of them remained outside the condition
    outside = block.replace(guarded, "")
    assert "closeModal();" not in outside


def test_the_empty_dashboard_really_has_no_list():
    """The root of B2: if the template starts rendering an empty list, the condition
    above no longer protects anything."""
    index = _read(_TPL / "index.html")
    block = index[index.index("transactions-list") - 400:index.index("transactions-list")]

    assert "{% if transactions %}" in block


# ─── core.js doesn't fall over when there is no storage ─────────────────────

def test_storage_access_cannot_take_the_whole_file_down():
    """`sessionStorage.getItem` runs when the file loads. An exception there
    (private browsing, blocked storage, or an environment without Web Storage)
    took down **all** of core.js: no toast, no confirm dialog, no soft refresh.

    That is how it was found: CI runs node 22, which has no `sessionStorage` as a
    global, and the tests here failed on something unrelated to them."""
    core = _read(_JS / "core.js")

    # `localStorage.getItem` was here for the sound flag, which went away with
    # the sound (11.10)
    for call in ("sessionStorage.getItem", "sessionStorage.removeItem"):
        i = core.index(call)
        before = core[max(0, i - 300):i]
        assert "try {" in before, f"{call} without try"


def test_the_harness_does_not_depend_on_the_node_version():
    """The harness itself provides what core.js touches on load, so the test fails
    on the code it tests and not on the runtime environment."""
    src = _HARNESS

    assert "g.sessionStorage" in src
    assert "g.localStorage" in src


# ─── The month page: the charts' data, and a month that was empty ───────────

_BOTH = ["header.page-hero", "main.main-content"]


def test_the_data_behind_the_charts_is_refreshed_too():
    """`sf-view-data` sits outside `main`. Without this the pie and the legend were
    redrawn from the old numbers, under cards that had already updated."""
    res = _run(_BOTH, chartLoaded=True,
               liveData={"sf-view-data": '{"summary": {"expense": 100}}'},
               freshData={"sf-view-data": '{"summary": {"expense": 600}}'})

    assert res["reloaded"] is False
    assert res["data"]["sf-view-data"] == '{"summary": {"expense": 600}}'


def test_charts_without_the_library_reload_fully_and_keep_the_message():
    """An empty month doesn't load Chart.js; the first transaction brings charts, and
    without the library they stayed empty squares. And the "transaction added"
    message isn't lost."""
    res = _run(_BOTH, chartLoaded=False, freshCanvas=True, toast="העסקה נוספה")

    assert res["reloaded"] is True
    assert res["how"] == "reloaded", "the caller will show the message twice"
    assert res["storedToast"] == "העסקה נוספה"
    assert res["replaced"] == [], "something was replaced a moment before the page reloads"


def test_a_page_that_already_has_the_library_stays_soft():
    """Control: only the library's absence justifies a full load."""
    res = _run(_BOTH, chartLoaded=True, freshCanvas=True, toast="העסקה נוספה")

    assert res["reloaded"] is False
    assert res["storedToast"] is None
