"""
Tests for three places where an action failed or was cancelled, and the screen
didn't tell the truth.

25: a settings switch flips by itself on tap, before the server is asked. On
    failure a red message was shown for two seconds and the switch stayed in the
    new state. The user navigated away confident it was saved, and on the next
    refresh it went back, with no visible link. A screen showing a state that
    wasn't saved is worse than an error message: it promises.

26: copying the invite code failed silently: `.then()` with no `.catch`. A
    denied permission, an insecure context or an in-app browser (a link opened
    from WhatsApp), and nothing happened. This is the action that makes a family
    a family.

28: on the second question of deleting a project, escaping from the dialog was
    read as "keep the transactions" and the project was deleted anyway. Whoever
    panicked and tapped outside meant to back out, and deleting a project has no
    undo.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "frontend/static/js"


def _read(name):
    return (_JS / name).read_text(encoding="utf-8")


# ─── 25: a switch that wasn't saved goes back ───────────────────────────────

def test_a_failed_preference_save_puts_the_control_back():
    body = _read("settings.js")
    fn = body[body.index("function savePrefs"):][:1400]

    assert "revert" in fn, "no revert path: the switch stays in a state that wasn't saved"
    assert "if (revert) revert();" in fn


def test_a_server_error_status_counts_as_failure_too():
    """Before, only `data.error` was checked; a 500 with an empty body looked like success."""
    body = _read("settings.js")
    fn = body[body.index("function savePrefs"):][:1400]

    assert "res.ok" in fn


@pytest.mark.parametrize("control,anchor", [
    ("שיוך הוצאה/הכנסה/חיסכון", "attrSwitches.forEach"),
    # "Overage alerts" and "show workplace" were removed from settings (Matan, 11.10)
])
def test_every_switch_passes_a_way_back(control, anchor):
    """One forgotten switch is a switch that keeps lying."""
    body = _read("settings.js")
    block = body[body.index(anchor):][:700]

    assert "savePrefs(" in block
    assert "checked = was" in block, f"{control} doesn't revert on failure"


def _strip_comments(js):
    """Strip comments before any code scan.

    A comment containing an example call was counted as a real call, and the
    bracket matcher ran from it to the end of the file and swallowed everything.
    It happened in practice: a comment explaining a bug broke the test meant to
    find it."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def _call_args(body, name):
    """The arguments of every call to name, by real bracket matching. A regex isn't
    enough: some calls span several lines and contain nested brackets."""
    body = _strip_comments(body)
    out = []
    needle = name + "("
    i = body.find(needle)
    while i != -1:
        j, depth = i + len(needle), 1
        while j < len(body) and depth:
            if body[j] == "(": depth += 1
            elif body[j] == ")": depth -= 1
            j += 1
        out.append(body[i + len(needle):j - 1])
        i = body.find(needle, j)
    return out


def _split_args(args):
    """Splits an argument list on top-level commas only: a comma inside an object
    or a function body doesn't separate arguments."""
    out, depth, current = [], 0, ""
    for ch in args:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(current.strip()); current = ""
        else:
            current += ch
    if current.strip():
        out.append(current.strip())
    return out


def test_every_call_to_saveprefs_passes_a_way_back():
    """The test that will catch the next switch added: every call, with no exception."""
    body = _read("settings.js")
    # The first call is the definition itself (`function savePrefs(patch, revert)`)
    calls = _call_args(body, "savePrefs")[1:]

    # Attribution and budgets. Alerts and workplace were removed from settings
    # (Matan, 11.10)
    assert len(calls) >= 2, f"only {len(calls)} calls were found: the test probably isn't finding them"
    for args in calls:
        # Any second argument: an inline function, or the name of one passed in.
        # What is checked is that there is a revert path, not how it is written.
        assert len(_split_args(args)) >= 2, \
            f"a call to savePrefs with no revert path: {args[:60]}"


# ─── 26: a failed copy says so ──────────────────────────────────────────────

def test_copying_has_a_failure_path_at_all():
    clip = _read("clipboard.js")

    assert ".catch(" in clip, "still an unhandled rejection: a silent failure"
    assert "!navigator.clipboard" in clip, "it isn't checked that the object even exists"


def test_a_failed_copy_leaves_the_user_something_to_do():
    """Selecting the text leaves one action (copying by hand) instead of wondering."""
    clip = _read("clipboard.js")

    assert "selectNodeContents" in clip
    assert "showToast" in clip


@pytest.mark.parametrize("caller", ["settings.js", "onboarding.js"])
def test_both_copy_buttons_use_the_shared_helper(caller):
    """The two places that show an invite code: settings and the signup wizard."""
    body = _read(caller)

    assert "window.copyToClipboard(" in body
    assert "navigator.clipboard" not in body, "a direct call with no failure handling remains"


def test_the_helper_is_loaded_where_it_is_used():
    """`onboarding.html` doesn't load core.js (the elements it expects don't exist
    there), so the helper sits in a standalone file of its own."""
    tpl = Path(__file__).resolve().parent.parent / "frontend/templates"

    for page in ("base.html", "onboarding.html"):
        assert "js/clipboard.js" in (tpl / page).read_text(encoding="utf-8"), page
    assert "getElementById" not in _read("clipboard.js"), \
        "the helper depends on elements: it is loaded on a page that doesn't have them too"


# ─── 28: backing out of a dialog is backing out ─────────────────────────────

def test_dismissing_a_dialog_is_distinguishable_from_saying_no():
    core = _read("core.js")

    assert "closeConfirm(null)" in core, "Escape and a tap outside are still reported as 'no'"
    assert core.count("closeConfirm(null)") == 2, "one of the retreat paths is missing"
    assert "closeConfirm(false)" in core, "the cancel button must stay 'no'"


def test_existing_callers_are_unaffected():
    """`null` was chosen because it is falsy: every existing `if (!ok) return`
    keeps working."""
    core = _read("core.js")
    assert "falsy" in core[core.index("Three outcomes"):core.index("window.appConfirm")]


# `projects.js` was dropped from the list: deleting a project moved into the
# project itself, and there are no longer two questions in a row in it. The
# choice of what happens to the transactions is a picker in a form that opens
# and shows how many transactions are at stake, i.e. there is no "second
# question" to back out of, and there is a form that must be submitted
# explicitly. The test below guards the property that replaced it.
@pytest.mark.parametrize("path", ["settings.js"])
def test_backing_out_of_the_second_question_cancels_everything(path):
    body = _read(path)

    assert "=== null" in body, f"{path}: backing out still deletes"


# "The default keeps the money" used to sit here. The choice itself was removed
# on 28.9.2026: "keep" landed in the household as "uncategorised" and made past
# months more expensive. The new decision is tested in
# tests/test_project_list_and_deletion.py.


def test_it_says_how_many_transactions_are_at_stake():
    """✕ in the list carried no number. Inside the project it is possible to say
    exactly what will be lost."""
    # Deletion at the bottom of the project page since 9.10
    html = (_ROOT / "frontend/templates/project_detail.html").read_text(encoding="utf-8")
    block = html[html.index("project-danger"):]

    assert "tx_count" in block


def test_backing_out_of_removing_a_family_member_cancels_too():
    """The same pattern, the same fix: two questions in a row."""
    body = _read("settings.js")
    fn = body[body.index("function askAboutTransactions"):][:1100]

    assert "wipe === null" in fn
