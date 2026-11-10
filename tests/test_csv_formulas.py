"""The CSV export doesn't hand Excel formulas.

A cell starting with `=`, `+`, `-` or `@` is a formula in Excel. An innocent
description like "`-50` הנחה" became `#NAME?`, and a malicious description can
run a formula for whoever opens the file (CSV injection). A leading apostrophe
tells Excel "text".
"""
import csv
import io

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"


def _tx(**kw):
    return {"date": "2026-09-01", "type": "expense", "amount": 50.0,
            "category_name": "סופר", "description": "", "user_name": "מתן",
            "project_name": "", **kw}


@pytest.fixture
def export(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))

    def run(rows):
        monkeypatch.setattr(db, "get_month_transactions", lambda *a, **k: rows)
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            body = c.get("/month.csv?year=2026&month=9").get_data(as_text=True)
        return list(csv.reader(io.StringIO(body.lstrip("﻿"))))[1:]
    return run


@pytest.mark.parametrize("text", ["=1+1", "+972", "-50 הנחה", "@SUM(A1)", "\t=1", "\r=1"])
def test_a_formula_start_becomes_text(export, text):
    row = export([_tx(description=text)])[0]

    assert row[4] == "'" + text


def test_every_text_column_is_covered(export):
    row = export([_tx(category_name="=c", description="=d", user_name="=u", project_name="=p")])[0]

    assert [row[3], row[4], row[5], row[6]] == ["'=c", "'=d", "'=u", "'=p"]


def test_ordinary_text_is_untouched(export):
    """Control: only the start is checked, and only these characters."""
    row = export([_tx(description="קפה = 12 ש״ח", category_name="מסעדות")])[0]

    assert row[3:5] == ["מסעדות", "קפה = 12 ש״ח"]


def test_the_amount_stays_a_number(export):
    """An amount is a number and not text; an apostrophe would turn it into text
    that can't be summed."""
    row = export([_tx(amount=-12.5)])[0]

    assert row[2] == "-12.50"
