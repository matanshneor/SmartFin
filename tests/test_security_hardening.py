"""
Tests for three hardenings the review found.

16: sensitive routes with no rate limit. Three of them verify a password against
    Supabase, that is, an oracle for guessing passwords for whoever obtained a
    session; the limit on /login doesn't help there, because these are other
    routes. The fourth, completing a password reset, isn't authenticated at all.

17: transaction fields were accepted from the client without validation. RLS
    checks only the family_id on the written row and not user_id, so an expense
    could be hung on the partner. And a personal project was protected for reading
    only: writing filtered by family only, so a family member could delete a
    project they aren't even allowed to see.

18: /logout was GET, that is, triggerable from another site; amounts with no upper
    bound hit the column limit with a generic message leading to a dead end.
"""
import inspect
from pathlib import Path

import pytest

from backend import app as app_module
from backend.app import _parse_amount

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent


# ─── 16: rate limit ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("endpoint,why", [
    ("update_password",       "אורקל לניחוש הסיסמה הנוכחית"),
    ("reset_account_route",   "אורקל סיסמה + מחיקת כל עסקאות המשפחה"),
    ("delete_account_route",  "אורקל סיסמה + מחיקת חשבון לצמיתות"),
    ("reset_password_submit", "לא מאומת, ומקבל טוקן שחזור מגוף הבקשה"),
])
def test_the_sensitive_routes_are_rate_limited(endpoint, why):
    src = inspect.getsource(getattr(app_module, endpoint))

    assert "@limiter.limit" in src, f"{endpoint} has no limit: {why}"


def test_the_destructive_ones_are_limited_harder_than_the_rest():
    """There is no legitimate scenario of deleting an account or resetting data again
    and again."""
    for endpoint in ("reset_account_route", "delete_account_route"):
        assert "per hour" in inspect.getsource(getattr(app_module, endpoint))


# ─── 17: field validation ───────────────────────────────────────────────────

def _user():
    return {"id": "11111111-1111-1111-1111-111111111111",
            "family_id": "22222222-2222-2222-2222-222222222222"}


@pytest.fixture
def attribution_on(monkeypatch):
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: {"owner_attribution": {"expense": True}})


def test_an_owner_outside_the_family_is_refused(attribution_on, monkeypatch):
    """Not an information leak but forged attribution, and the 'by family member'
    breakdown relies on it."""
    monkeypatch.setattr(app_module.db, "get_family_members",
                        lambda fid: [{"id": _user()["id"]}])

    owner, err = app_module._resolve_owner(
        {"owner": "99999999-9999-9999-9999-999999999999"}, _user(), "expense")

    assert owner is None
    assert "אינו במשפחה" in err


def test_a_real_family_member_is_still_accepted(attribution_on, monkeypatch):
    """Control: legitimate attribution must keep working."""
    mate = "33333333-3333-3333-3333-333333333333"
    monkeypatch.setattr(app_module.db, "get_family_members",
                        lambda fid: [{"id": _user()["id"]}, {"id": mate}])

    owner, err = app_module._resolve_owner({"owner": mate}, _user(), "expense")

    assert (owner, err) == (mate, None)


def test_a_category_of_the_wrong_type_is_refused(monkeypatch):
    monkeypatch.setattr(app_module.db, "get_categories",
                        lambda fid: [{"id": "cat-1", "type": "income"}])

    _, err = app_module._validated_category("cat-1", _user(), "expense")

    assert "אינה שייכת למחלקה" in err


def test_every_project_route_goes_through_the_access_gate():
    """Eight routes filtered by family only. The rule is enforced in one gate and not
    in eight copies, because the eighth is the one that gets forgotten."""
    src = (_ROOT / "backend/app.py").read_text(encoding="utf-8")

    for fn in ("update_project_route", "delete_project_route",
               "share_project_route", "unshare_project_route",
               "list_project_categories_route", "add_project_category_route",
               "update_project_category_route", "delete_project_category_route"):
        block = src[src.index(f"def {fn}(") - 120:src.index(f"def {fn}(")]
        assert "@project_access_required" in block, f"{fn} has no access gate"


def test_a_personal_project_answers_not_found_rather_than_forbidden():
    """A personal project is private: the response mustn't confirm it exists."""
    src = inspect.getsource(app_module.project_access_required)

    assert "404" in src
    assert "403" not in src


# ─── 18: bounds and amounts ─────────────────────────────────────────────────

@pytest.mark.parametrize("raw,reason", [
    (999_999_999, "גדול מדי"),
    (0.004,       "קטן מדי"),
    (0,           "חיובי"),
    (-5,          "חיובי"),
    ("abc",       "מספר"),
    (float("inf"), "חיובי"),
])
def test_bad_amounts_are_explained_not_swallowed(raw, reason):
    """Without these checks Postgres fails and the user gets 'try again', so they try
    the same input again, and fail again, with no hint why."""
    value, err = _parse_amount(raw)

    assert value is None
    assert reason in err


@pytest.mark.parametrize("raw", [1, 0.01, 12.5, "1250.75", 99_999_999])
def test_normal_amounts_still_pass(raw):
    value, err = _parse_amount(raw)
    assert err is None and value > 0


def test_logging_out_is_not_something_another_site_can_trigger():
    """GET /logout can be triggered with <img src="...">. SameSite=Lax allows exactly
    this form of navigation."""
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")

    assert 'method="POST" action="{{ url_for(\'logout\') }}"' in html, \
        "the sign-out button is still a GET link"
