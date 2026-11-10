"""
Tests for the CSV export.

Data you can take out is data you can trust, and this is the cheapest answer to
"what happens if I want to leave": a question a family asks exactly before
putting its money into the app.

Two details decide whether the file really opens correctly, and both are
invisible in a quick manual check: a BOM at the start of the file, and CRLF at
the end of a line. Without the BOM Excel reads UTF-8 as a local encoding and all
the Hebrew text turns into gibberish, and it looks like a bug in the app, not in
the program opening it.
"""
import csv
import io as _io

import pytest

from backend import app as app_module
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROWS = [
    {"date": "2026-09-03", "type": "expense", "amount": 240.5,
     "category_name": "מזון", "description": "סופר", "user_name": "מתן",
     "project_name": None, "is_recurring": False, "recurring_parent_id": None},
    {"date": "2026-09-05", "type": "income", "amount": 14000.0,
     "category_name": "משכורת", "description": "", "user_name": "אור",
     "project_name": None, "is_recurring": True, "recurring_parent_id": None},
    {"date": "2026-09-09", "type": "expense", "amount": 22000.0,
     "category_name": "טיסות", "description": 'כרטיסים, הלוך "ושוב"',
     "user_name": "משותף", "project_name": "טיול ליפן",
     "is_recurring": False, "recurring_parent_id": None},
]


@pytest.fixture(autouse=True)
def _fresh_limits():
    """The limiter's counters live in process memory and don't expire within a test
    run. Without the reset, the 11th test in the file gets a 429 instead of what
    it really tests."""
    limiter.reset()


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "get_month_transactions",
                        lambda fid, y, m, settings=None, viewer_user_id=None: _ROWS)
    monkeypatch.setattr(app_module, "family_settings", lambda: {})
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "11111111-1111-1111-1111-111111111111"
            sess["family_id"] = "22222222-2222-2222-2222-222222222222"
        yield c


def _fetch(client, **params):
    return client.get("/month.csv", query_string=params or {"year": 2026, "month": 9})


# ─── What makes the file open correctly ─────────────────────────────────────

def test_the_file_starts_with_a_bom_so_hebrew_is_not_mangled(client):
    """Without this Excel shows gibberish, and the user blames the app."""
    body = _fetch(client).get_data()

    assert body.startswith(b"\xef\xbb\xbf"), "no BOM: the Hebrew text will break in Excel"


def test_rows_end_with_crlf(client):
    body = _fetch(client).get_data()

    assert b"\r\n" in body


def test_it_is_served_as_a_download_with_a_dated_name(client):
    response = _fetch(client, year=2026, month=3)

    assert response.headers["Content-Type"].startswith("text/csv")
    assert 'filename="smartfin-2026-03.csv"' in response.headers["Content-Disposition"]


def test_it_is_never_cached(client):
    """A file containing all of the month's transactions shouldn't be cached by a proxy."""
    assert _fetch(client).headers["Cache-Control"] == "no-store"


# ─── The content ────────────────────────────────────────────────────────────

def _parse(client, **params):
    text = _fetch(client, **params).get_data(as_text=True).lstrip("﻿")
    return list(csv.reader(_io.StringIO(text)))


def test_every_transaction_appears_once_under_a_header(client):
    rows = _parse(client)

    assert rows[0][0] == "תאריך"
    assert len(rows) == 1 + len(_ROWS)


def test_the_type_is_written_in_hebrew_not_as_the_database_value(client):
    """The file is opened in front of an accountant, not a programmer."""
    rows = _parse(client)

    assert [r[1] for r in rows[1:]] == ["הוצאה", "הכנסה", "הוצאה"]


def test_amounts_keep_their_agorot(client):
    """`{:,.0f}` on screen rounds to whole shekels; in an export that would lose
    information."""
    rows = _parse(client)

    assert rows[1][2] == "240.50"


def test_quotes_and_commas_in_a_description_survive(client):
    """A comma in the description is the classic way to break a CSV: csv.writer
    handles it, but only if it is really used rather than joining strings."""
    rows = _parse(client)

    assert rows[3][4] == 'כרטיסים, הלוך "ושוב"'


def test_the_project_and_recurring_columns_are_filled(client):
    rows = _parse(client)

    assert rows[3][6] == "טיול ליפן"
    assert rows[2][7] == "כן"
    assert rows[1][7] == ""


# ─── Privacy and permissions ────────────────────────────────────────────────

def test_the_export_hides_what_the_screen_hides(client, monkeypatch):
    """The same query as the month page, including filtering out another family
    member's personal project. An export that bypasses the filter is a back door
    to private data."""
    seen = {}
    monkeypatch.setattr(app_module.db, "get_month_transactions",
                        lambda fid, y, m, settings=None, viewer_user_id=None:
                            (seen.update(viewer=viewer_user_id) or []))

    _fetch(client)

    assert seen["viewer"] == "11111111-1111-1111-1111-111111111111"


def test_it_needs_a_session():
    app.config["TESTING"] = True
    response = app.test_client().get("/month.csv")

    assert response.status_code == 302
