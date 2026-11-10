"""
Tests for the refresh after deleting a transaction.

Deleting removes the row right away and shows "undo" for six seconds. At the end
of the window the page reloads, so the balance and KPI update.

The problem: that reload was forced. A user who deleted a wrong transaction and
immediately opened the modal to type the fix (exactly the sequence people do)
got a reload mid-word. The modal closed, and everything they typed was lost.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_JS = (Path(__file__).resolve().parent.parent
       / "frontend/static/js/transactions.js").read_text(encoding="utf-8")


def _fn(name):
    start = _JS.index(f"function {name}(")
    depth, i = 0, _JS.index("{", start)
    for j in range(i, len(_JS)):
        if _JS[j] == "{": depth += 1
        elif _JS[j] == "}":
            depth -= 1
            if depth == 0: return _JS[start:j + 1]
    raise AssertionError(name)


# ─── The heart ──────────────────────────────────────────────────────────────

def test_the_refresh_waits_while_something_is_being_edited():
    body = _fn("refreshAfterDelete")

    assert "somethingIsBeingEdited()" in body
    assert "refreshWhenEditingEnds = true" in body


def test_the_editing_window_counts():
    """Since 5.10 there is only one edit sheet, on the home page too (the inline
    editor was removed)."""
    body = _fn("somethingIsBeingEdited")

    assert "overlay.classList.contains('open')" in body


def test_the_deferred_refresh_actually_happens_when_editing_ends():
    """A deferral without completion is a balance that never updates."""
    assert "refreshIfPending();" in _fn("closeModal")

    body = _fn("refreshIfPending")
    assert "refreshWhenEditingEnds" in body and "refreshAfterDelete()" in body


def test_undoing_the_delete_cancels_the_pending_refresh():
    """The transaction was restored: nothing to refresh, and a refresh would close
    an open modal for nothing."""
    # the delete's (not the edit's "undo", round 6, item 10, which appears
    # earlier in the file)
    block = _JS[_JS.index("label: 'ביטול'", _JS.index("function deleteWithUndo")):][:400]

    assert "refreshWhenEditingEnds = false" in block
    assert "clearTimeout(pendingDeleteReload)" in block


# ─── And what happens when we do refresh ────────────────────────────────────

def test_it_prefers_the_soft_reload_where_the_page_allows_it():
    """A soft refresh doesn't touch the modal at all: it sits outside main."""
    body = _fn("refreshAfterDelete")

    assert "main[data-soft-reload]" in body
    assert "window.softReload()" in body


def test_a_page_without_soft_reload_still_gets_its_totals_updated():
    """Control: the caution shouldn't leave a stale balance on the screen."""
    assert "window.location.reload()" in _fn("refreshAfterDelete")


def test_the_six_second_undo_window_is_unchanged():
    """The deferral is about what happens at the end of the window, not during it."""
    assert re.search(r"setTimeout\(refreshAfterDelete,\s*6000\)", _JS)
