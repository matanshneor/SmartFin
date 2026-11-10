"""
The only route that costs money, and the only one with no brake at all.

/api/receipts/scan calls OpenAI. The quota was 100 scans **per family**, and
sign-up is open: every new account gets a family, and with it a fresh quota. That
is, there was no ceiling on the total at all. And of the app's 23 writing routes,
this was the only one with no rate limit at all.

There is also a billing cap in the OpenAI dashboard, and it is the last safety
net. The ceiling here exists because it fails differently: in Hebrew, with an
offer to enter manually, instead of the key being blocked and an opaque API error
that looks to the user like a bug.
"""
import io
from datetime import datetime, timezone

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _fresh_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "get_categories", lambda *a, **k: [])
    monkeypatch.setattr(app_module.db, "record_receipt_scan", lambda *a, **k: None)
    monkeypatch.setattr(app_module.db, "upload_receipt", lambda *a, **k: (None, None))
    # the same keys the real function always guarantees (see scan_receipt)
    monkeypatch.setattr(app_module.db, "scan_receipt", lambda *a, **k: (
        {"amount": 12.5, "merchant": "סופר", "date": "2026-09-20",
         "category_name": None}, None))
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "22222222-2222-2222-2222-222222222222"
            sess["family_id"] = _FAM
        yield c


def _quota(monkeypatch, mine=0, everyone=0):
    monkeypatch.setattr(app_module.db, "receipt_scans_this_month", lambda *a: mine)
    monkeypatch.setattr(app_module.db, "receipt_scans_globally_this_month", lambda: everyone)


def _scan(client):
    return client.post("/api/receipts/scan", content_type="multipart/form-data",
                       data={"image": (io.BytesIO(b"\xff\xd8fake"), "r.jpg", "image/jpeg")})


# ─── The global ceiling ─────────────────────────────────────────────────────

def test_the_service_stops_when_everyone_together_hits_the_ceiling(client, monkeypatch):
    """The heart: this family used 0 of its 100, and the scan still stops."""
    _quota(monkeypatch, mine=0, everyone=app_module.db.RECEIPT_GLOBAL_MONTHLY_LIMIT)

    res = _scan(client)

    assert res.status_code == 503
    assert "ניתן להזין את הפרטים ידנית" in res.get_json()["error"]


def test_the_message_does_not_blame_the_person_standing_there(client, monkeypatch):
    """They didn't use up any quota. A message about "your quota" would simply be a
    lie."""
    _quota(monkeypatch, mine=0, everyone=99999)

    error = _scan(client).get_json()["error"]

    assert "מכסת הסריקות החודשית" not in error
    assert "הגעתם" not in error


def test_one_below_the_ceiling_still_works(client, monkeypatch):
    """Control: an off-by-one error here disables the feature for a whole day."""
    _quota(monkeypatch, mine=0, everyone=app_module.db.RECEIPT_GLOBAL_MONTHLY_LIMIT - 1)

    assert _scan(client).status_code == 200


def test_the_family_quota_still_applies_on_its_own(client, monkeypatch):
    _quota(monkeypatch, mine=app_module.db.RECEIPT_MONTHLY_LIMIT, everyone=1)

    res = _scan(client)

    assert res.status_code == 429
    assert "מכסת הסריקות החודשית" in res.get_json()["error"]


def test_a_failed_count_refuses_rather_than_guessing(client, monkeypatch):
    """Allowing when the check failed effectively cancels the spending limit."""
    def _boom():
        raise app_module.db.DataUnavailable("global")
    monkeypatch.setattr(app_module.db, "receipt_scans_this_month", lambda *a: 0)
    monkeypatch.setattr(app_module.db, "receipt_scans_globally_this_month", _boom)

    assert _scan(client).status_code == 503


def test_the_ceiling_can_be_raised_without_a_deploy(monkeypatch):
    """A number that needs a deploy to change is a number that will stay wrong exactly
    when it needs changing."""
    import importlib
    monkeypatch.setenv("RECEIPT_GLOBAL_MONTHLY_LIMIT", "12345")
    db = importlib.reload(app_module.db)
    try:
        assert db.RECEIPT_GLOBAL_MONTHLY_LIMIT == 12345
    finally:
        monkeypatch.delenv("RECEIPT_GLOBAL_MONTHLY_LIMIT")
        importlib.reload(db)


# ─── Rate limit ─────────────────────────────────────────────────────────────

def test_the_expensive_route_is_rate_limited(client, monkeypatch):
    """23 writing routes, and this was the only one without a limit: the expensive one
    of all."""
    _quota(monkeypatch, mine=0, everyone=0)

    codes = [_scan(client).status_code for _ in range(12)]

    assert 429 in codes, "no rate limit on receipt scanning"


def test_a_normal_person_never_meets_the_rate_limit(client, monkeypatch):
    """One scan takes 2-6 seconds. Whoever scans three receipts in a row is a person,
    not a script, and shouldn't be blocked."""
    _quota(monkeypatch, mine=0, everyone=0)

    assert [_scan(client).status_code for _ in range(3)] == [200, 200, 200]


# ─── Both counts reset together ─────────────────────────────────────────────

def test_both_counts_share_one_month_boundary():
    """The server runs in UTC and the month is measured in Israel time. Two different
    boundaries would reset the quotas at different hours."""
    from backend import supabase_config as db
    import inspect

    family = inspect.getsource(db.receipt_scans_this_month)
    glob   = inspect.getsource(db.receipt_scans_globally_this_month)

    assert "_month_start()" in family
    assert "_month_start()" in glob


# ─── The start of the month in Israel time, not UTC ─────────────────────────
#
# "2026-10-01" was sent as a date string, and the database (UTC) interpreted it as
# UTC midnight: 03:00 in Israel in summer. A scan on the 1st of the month at 01:30
# was counted to the previous month.

@pytest.mark.parametrize("israel_now,start,utc", [
    (datetime(2026, 10, 1, 1, 30), "2026-10-01T00:00:00+03:00", "2026-09-30T21:00:00+00:00"),
    (datetime(2026, 12, 1, 0, 30), "2026-12-01T00:00:00+02:00", "2026-11-30T22:00:00+00:00"),
], ids=["summer", "winter"])
def test_the_month_starts_at_midnight_in_israel(monkeypatch, israel_now, start, utc):
    monkeypatch.setattr(db.clock, "now", lambda: israel_now)

    boundary = db._month_start()

    assert boundary == start
    assert datetime.fromisoformat(boundary).astimezone(timezone.utc).isoformat() == utc
