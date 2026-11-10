import os
import sys

import pytest
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from backend import supabase_config as db

import _test_accounts


@pytest.fixture(scope="session")
def _test_pair():
    """Two temporary test accounts for the whole run: created the first time a test
    needs them, and deleted at the end with everything they left (see
    `tests/_test_accounts.py`). Until 4.10 these were two permanent users in the
    production database."""
    if not os.environ.get("SUPABASE_URL"):
        pytest.skip("No SUPABASE_URL: the tests against the real database run locally only")
    pair = _test_accounts.create_pair("pytest")
    yield pair
    _test_accounts.purge([a["user_id"] for a in pair.values()])


def _signed_in(account):
    db.set_auth_token(account["token"])
    return {k: account[k] for k in ("user_id", "family_id", "token")}


@pytest.fixture(scope="session")
def family_a(_test_pair):
    return _signed_in(_test_pair["a"])


@pytest.fixture(scope="session")
def family_b(_test_pair):
    return _signed_in(_test_pair["b"])


def category_of(family_id: str, type_: str = "expense") -> str:
    """An existing category of the family of this type, and if there is none,
    creates a permanent one.

    A household transaction without a category is rejected by the database
    (`transactions_category_required`), so every test inserting a transaction
    directly needs one. The active token must belong to this family's member."""
    table = db.get_client().table("categories")
    found = table.select("id").eq("family_id", family_id).eq("type", type_) \
        .limit(1).execute().data
    if found:
        return found[0]["id"]
    return table.insert({"family_id": family_id, "name": f"TEST-{type_}", "icon": "🧪",
                         "type": type_, "is_custom": True}).execute().data[0]["id"]


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "unit: בדיקה טהורה שלא נוגעת ב-Supabase — לא מתחברת ולא דורשת משתמשי בדיקה",
    )


@pytest.fixture(autouse=True)
def _unit_tests_cannot_reach_supabase(request, monkeypatch):
    """Enforces what the `unit` marker promised, and didn't enforce.

    The marker documented "doesn't touch Supabase", but nothing prevented it. A
    test that relied on server validation to avoid reaching `db.sign_up` simply
    reached it the moment the validation was removed (for example in a
    mutation test) **and created a real account in the production database**.
    It happened, on September 20th 2026, with `israel@gmail`.

    The failure was also completely silent: the test passed. So from here
    `get_client` refuses to work in unit tests, and every network touch fails
    loudly with the explanation."""
    if "unit" not in request.keywords:
        yield
        return

    def _refuse():
        raise AssertionError(
            "בדיקת יחידה ניסתה לפנות ל-Supabase האמיתי. או שחסר "
            "monkeypatch על הפונקציה שנקראה, או שהבדיקה הזאת אינה unit."
        )

    _real_home_page = db.fetch_home_page
    _real_recap = db.month_recap
    _real_settings_page = db.fetch_settings_page
    monkeypatch.setattr(db, "get_client", _refuse)
    # The latest email from Supabase (`_current_email` in app). Default in
    # tests: "can't know", and the app falls back to the session's email, as
    # before 1.10. A test that needs another email replaces this itself.
    monkeypatch.setattr(db, "get_auth_email", lambda token: (None, None))
    # The home page in one trip (`get_home_page`, Matan 13.10). Default:
    # "unavailable", so the existing home page tests (which replace each query
    # separately) pass through the fallback. The main path is tested in
    # `test_home_page_rpc.py`.
    def _no_home_page(*a, **k):
        raise db.DataUnavailable("fetch_home_page: unit default")
    monkeypatch.setattr(db, "fetch_home_page", _no_home_page)
    monkeypatch.setattr(db, "_real_fetch_home_page", _real_home_page, raising=False)
    # And the same for the settings page (`get_settings_page`):
    # `test_settings_page_rpc.py`
    def _no_settings_page(*a, **k):
        raise db.DataUnavailable("fetch_settings_page: unit default")
    monkeypatch.setattr(db, "fetch_settings_page", _no_settings_page)
    monkeypatch.setattr(db, "_real_fetch_settings_page", _real_settings_page, raising=False)
    # "September closed" only on the first day of the month: without this every
    # home page test would fail when run on the 1st. `test_month_recap.py` tests
    # the real one.
    monkeypatch.setattr(db, "month_recap", lambda *a, **k: None)
    # Projects and comparison in one trip: default "unavailable", like home and
    # settings. `test_projects_months_rpc.py` tests the real ones.
    for _name in ("fetch_projects_page", "fetch_months_page"):
        monkeypatch.setattr(db, "_real_" + _name, getattr(db, _name), raising=False)
        def _unavailable(*a, _n=_name, **k):
            raise db.DataUnavailable(_n + ": unit default")
        monkeypatch.setattr(db, _name, _unavailable)
    monkeypatch.setattr(db, "_real_month_recap", _real_recap, raising=False)
    # The monthly average per category in settings (idea 38): default: none
    _real_averages = db.category_monthly_averages
    monkeypatch.setattr(db, "category_monthly_averages", lambda *a, **k: {})
    monkeypatch.setattr(db, "_real_category_monthly_averages", _real_averages, raising=False)
    # "₪5,000 ← ₪5,500 מאוקטובר" in settings (idea 40): default: no changes
    monkeypatch.setattr(db, "recurring_price_changes", lambda *a, **k: {})
    # And direct network too. `send_reset_email`, `upload_receipt` and
    # `delete_receipts` call Supabase with httpx and not through `get_client`, and
    # an audit test on "forgot password" sent a real reset request that way
    # (30.9.2026, to an unregistered address, so no email was sent). In CI there
    # is no `SUPABASE_URL` and the test failed; locally it passed.
    import httpx
    for verb in ("request", "get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(httpx, verb, lambda *a, **k: _refuse())
    # The leaked-password check (`backend/passwords.py`) calls an external
    # service. Default: no password leaked. `test_passwords.py` replaces this.
    from backend import passwords
    monkeypatch.setattr(passwords, "_fetch_range", lambda prefix: "")
    yield


@pytest.fixture(autouse=True)
def _start_authenticated_as_family_a(request):
    """Every test starts with the client authenticated as family A by default;
    tests that need to switch context (e.g. to check cross-family access) do so
    themselves.

    Tests marked @pytest.mark.unit skip the sign-in: they test pure logic, and a
    needless sign-in both slows them down and burns into the quota of 10
    sign-ins a minute."""
    if "unit" in request.keywords:
        yield
        return
    family_a = request.getfixturevalue("family_a")
    db.set_auth_token(family_a["token"])
    yield
