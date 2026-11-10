"""Every family member's settings always have a valid invite code, without
tapping "new code".

Matan's request (29.9.2026). A code is valid for a week, and the family's code
expired silently that morning: whoever came into settings to send a code found
"the code expired. Generate a new one".

A new code is created only when the previous one **has already expired**, so no
invitation you sent that is still valid gets cancelled. Also for someone who
isn't the manager (Matan's request): the renewal goes through a function that
can't cancel a valid code, so the manager's permission to replace a code ("new
code") isn't weakened.
"""
from datetime import timedelta

import pytest

from backend import app as app_module
from backend import clock
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_ME    = "11111111-1111-1111-1111-111111111111"
_OTHER = "33333333-3333-3333-3333-333333333333"
_FAM   = "22222222-2222-2222-2222-222222222222"


def _stamp(days):
    return (clock.now_utc() + timedelta(days=days)).isoformat()


@pytest.fixture
def settings_page(monkeypatch):
    app.config["TESTING"] = True
    state = {"family": None, "rotations": 0, "rotate_result": ("NEW777", None)}

    def rotate():
        state["rotations"] += 1
        code, err = state["rotate_result"]
        if code:
            state["family"] = {**state["family"], "invite_code": code,
                               "invite_code_expires_at": _stamp(7)}
        return code, err

    monkeypatch.setattr(app_module.db, "get_family", lambda fid: dict(state["family"]))
    monkeypatch.setattr(app_module.db, "renew_expired_invite_code", rotate)
    monkeypatch.setattr(app_module.db, "rotate_invite_code",
                        lambda: pytest.fail("the manager's replacement, not renewing an expired code"))
    monkeypatch.setattr(app_module.db, "get_family_members",
                        lambda fid: [{"id": _ME, "name": "מתן", "full_name": "מתן"}])
    for fn, val in (("get_categories", []), ("get_recurring_transactions", []),
                    ("get_projects", []), ("family_transaction_count", 0)):
        monkeypatch.setattr(app_module.db, fn, lambda *a, _v=val, **k: _v)
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: dict(db.DEFAULT_FAMILY_SETTINGS))

    def open_as(manager, code="OLD111", expires_in_days=-1):
        state["family"] = {"id": _FAM, "name": "שניאור", "manager_id": manager,
                           "invite_code": code, "invite_code_expires_at": _stamp(expires_in_days)}
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            return c.get("/settings").get_data(as_text=True)
    return state, open_as


def test_the_manager_gets_a_fresh_code_instead_of_an_expired_one(settings_page):
    state, open_as = settings_page

    html = open_as(manager=_ME, expires_in_days=-1)

    assert state["rotations"] == 1
    assert "NEW777" in html and "OLD111" not in html
    assert "הקוד פג" not in html


def test_a_code_that_is_still_valid_is_left_alone(settings_page):
    """Otherwise every visit to settings would cancel an invitation you sent yesterday."""
    state, open_as = settings_page

    html = open_as(manager=_ME, expires_in_days=3)

    assert state["rotations"] == 0
    assert "OLD111" in html


def test_a_member_who_is_not_the_manager_gets_a_fresh_code_too(settings_page):
    state, open_as = settings_page

    html = open_as(manager=_OTHER, expires_in_days=-1)

    assert state["rotations"] == 1
    assert "NEW777" in html and "הקוד פג" not in html


def test_a_member_cannot_replace_a_code_that_is_still_valid(settings_page):
    """This is the manager's permission ("new code"), and it doesn't go through the renewal."""
    state, open_as = settings_page

    open_as(manager=_OTHER, expires_in_days=3)

    assert state["rotations"] == 0


def test_a_failed_renewal_still_shows_the_page_and_says_it_expired(settings_page):
    state, open_as = settings_page
    state["rotate_result"] = (None, "db down")

    html = open_as(manager=_ME, expires_in_days=-1)

    assert state["rotations"] == 1
    assert "הקוד פג" in html
