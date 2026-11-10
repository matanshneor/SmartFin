"""
"בטל" brought the transaction back, without the receipt, and without saying a word.

Deleting a transaction deletes the receipt image from storage immediately. That is
right: otherwise storage would fill with files no transaction points to. But the
"בטל" window stays open six seconds after that, and the restore sends
receipt_path: null explicitly, because a URL to a deleted file would produce a
broken 📎 tag.

The result: the user tapped "בטל", saw the transaction come back, and assumed
everything came back. The receipt simply wasn't there any more, and nothing
indicated it.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS   = (_ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
_CSS  = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _block(start):
    """The function body, bounded by structure and not by a magic character count.

    The previous version cut a fixed 1400 characters, and broke the moment logic
    was added at the top of the function, without any behaviour changing. A test
    that breaks on a refactor teaches people to ignore it."""
    body = _JS[_JS.index(start) + len(start):]
    end = body.find("\n    function ")
    return body[:end if end != -1 else len(body)]


# ─── Say it, and only when it is true ───────────────────────────────────────

def test_the_deletion_warns_that_the_receipt_is_gone():
    block = _block("function deleteWithUndo(")

    assert "ולא תחזור" in block, "the delete doesn't warn that the receipt won't come back"


def test_the_warning_only_appears_when_there_was_a_receipt():
    """Control: a warning about a receipt that didn't exist would teach people to
    ignore messages."""
    block = _block("function deleteWithUndo(")

    assert "querySelector('.receipt-badge')" in block, "there is no check whether there was a receipt at all"
    assert re.search(r"hadReceipt\s*\n?\s*\?", block), "the message isn't conditional"


def test_the_plain_message_is_still_there_for_a_transaction_without_one():
    block = _block("function deleteWithUndo(")

    assert "'העסקה נמחקה'" in block


# ─── And also after the restore ─────────────────────────────────────────────

def test_the_restore_says_what_did_not_come_back():
    """This is the moment the user really looks at the result."""
    block = _block("function restoreTransaction(")

    assert "בלי הקבלה" in block


def test_the_restore_knows_whether_there_was_one():
    block = _block("function deleteWithUndo(")

    assert "hadReceipt: hadReceipt" in block, "the knowledge isn't passed to the restore"


def test_the_restore_still_sends_a_null_path():
    """The file was deleted from storage. A URL to it would produce a broken 📎 tag."""
    block = _block("function restoreTransaction(")

    assert "receipt_path:        null" in block


# ─── The message must be readable ───────────────────────────────────────────

def test_a_long_message_is_not_cut_in_half():
    """nowrap + ellipsis cut every message longer than about 30 characters,
    including this warning, which is exactly the message that mustn't be lost."""
    block = _CSS[_CSS.index(".toast .toast-msg"):][:400]

    assert "white-space: nowrap" not in block
    assert "line-clamp: 2" in block, "without a ceiling, an unusual message will stretch the bubble"
