"""
Tests for a transient failure fetching the profile during sign-in.

This was the most destructive bug the review found, and it all stems from one
distinction that wasn't made: get_profile returned None both for "no profile" and
for "the server didn't answer".

The chain: a two-second network glitch during sign-in → get_profile returns None
→ the route infers there is no family_id → ensure_family infers there is no
family → it creates a new family **and overwrites the existing link**. The user
lands on an empty dashboard, and all their transactions stay behind an old
family_id with no way back from the UI. The partner stays in the old family, and
from that moment the two enter into two separate budgets without knowing.

The rule tested here: never infer "none" from "I couldn't check".
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit


class _Session:
    access_token, refresh_token, expires_at = "a", "r", 9999999999


class _User:
    id, email = "00000000-0000-0000-0000-000000000000", "dana@example.com"


class _SignIn:
    session, user = _Session(), _User()


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    limiter.reset()
    monkeypatch.setattr(app_module.db, "sign_in",        lambda e, p: (_SignIn(), None))
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(app_module.db, "log_login_event", lambda: None)
    yield app.test_client()
    limiter.reset()


def _login(client):
    return client.post("/login", data={"identifier": "dana@example.com", "password": "x"})


# ─── The distinction itself ──────────────────────────────────────────────────

def test_a_failed_read_is_not_reported_as_a_missing_profile(monkeypatch):
    """The heart. Without this distinction none of the other tests here can work."""
    class _Boom:
        def table(self, *a, **k): raise RuntimeError("Supabase לא ענה")

    monkeypatch.setattr(db, "get_client", lambda: _Boom())
    profile, ok = db.fetch_profile("some-user")

    assert profile is None
    assert ok is False, "a fetch failure was reported as 'no profile'"


def test_a_genuinely_missing_profile_is_reported_as_an_answer(monkeypatch):
    """Control: 'no row' is a valid answer and not a fault; otherwise a brand-new
    user would never get a family."""
    from postgrest.exceptions import APIError

    class _Empty:
        def table(self, *a, **k): return self
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def single(self, *a, **k): return self
        def execute(self):
            raise APIError({"code": "PGRST116", "details": "The result contains 0 rows",
                            "hint": None, "message": "..."})

    monkeypatch.setattr(db, "get_client", lambda: _Empty())
    profile, ok = db.fetch_profile("some-user")

    assert profile is None
    assert ok is True, "a user with no profile was treated as a fault: they can't be created"


# ─── The dangerous consequence ───────────────────────────────────────────────

def test_a_failed_read_never_creates_a_family(monkeypatch):
    """This is the line that destroyed the data: creating a family based on a failure
    overwrites the existing link and cuts the user off from all their history."""
    created = []

    class _Recorder:
        def table(self, name):
            created.append(name)
            raise AssertionError(f"נוצרה כתיבה ל-{name} למרות שהשליפה נכשלה")

        def rpc(self, name, params):
            created.append(name)
            raise AssertionError(f"נקראה {name} למרות שהשליפה נכשלה")

    monkeypatch.setattr(db, "get_client", lambda: _Recorder())
    monkeypatch.setattr(db, "fetch_profile", lambda uid: (None, False))

    assert db.ensure_family("some-user") is None
    assert created == []


def test_a_missing_profile_does_still_get_a_family(monkeypatch):
    """Control: the caution shouldn't break a real new user."""
    calls = []

    class _Fake:
        # creation goes through an RPC and not a direct write: writing to
        # profiles.family_id is blocked at the grants level since migration 20260916100000
        def rpc(self, name, params): calls.append((name, params)); return self
        def execute(self): self.data = "44444444-4444-4444-4444-444444444444"; return self

    monkeypatch.setattr(db, "get_client", lambda: _Fake())
    monkeypatch.setattr(db, "fetch_profile", lambda uid: (None, True))

    assert db.ensure_family("some-user") == "44444444-4444-4444-4444-444444444444"
    assert calls and calls[0][0] == "create_own_family"


# ─── What the user experiences ───────────────────────────────────────────────

def test_login_is_refused_when_the_profile_cannot_be_read(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "fetch_profile", lambda uid: (None, False))

    response = _login(client)

    assert response.status_code != 302, "signed in although the profile wasn't read"
    assert "נסו שוב" in response.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert not sess.get("user_id"), "a session was opened without a profile"


def test_login_is_refused_when_the_family_cannot_be_created(client, monkeypatch):
    """A session without a family is an app where nothing can be done."""
    monkeypatch.setattr(app_module.db, "fetch_profile", lambda uid: (None, True))
    monkeypatch.setattr(app_module.db, "ensure_family", lambda uid: None)

    response = _login(client)

    assert response.status_code != 302
    with client.session_transaction() as sess:
        assert not sess.get("user_id")


def test_a_healthy_login_still_works(client, monkeypatch):
    """Final control: the caution doesn't block the healthy path."""
    monkeypatch.setattr(app_module.db, "fetch_profile",
                        lambda uid: ({"name": "דנה", "avatar_initial": "ד",
                                      "family_id": "11111111-1111-1111-1111-111111111111"}, True))

    response = _login(client)

    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["family_id"] == "11111111-1111-1111-1111-111111111111"
