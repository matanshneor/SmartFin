"""בדיקה לפני שמירה של עסקה חדשה (מתן, 30.9 — סבב 6, פריטים 6 ו-7).

6 — כפילות: הכל זהה — סוג, קטגוריה, סכום, יום, הערה, פרויקט ומשויך.
    שאלה ולא חסימה; "הוזנה ע״י אור" רק כשמישהו אחר הזין.
7 — סכום חריג: פי 5 ומעלה מהעסקה הגדולה בקטגוריה בחצי השנה האחרונה, רק
    כשיש לפחות 5 קודמות, ולא בהכנסות.
"""
import datetime

import pytest

from backend import app as app_module
from backend import supabase_config as db
from tests._fake_db import FakeSupabase
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_HER = "33333333-3333-3333-3333-333333333333"
_TODAY = datetime.date(2026, 9, 30)


def _tx(amount, date, cat="food", type_="expense", by=_ME, desc="", project=None, created="2026-09-30T15:40:00+00:00"):
    return {"id": f"{cat}-{amount}-{date}-{by}", "family_id": _FAM, "amount": amount, "type": type_,
            "category_id": cat, "project_category_id": None, "date": date, "description": desc,
            "created_by": by, "created_at": created, "project_id": project, "projects": None}


def _check(rows, monkeypatch, **payload):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    monkeypatch.setattr(db, "get_family_members", lambda fid: [{"id": _ME, "name": "מתן"},
                                                               {"id": _HER, "name": "אור"}])
    body = {"amount": 320, "type": "expense", "category_id": "food", "date": "2026-09-30", **payload}
    return db.precheck_transaction(_FAM, _ME, body, today=_TODAY)


# ── 6. כפילות ─────────────────────────────────────────────────────────

def test_the_same_purchase_entered_by_her_is_a_duplicate(monkeypatch):
    r = _check([_tx(320, "2026-09-30", by=_HER, desc="רמי לוי")], monkeypatch, description="רמי לוי")

    d = r["duplicate"]
    assert d["amount"] == 320 and d["description"] == "רמי לוי" and d["date"] == "2026-09-30"
    assert d["by"] == "אור" and d["time"] == "18:40"          # שעון ישראל


def test_a_different_day_is_not_a_duplicate(monkeypatch):
    assert _check([_tx(320, "2026-09-29")], monkeypatch)["duplicate"] is None
    assert _check([_tx(320, "2026-09-30")], monkeypatch)["duplicate"]


def test_a_different_description_is_not_a_duplicate(monkeypatch):
    assert _check([_tx(320, "2026-09-30", desc="דלק")], monkeypatch, description="סופר")["duplicate"] is None
    # רווחים והבדל אותיות לא נחשבים
    assert _check([_tx(320, "2026-09-30", desc="Super")], monkeypatch, description=" super ")["duplicate"]


def test_a_different_project_is_not_a_duplicate(monkeypatch):
    assert _check([_tx(320, "2026-09-30", project="p1")], monkeypatch)["duplicate"] is None
    assert _check([_tx(320, "2026-09-30", project="p1")], monkeypatch, project_id="p2")["duplicate"] is None
    assert _check([_tx(320, "2026-09-30", project="p1")], monkeypatch, project_id="p1")["duplicate"]


def test_a_different_owner_is_not_a_duplicate(monkeypatch):
    row = _tx(320, "2026-09-30")
    row["user_id"] = _HER
    assert _check([row], monkeypatch, owner=_ME)["duplicate"] is None
    assert _check([row], monkeypatch)["duplicate"] is None
    assert _check([row], monkeypatch, owner=_HER)["duplicate"]


def test_my_own_duplicate_does_not_name_me(monkeypatch):
    assert _check([_tx(320, "2026-09-30", by=_ME)], monkeypatch)["duplicate"]["by"] is None


def test_different_amount_category_or_type_is_not_a_duplicate(monkeypatch):
    assert _check([_tx(321, "2026-09-30")], monkeypatch)["duplicate"] is None
    assert _check([_tx(320, "2026-09-30", cat="fuel")], monkeypatch)["duplicate"] is None
    assert _check([_tx(320, "2026-09-30", type_="savings")], monkeypatch)["duplicate"] is None


# ── 7. סכום חריג ──────────────────────────────────────────────────────

def _history(*amounts):
    return [_tx(a, f"2026-0{4 + i % 5}-1{i}", desc="קנייה") for i, a in enumerate(amounts)]


def test_five_times_the_biggest_is_unusual(monkeypatch):
    r = _check(_history(60, 120, 200, 450, 300), monkeypatch, amount=3200)

    assert r["unusual"] == {"min": 60, "max": 450}
    assert r["duplicate"] is None


def test_just_under_five_times_is_fine(monkeypatch):
    assert _check(_history(60, 120, 200, 450, 300), monkeypatch, amount=2249)["unusual"] is None


def test_needs_five_earlier_transactions(monkeypatch):
    assert _check(_history(60, 120, 200, 450), monkeypatch, amount=9000)["unusual"] is None


def test_older_than_six_months_does_not_count(monkeypatch):
    old = [_tx(a, "2026-02-10") for a in (10, 10, 10, 10, 10)]
    assert _check(old, monkeypatch, amount=9000)["unusual"] is None


def test_income_is_never_unusual(monkeypatch):
    rows = [_tx(a, f"2026-0{4 + i}-01", type_="income", cat="bonus") for i, a in enumerate((100,) * 5)]
    assert _check(rows, monkeypatch, amount=50000, type="income", category_id="bonus")["unusual"] is None


# ── המסלול ─────────────────────────────────────────────────────────────

def test_the_route(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module.clock, "today", lambda: _TODAY)
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=[_tx(320, "2026-09-30", by=_HER)]))
    monkeypatch.setattr(db, "get_family_members", lambda fid: [{"id": _HER, "name": "אור"}])
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        res = c.post("/api/transactions/precheck", json={"amount": 320, "type": "expense",
                                                         "category_id": "food", "date": "2026-09-30"})
        bad = c.post("/api/transactions/precheck", json={"amount": "x", "type": "expense"})
    assert res.status_code == 200 and res.get_json()["duplicate"]["by"] == "אור"
    # קלט שבור לא מפיל את השמירה — פשוט אין מה להזהיר
    assert bad.status_code == 200 and bad.get_json() == {"duplicate": None, "unusual": None}
