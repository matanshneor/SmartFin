"""
Tests for the seven functions that returned a false value when the query failed.

The pattern: except → return 0 / [] / False / {}. The caller couldn't tell "no
data" from "I couldn't check", and the result on screen was a confident, wrong
answer. A bug visible on screen gets reported; this one doesn't: the user looks at
₪0 and believes that is what there is.

The rule tested: a fetch failure is raised, not invented. Where a measured response
is preferable to an error page, the direction is always to be stricter, not more
lenient.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit


class _BrokenClient:
    """Every query fails, as in a transient network glitch."""
    def __getattr__(self, _n):
        def boom(*a, **k): raise RuntimeError("Supabase did not answer")
        return boom


@pytest.fixture
def broken(monkeypatch):
    monkeypatch.setattr(db, "get_client", lambda: _BrokenClient())


# ─── All seven functions ─────────────────────────────────────────────────────

@pytest.mark.parametrize("call,what_it_used_to_return", [
    (lambda: db.get_monthly_summary("f", 2026, 9),  "₪0 לכל התקציב"),
    (lambda: db._fetch_categories("f"),             "רשימה ריקה → 'משפחה חדשה'"),
    (lambda: db.family_transaction_count("f"),      "0 → דילוג על אישור נטישה"),
    (lambda: db._fetch_family("f"),                 "{} → ברירות שיוך שגויות"),
    (lambda: db.family_has_no_transactions("f"),    "False"),
    (lambda: db.family_needs_onboarding("f"),       "False"),
    (lambda: db.receipt_scans_this_month("f"),      "0 → ההגבלה על OpenAI כבויה"),
])
def test_a_failed_query_raises_instead_of_inventing_an_answer(
        broken, call, what_it_used_to_return):
    with pytest.raises(db.DataUnavailable):
        call()


# ─── What the user sees ──────────────────────────────────────────────────────

def test_the_dashboard_does_not_show_a_zeroed_budget(broken):
    """The August incident. Better an error page, which can be refreshed, than a zeroed
    budget that looks real."""
    app.config["TESTING"] = False
    try:
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"]   = "11111111-1111-1111-1111-111111111111"
                sess["family_id"] = "22222222-2222-2222-2222-222222222222"
            body = c.get("/").get_data(as_text=True)

        assert "₪0" not in body, "the dashboard still shows a zeroed budget on a fault"
        assert "שגיאה" in body or "נסו שוב" in body
    finally:
        app.config["TESTING"] = True


# ─── On failure: stricter, not more lenient ──────────────────────────────────

def test_a_failed_quota_check_refuses_the_scan(broken, monkeypatch):
    """Allowing a scan when the quota wasn't checked effectively cancels the spending
    limit."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "11111111-1111-1111-1111-111111111111"
            sess["family_id"] = "22222222-2222-2222-2222-222222222222"

        response = c.post("/api/receipts/scan")

    assert response.status_code == 503, "the scan was allowed although the quota wasn't checked"


def test_a_failed_count_still_asks_before_abandoning_a_family(monkeypatch):
    """Skipping the confirmation because of a fault is exactly the loss the
    confirmation prevents."""
    monkeypatch.setattr(app_module.db, "family_transaction_count",
                        lambda fid: (_ for _ in ()).throw(db.DataUnavailable("x")))
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "11111111-1111-1111-1111-111111111111"
            sess["family_id"] = "22222222-2222-2222-2222-222222222222"

        response = c.post("/api/family/join", json={"code": "ABC123"})

    assert response.status_code == 409
    assert response.get_json()["needs_confirm"] is True


def test_the_confirmation_does_not_invent_a_number(monkeypatch):
    """A message saying 'there are null transactions' is worse than a message with no
    number."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent
          / "frontend/static/js/settings.js").read_text(encoding="utf-8")

    assert "typeof n === 'number'" in js
    assert "יש עסקאות קיימות" in js


def test_the_error_page_itself_survives_a_dead_database(broken):
    """A trap uncovered along the way: the context processor feeding settings to every
    template also runs on error.html. Without a guard there, a database fault also
    brought down the error page meant to report it, and the user got a completely
    blank screen instead of a message."""
    app.config["TESTING"] = False
    try:
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"]   = "11111111-1111-1111-1111-111111111111"
                sess["family_id"] = "22222222-2222-2222-2222-222222222222"
            response = c.get("/")

        # 503 when the failure surfaces as DataUnavailable, 500 when it is a raw
        # exception; both render error.html, which is what this test guards. It
        # used to demand 500 only, and passed only when an earlier test in the run
        # left state behind that turned the failure into a raw exception.
        assert response.status_code in (500, 503)
        assert "error-code" in response.get_data(as_text=True) or \
               "שגיאה" in response.get_data(as_text=True)
    finally:
        app.config["TESTING"] = True
