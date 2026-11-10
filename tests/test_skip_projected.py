"""Tapping a "צפוי" occurrence in a future month (Matan, 1.11): "דלג על החודש הזה"
or "עריכת הסדרה". The skip is recorded in the template's recurring_skips (the
occurrence doesn't exist yet), and "בטל" removes it."""
import datetime
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
_TPL = "11111111-1111-1111-1111-111111111111"
_FAM = "22222222-2222-2222-2222-222222222222"
_ME = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module.db, "personal_project_owner", lambda *a: None)
    monkeypatch.setattr(app_module.clock, "today", lambda: datetime.date(2026, 11, 1))
    calls = []
    monkeypatch.setattr(app_module.db, "skip_recurring_date",
                        lambda *a: (calls.append(a), (True, None))[1])
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield c, calls


def test_skipping_a_future_month(client):
    c, calls = client
    r = c.put(f"/api/recurring/{_TPL}/skip", json={"date": "2026-12-10"})
    assert r.status_code == 200
    assert calls == [(_TPL, _FAM, "2026-12-10", True)]


def test_undo_removes_the_skip(client):
    c, calls = client
    c.put(f"/api/recurring/{_TPL}/skip", json={"date": "2026-12-10", "skip": False})
    assert calls == [(_TPL, _FAM, "2026-12-10", False)]


@pytest.mark.parametrize("bad", ["2026-11-01", "2026-10-10", "nope", None, 5])
def test_only_dates_that_have_not_come(client, bad):
    """What already went out is a real transaction: deleted like a transaction, not
    skipped."""
    c, calls = client
    r = c.put(f"/api/recurring/{_TPL}/skip", json={"date": bad})
    assert r.status_code == 422 and calls == []


def test_someone_elses_private_series_is_not_found(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(app_module.db, "personal_project_owner", lambda *a: "someone-else")
    r = c.put(f"/api/recurring/{_TPL}/skip", json={"date": "2026-12-10"})
    assert r.status_code == 404 and calls == []


def test_a_missing_series_says_so(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(app_module.db, "skip_recurring_date", lambda *a: (False, "not found"))
    r = c.put(f"/api/recurring/{_TPL}/skip", json={"date": "2026-12-10"})
    assert r.status_code == 404


def test_the_database_does_it_in_one_atomic_call(monkeypatch):
    sent = []

    class _Rpc:
        def rpc(self, name, params): sent.append((name, params)); return self
        def execute(self):
            class R: data = 1
            return R()
    monkeypatch.setattr(db, "get_client", lambda: _Rpc())
    assert db.skip_recurring_date(_TPL, _FAM, "2026-12-10", True) == (True, None)
    assert sent == [("skip_recurring_date", {"p_template_id": _TPL, "p_family_id": _FAM,
                                             "p_date": "2026-12-10", "p_skip": True})]


def test_the_db_function_checks_the_caller_and_the_date():
    sql = (ROOT / "backend/supabase/migrations/20261101220902_skip_recurring_date.sql").read_text()
    assert "auth.uid()" in sql and "is_recurring" in sql
    assert "p_date <= (now() at time zone 'Asia/Jerusalem')::date" in sql
    assert "from public, anon" in sql


def test_the_projected_row_carries_what_the_question_needs():
    html = (ROOT / "frontend/templates/month.html").read_text(encoding="utf-8")
    assert 'data-template-id="{{ tx.recurring_parent_id }}" data-date="{{ tx.date }}"' in html
    assert "js/projected.js" in html
    js = (ROOT / "frontend/static/js/projected.js").read_text(encoding="utf-8")
    assert "'/settings?series='" in js and "label: 'ביטול'" in js
