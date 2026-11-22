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

# Since 22.11 a budget is set in one place, the sheet on the month page; the
# switch, amount box and save button on the settings page are gone, and with
# them the harness here that ran that block of settings.js. What stays below
# is what still holds: no separate alert question, the server's default, no
# call across scopes, and the row in settings that leads to the sheet.


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
