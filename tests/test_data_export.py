"""The right to take your data and leave wasn't actually provided.

The only export was `/month.csv`: a single month, available only from the month
page, without the profile, categories or projects. Whoever wanted their data had
to export month by month by hand and assemble it themselves, and that isn't
portability, it is a chore.

The privacy policy promises a right of access, and `/month.csv` exists exactly
for "what happens if I want to leave". It just didn't answer that.
"""
import json
from pathlib import Path

import pytest

from backend import supabase_config as db
from backend.app import app, limiter
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_FAM = "11111111-1111-1111-1111-111111111111"
_ME = "22222222-2222-2222-2222-222222222222"
_MATE = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def exporting(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True

    fake = FakeSupabase(transactions=[
        {"id": "t1", "family_id": _FAM, "amount": 100.0, "type": "expense",
         "date": "2026-09-01", "project_id": None},
        {"id": "t2", "family_id": _FAM, "amount": 200.0, "type": "expense",
         "date": "2026-08-01", "project_id": "mine"},
        {"id": "t3", "family_id": _FAM, "amount": 300.0, "type": "expense",
         "date": "2026-07-01", "project_id": "theirs"},
    ])
    monkeypatch.setattr(db, "get_client", lambda: fake)
    monkeypatch.setattr(db, "get_profile", lambda uid: {"id": uid, "name": "מתן"})
    monkeypatch.setattr(db, "get_family",
                        lambda fid: {"id": fid, "name": "שניאור", "invite_code": "K4F2QX"})
    monkeypatch.setattr(db, "get_family_members",
                        lambda fid: [{"id": _ME, "name": "מתן"}, {"id": _MATE, "name": "אור"}])
    monkeypatch.setattr(db, "get_categories",
                        lambda fid: [{"id": "c1", "name": "מכולת", "type": "expense"}])
    # "mine" is shared/mine, "theirs" is another family member's personal one:
    # it shouldn't be included
    monkeypatch.setattr(db, "get_projects",
                        lambda fid, viewer: [{"id": "mine", "name": "שיפוץ"}])

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield c, fake


# ─── What is in the file ────────────────────────────────────────────────────

def test_the_export_carries_everything_and_not_one_month(exporting):
    """The heart: profile, family, members, categories, projects and every transaction."""
    c, _ = exporting

    res = c.get("/account.json")

    assert res.status_code == 200
    data = json.loads(res.get_data(as_text=True))
    for key in ("profile", "family", "members", "categories",
                "projects", "transactions", "exported_at"):
        assert key in data, f"{key} is missing from the export"


def test_it_downloads_as_a_file_and_does_not_open_in_the_tab(exporting):
    c, _ = exporting

    res = c.get("/account.json")

    assert "attachment" in res.headers.get("Content-Disposition", "")
    assert "smartfin-" in res.headers["Content-Disposition"]


def test_hebrew_survives_the_round_trip(exporting):
    """`ensure_ascii` would turn every category name into a `\\uXXXX` sequence: a
    valid file that can't be read."""
    c, _ = exporting

    body = c.get("/account.json").get_data(as_text=True)

    assert "שניאור" in body
    assert "\\u05e9" not in body


# ─── And what isn't ─────────────────────────────────────────────────────────

def test_a_personal_project_of_another_member_is_not_exported(exporting):
    """An export doesn't bypass privacy within the family. Another family member's
    personal project is hidden everywhere else in the app, and the export file is
    exactly the place where it is easy to forget that."""
    c, _ = exporting

    data = json.loads(c.get("/account.json").get_data(as_text=True))
    ids = {t["id"] for t in data["transactions"]}

    assert "t3" not in ids, "a transaction from someone else's personal project was included in the export"
    assert {"t1", "t2"} <= ids, "legitimate transactions were blocked"


def test_it_needs_a_session():
    limiter.reset()
    app.config["TESTING"] = True
    with app.test_client() as c:
        res = c.get("/account.json")

    assert res.status_code in (302, 401)


def test_a_failed_read_is_not_exported_as_an_empty_account():
    """An empty export looks exactly like an empty account, and whoever downloads
    it before deleting their account gets a file with nothing in it and doesn't
    know.

    Checked on the source and not through a request: `app.config["TESTING"]`
    leaks between tests, and with the flag on Flask raises the exception
    instead of running the handler, so the test would be measuring the flag."""
    src = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    fn = src[src.index("def export_account():"):]
    fn = fn[:fn.index("\n@app.route")]

    assert "db.export_account_data" in fn
    assert "except" not in fn, \
        "the route catches the failure: the global handler (503) won't run, and the user will get a file"


def test_the_reader_itself_raises_instead_of_returning_an_empty_dict():
    src = (_ROOT / "backend/supabase_config.py").read_text(encoding="utf-8")
    fn = src[src.index("def export_account_data("):]
    fn = fn[:fn.index("\ndef ")]

    assert "raise DataUnavailable" in fn
    assert "return {}" not in fn


# ─── And that the screen offers it ──────────────────────────────────────────

def test_settings_no_longer_offers_the_export():
    """Matan (1.10): no "export my data" in settings. The route itself stays:
    data access requests arrive by email (privacy policy) and are answered from it."""
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")

    assert "url_for('export_account')" not in html
    assert "ייצוא הנתונים שלי</p>" not in html


def test_the_export_is_rate_limited():
    """A query that pulls the family's entire history."""
    src = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    after = src[src.index('@app.route("/account.json")'):]

    assert "@limiter.limit" in after[:after.index("\ndef ")]


# ─── And the policy tells the truth ─────────────────────────────────────────
#
# Two claims were wrong. "Deleting the account removes the profile and the data
# associated with it" and "data is kept as long as the account is active", while
# `owner_archive` kept everything forever, with no code cleaning it. And
# separately: receipt photos are sent to OpenAI, i.e. leave the EU, and the
# policy listed only European providers.

_PRIVACY = (_ROOT / "frontend/templates/privacy.html").read_text(encoding="utf-8")


def test_the_policy_states_the_archive_retention_window():
    """A deletion promise that isn't kept is worse than no promise."""
    assert "30 יום" in _PRIVACY, "the archive's retention window isn't declared"
    assert "90 יום" in _PRIVACY, "the sign-in log's retention isn't declared"


def test_the_stated_window_matches_what_the_database_actually_does():
    """Two numbers that can change separately are a promise that will silently go stale."""
    sql = (_ROOT / "backend/supabase/migrations"
           / "20260922160000_archive_retention.sql").read_text(encoding="utf-8")

    assert "interval '30 days'" in sql
    assert "interval '90 days'" in sql


def test_the_policy_says_receipt_images_leave_the_eu():
    """The policy lists Supabase and Railway as European and mentions OpenAI,
    without saying the image leaves the EU. That is an international transfer
    (44-49)."""
    assert "ארצות הברית" in _PRIVACY


def test_the_policy_names_who_is_responsible():
    """Article 13(1)(a): a Gmail address alone isn't a controller's identity."""
    assert "האחראי על המידע" in _PRIVACY
