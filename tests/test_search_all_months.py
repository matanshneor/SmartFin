"""Search across all months (Matan, 30.9: round 6, item 2).

A magnifying glass at the top of the home page. Searches in the description, the
category name, the workplace and the project name; a number also finds
transactions of that amount. Newest to oldest, up to 100, with how many were found
and the total. Transactions in someone else's personal project don't exist.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_HER = "33333333-3333-3333-3333-333333333333"


def _tx(id_, date, amount, desc="", cat="רכב", type_="expense", project=None, owner=None, workplace=None):
    return {"id": id_, "family_id": _FAM, "date": date, "amount": amount, "type": type_,
            "description": desc, "workplace": workplace, "category_id": "c-" + cat,
            "project_id": project, "project_category_id": None, "user_id": None,
            "is_recurring": False, "recurring_parent_id": None, "recurring_frequency": None,
            "recurring_end_date": None, "receipt_path": None,
            "categories": {"name": cat, "icon": "🚗"}, "project_categories": None,
            "profiles": None,
            "projects": {"owner_id": owner, "name": "שיפוץ", "icon": "🔨"} if project else None}


def _search(rows, q, monkeypatch, **kw):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    return db.search_transactions(_FAM, _ME, q, **kw)


def test_finds_by_description_across_months_newest_first(monkeypatch):
    r = _search([_tx("a", "2025-03-12", 650, "ביטוח רכב הראל"), _tx("b", "2026-09-12", 690, "ביטוח רכב"),
                 _tx("c", "2026-08-01", 90, "דלק")], "ביטוח", monkeypatch)

    assert [t["id"] for t in r["results"]] == ["b", "a"]
    assert r["count"] == 2 and r["totals"]["expense"] == 1340 and not r["truncated"]


def test_finds_by_category_workplace_and_project_name(monkeypatch):
    rows = [_tx("cat", "2026-09-01", 300, "", cat="סופר"),
            _tx("wp", "2026-09-01", 8000, "", cat="משכורת", type_="income", workplace="אינטל"),
            _tx("pr", "2026-09-01", 4000, "", project="p1")]

    assert [t["id"] for t in _search(rows, "סופר", monkeypatch)["results"]] == ["cat"]
    assert [t["id"] for t in _search(rows, "אינטל", monkeypatch)["results"]] == ["wp"]
    assert [t["id"] for t in _search(rows, "שיפוץ", monkeypatch)["results"]] == ["pr"]


def test_a_number_finds_that_amount(monkeypatch):
    r = _search([_tx("a", "2026-09-01", 690, "ביטוח"), _tx("b", "2026-09-02", 1690, "משהו"),
                 _tx("c", "2026-09-03", 90, "קוד 690")], "690", monkeypatch)

    assert sorted(t["id"] for t in r["results"]) == ["a", "c"]   # 1690 isn't 690


def test_case_and_spaces_do_not_matter(monkeypatch):
    r = _search([_tx("a", "2026-09-01", 50, "Netflix")], "  netflix ", monkeypatch)

    assert [t["id"] for t in r["results"]] == ["a"]


def test_someone_elses_personal_project_does_not_exist(monkeypatch):
    r = _search([_tx("mine", "2026-09-01", 10, "מתנה", project="p1", owner=_ME),
                 _tx("hers", "2026-09-01", 10, "מתנה", project="p2", owner=_HER)], "מתנה", monkeypatch)

    assert [t["id"] for t in r["results"]] == ["mine"] and r["count"] == 1


def test_capped_but_counted(monkeypatch):
    rows = [_tx(f"t{i}", f"2026-0{1 + i % 9}-1{i % 9}", 10, "קפה") for i in range(7)]
    r = _search(rows, "קפה", monkeypatch, limit=5)

    assert len(r["results"]) == 5 and r["count"] == 7 and r["truncated"]
    assert r["totals"]["expense"] == 70         # the total of everything found, not only what's shown


def test_too_short_a_query_does_not_search(monkeypatch):
    def boom():
        raise AssertionError("a fetch on a single letter")
    monkeypatch.setattr(db, "get_client", boom)

    assert db.search_transactions(_FAM, _ME, "א")["results"] == []
    assert db.search_transactions(_FAM, _ME, "  ")["results"] == []


# ── The route ─────────────────────────────────────────────────────────

def test_the_route_answers_json(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(db, "get_client",
                        lambda: FakeSupabase(transactions=[_tx("a", "2026-09-12", 690, "ביטוח רכב")]))
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        res = c.get("/api/search?q=ביטוח")
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] == 1 and body["results"][0]["id"] == "a"
    assert body["results"][0]["category_name"] == "רכב"
