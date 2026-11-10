"""
Tests for messages shown to the user.

15 error messages were returned to the screen in English: "Missing required
fields: amount, type, date", "No family linked to account", "type must be
expense, income, or savings". They were written as messages for the developer,
and the code showed them as is to a user whose whole app is in Hebrew.

The root is that the DB layer returns two kinds of strings in the same place:
messages written for the user, and internal markers like "Database not
configured" or a raw str(e) from Supabase. The routes showed both.
"""
import ast
import re
from pathlib import Path

import pytest

from backend import app as app_module

pytestmark = pytest.mark.unit

_APP = Path(__file__).resolve().parent.parent / "backend/app.py"
_HEBREW = re.compile(r"[֐-׿]")


def _literal_error_messages():
    """Every `jsonify({"error": "…"})` with a fixed string."""
    tree = ast.parse(_APP.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "jsonify"):
            continue
        for arg in node.args:
            if not isinstance(arg, ast.Dict):
                continue
            for k, v in zip(arg.keys, arg.values):
                if (isinstance(k, ast.Constant) and k.value == "error"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    out.append((node.lineno, v.value))
    return out


def test_no_route_answers_the_user_in_english():
    english = [(l, m) for l, m in _literal_error_messages() if not _HEBREW.search(m)]

    assert not english, f"English messages for a Hebrew user: {english}"


def test_there_are_actually_messages_to_check():
    """Guards against a test that passes because it found nothing."""
    assert len(_literal_error_messages()) > 20


# ─── The gate that prevents the next leak ───────────────────────────────────

def test_an_internal_string_never_reaches_the_screen():
    """`"Database not configured"` is a marker for the log. It appeared 23 times in
    the DB layer, and on several routes was returned to the user as is."""
    assert app_module._user_message("Database not configured") == "הפעולה נכשלה — נסו שוב"


def test_a_raw_supabase_exception_never_reaches_the_screen():
    """Several functions return `str(e)`: a library's text, sometimes with query
    details in it."""
    msg = app_module._user_message("APIError: {'code': 'PGRST116', 'details': ...}")

    assert msg == "הפעולה נכשלה — נסו שוב"


def test_a_message_written_for_the_user_passes_through():
    """Control: the gate shouldn't swallow good messages."""
    real = "רק הבעלים של הפרויקט יכול להפוך אותו למשותף"

    assert app_module._user_message(real) == real


def test_the_caller_can_choose_its_own_fallback():
    assert app_module._user_message(None, "עדכון הסיסמה נכשל") == "עדכון הסיסמה נכשל"


def test_a_leaked_internal_string_is_logged_so_it_can_be_fixed(caplog):
    """A silent swallow would replace one bug with another."""
    import logging
    with caplog.at_level(logging.WARNING):
        app_module._user_message("Database not configured")

    assert any("internal error" in r.message for r in caplog.records)
