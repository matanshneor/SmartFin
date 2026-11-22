"""
The budget field never saved anything.

`savePrefs` was defined inside the "family settings" block, and the category
budget code sits in a completely different block. Two separate scopes, i.e. the
function simply doesn't exist there: every keystroke in the budget field threw
`ReferenceError: savePrefs is not defined` from a `setTimeout`, with no message,
no error on screen, no sign at all. Matan reported it in the words "as if it
wasn't saved", and that was accurate: no request went out.

This is the second half of the same feature. The first (`apply_budgets`
looking for a `category_id` that wasn't in the breakdown rows) was fixed
earlier. Both halves were broken in parallel, so the feature didn't work from
the day it shipped.

**Why no test caught it:** the tests looked for the string `savePrefs({ limits:`
in the source, and it was there. Presence in the text isn't reachability in
scope. The tests here run the real file and check that a request really goes out.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS   = _ROOT / "frontend/static/js"

_CAT = "c1"

# A minimal DOM of one category's budget fields, as on the settings page.
_HARNESS = r"""
const fs = require('fs'), g = globalThis;
const listeners = {};
const CAT = 'c1';

const amountEl = { classList: { contains: c => c === 'budget-amount' },
                   dataset: { id: CAT, saved: '' }, value: '500', focus() {},
                   closest: (s) => s === '.budget-amount' ? amountEl
                                 : (s === '.cat-budget-edit' ? editEl : null) };
const enabledEl = { checked: true, dataset: { id: CAT },
                    closest: (s) => s === '.cat-budget-edit' ? editEl : null,
                    classList: { contains: () => false } };
const fieldsEl = { hidden: false };
const saveBtn  = { disabled: false, textContent: 'שמור', dataset: { id: CAT },
                   closest: (s) => s === '.budget-save' ? saveBtn
                                 : (s === '.cat-budget-edit' ? editEl : null) };
const editEl = { querySelector: (s) => ({
    '.budget-enabled': enabledEl, '.budget-amount': amountEl,
    '.budget-save': saveBtn, '.cat-budget-fields': fieldsEl }[s] || null) };

const stub = () => ({ style:{}, dataset:{}, textContent:'', innerHTML:'', value:'',
    checked:false, hidden:false, classList:{add(){},remove(){},toggle(){},contains:()=>false},
    addEventListener(){}, appendChild(){}, focus(){}, setAttribute(){},
    closest:()=>stub(), querySelector:()=>stub(), querySelectorAll:()=>[] });

g.document = {
    getElementById: () => stub(),
    querySelector: (s) => s.startsWith('.budget-enabled[data-id') ? enabledEl : stub(),
    querySelectorAll: () => [],
    createElement: () => stub(),
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    body: { appendChild() {}, style: {} },
};
g.window = g; g.location = { href:'http://x/', reload(){}, pathname:'/settings' };
g.sessionStorage = { getItem:()=>null, setItem(){}, removeItem(){} };
g.localStorage   = { getItem:()=>null, setItem(){}, removeItem(){} };
const sent = [], errors = [];
g.fetch = (url, opt) => { sent.push({ url, body: opt && opt.body });
    return Promise.resolve({ ok:true, json:()=>Promise.resolve({ settings:{} }) }); };
g.showToast=()=>{}; g.appConfirm=()=>Promise.resolve(false); g.sfNetError=()=>'net';
g.sfData=()=>({}); g.softReload=()=>Promise.resolve(); g.requestAnimationFrame=fn=>fn(0);
g.matchMedia=()=>({matches:false}); g.SF_PAGE_DATA={}; g.confirm=()=>false;

(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));

// הקלדה בשדה הסכום, ואז לחיצה על "שמור"
(listeners.input || []).forEach(fn => { try { fn({ target: amountEl }); }
                                        catch (e) { errors.push(String(e)); } });
if (process.argv[3] !== 'no-click') {
    (listeners.click || []).forEach(fn => { try { fn({ target: saveBtn }); }
                                            catch (e) { errors.push(String(e)); } });
}

// ההשהיה לפני השמירה היא 600ms
setTimeout(() => {
    console.log(JSON.stringify({ listeners: (listeners.input||[]).length, sent, errors }));
}, 900);

process.on('uncaughtException', (e) => {
    console.log(JSON.stringify({ listeners: (listeners.input||[]).length, sent,
                                 errors: errors.concat(String(e)) }));
    process.exit(0);
});
"""


def _run(click=True):
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    harness = _ROOT / "tests" / "_budget_harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    try:
        out = subprocess.run([node, str(harness), str(_JS / "settings.js"),
                              "click" if click else "no-click"],
                             capture_output=True, text=True, timeout=30)
    finally:
        harness.unlink(missing_ok=True)
    assert out.stdout.strip(), f"the harness printed nothing:\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


# ─── The test that was missing ──────────────────────────────────────────────

def test_saving_a_budget_actually_sends_it_to_the_server():
    """The heart. Until now this threw a ReferenceError and no request went out."""
    res = _run()

    assert res["errors"] == [], f"an error was thrown: {res['errors']}"
    assert len(res["sent"]) == 1, "no request was sent to the server"


def test_the_request_goes_to_the_right_place_with_the_right_shape():
    res = _run()
    req = res["sent"][0]

    assert req["url"] == "/api/family/settings"
    assert json.loads(req["body"]) == {"limits": {_CAT: {"amount": 500}}}


def test_the_listener_is_registered_at_all():
    """Control: if the file crashes on load before the budget block, there is no listener."""
    assert _run()["listeners"] == 1


# ─── Explicit saving, and without a needless question ───────────────────────

def test_typing_alone_saves_nothing():
    """An amount is typed digit by digit. Saving on every keystroke also saves "2"
    and "20" on the way to "200", i.e. a wrong budget, saved, three times."""
    res = _run(click=False)

    assert res["sent"] == [], "saved without save being tapped"


def test_there_is_no_separate_alert_question():
    """There was a second checkbox, "alert on overage". In practice it was a
    distinction without a difference: whoever bothered to set a budget wants to
    know when they went over it."""
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    js   = (_JS / "settings.js").read_text(encoding="utf-8")

    assert "budget-alert" not in html
    assert "budget-alert" not in js


def test_the_server_still_alerts_when_nothing_was_asked():
    """Once the checkbox was removed, the default must be "yes"; otherwise the
    budget becomes a number on the screen with no alert at all."""
    from backend import supabase_config as db

    budget = db.category_budget({"limits": {_CAT: {"amount": 500}}}, _CAT)

    assert budget["alert"] is True


# ─── The rule, not the case ─────────────────────────────────────────────────

def test_no_function_is_called_from_outside_the_block_that_defines_it():
    """This is the rule the bug broke. The file has several independent blocks,
    and calling another block's function silently passes every textual check,
    and fails at runtime, inside a callback nobody watches."""
    import re

    for path in sorted(_JS.glob("*.js")):
        src = path.read_text(encoding="utf-8")

        tops, stack = [], []
        for i, ch in enumerate(src):
            if ch == "{":
                stack.append(i)
            elif ch == "}":
                if len(stack) == 1:
                    tops.append((stack[0], i))
                if stack:
                    stack.pop()
        if len(tops) < 2:
            continue

        defined = {}
        for m in re.finditer(r"^\s{4,}function ([A-Za-z_$][\w$]*)\s*\(", src, re.M):
            for a, b in tops:
                if a < m.start() < b:
                    defined[m.group(1)] = (a, b)
                    break

        for name, (a, b) in defined.items():
            # `(?<!\.)`: `r.json()` is a method of the response, not another block's
            # `json` function
            for m in re.finditer(r"(?<!\.)\b" + re.escape(name) + r"\s*\(", src):
                if a < m.start() < b:
                    continue
                if src[max(0, m.start() - 10):m.start()].rstrip().endswith("function"):
                    continue
                if any(x < m.start() < y for x, y in tops):
                    line = src[:m.start()].count("\n") + 1
                    pytest.fail(f"{path.name}:{line} קורא ל-{name} מחוץ לבלוק שמגדיר אותו")


# ─── Discovery: you can't use what you can't see ────────────────────────────

def _css():
    return (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def test_the_budget_control_is_visible_without_manage_mode():
    """It was hidden behind the same toggle that hides editing, deleting and
    reordering. For those that is right: they are actions on the list. A budget is
    the setting people install a budgeting app for, and Matan couldn't find it."""
    css = _css()

    assert ".cat-budget-edit { display: none; }" not in css
    assert ".managing .cat-budget-edit" not in css


def test_the_budget_block_gets_its_own_line():
    """The block is `width: 100%` inside a flex row. Without `flex-wrap` it is
    squeezed next to the category name instead of dropping under it."""
    css = _css()
    row = css[css.index(".category-row {"):]
    row = row[:row.index("}")]

    assert "flex-wrap: wrap" in row


def test_manage_mode_still_hides_the_list_management_controls():
    """Control: only the budget left "management mode". Editing, deleting and
    reordering stay behind it: they are actions on the list."""
    css = _css()

    for control in (".edit-cat-btn", ".delete-cat-btn", ".cat-reorder-btns"):
        assert f"#categoriesArea:not(.managing) {control}" in css


# ─── The structure Matan asked for ──────────────────────────────────────────

def _budget_block():
    """The budget link on a category's row in the template.

    Since 22.11 (Matan: "one place to set a budget") the row in settings only
    shows the budget and opens the sheet on the month page; the switch, the
    amount box and the save button that used to be here are gone."""
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    start = html.index('<a class="cat-budget-link')
    return html[start:html.index("</a>", start)]


def test_the_row_shows_the_budget_and_opens_the_month_sheet():
    """One place to set a budget: the sheet on the month page. The settings row
    says what the budget is (or offers to set one) and leads there."""
    block = _budget_block()

    assert "url_for('month_view') }}?budgets=1" in block
    assert "תקציב ₪{{ limit.amount | round | int | money }}" in block
    assert "קביעת תקציב" in block
    assert 'type="checkbox"' not in block, "the budget is no longer set from this row"
    js = (_ROOT / "frontend/static/js/month.js").read_text(encoding="utf-8")
    assert ".get('budgets') === '1'" in js and "history.replaceState(null, '', window.location.pathname);" in js


def test_the_switch_reuses_the_existing_component():
    """Control: the style comes from the shared component, not a local copy."""
    css = _css()

    assert ".pref-switch input:checked + .pref-slider" in css
    assert ".cat-budget-toggle" not in css, "a redundant local component remains"


def test_the_amount_box_is_hidden_until_the_toggle_is_on():
    """The author's `display: flex` beats the browser's `[hidden]`. As long as the
    whole block was hidden outside "management mode" this wasn't noticed, and the
    moment it was revealed, the amount box and the save button appeared for
    everyone who hadn't turned anything on.

    Exactly the same trap as in the "no connection" bar, where an explicit
    `[hidden]` was already written."""
    css = _css()

    assert ".cat-budget-fields[hidden] { display: none; }" in css
    assert css.index(".cat-budget-fields {") < css.index(".cat-budget-fields[hidden]"), \
        "the explicit rule must come after, otherwise it doesn't win"
