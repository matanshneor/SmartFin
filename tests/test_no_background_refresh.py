"""Signed out after a break (Matan, 13.10: "why does it put me back on the sign-in
screen?").

In the logs: "Invalid Refresh Token: Already Used" three times a day, each time
after a break of more than an hour. The cause: the Supabase library's default
schedules a background timer that refreshes the session of whoever signed in last
on the shared client, and so "consumes" the refresh token in the user's cookie,
without anyone knowing.
"""
import time

import pytest
from gotrue.types import Session, User

from backend import supabase_config as db

pytestmark = pytest.mark.unit


def _session():
    return Session(access_token="a", refresh_token="r", expires_in=3600,
                   expires_at=int(time.time()) + 3600, token_type="bearer",
                   user=User(id="u", app_metadata={}, user_metadata={}, aud="a",
                             created_at="2026-01-01T00:00:00Z"))


@pytest.fixture
def real_client(monkeypatch):
    """A real client of the library (no network: construction only), through our
    get_client."""
    import backend.supabase_config as mod
    from supabase import create_client
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.x")
    monkeypatch.setenv("SF_NO_WARM_PINGS", "1")
    monkeypatch.setattr(mod, "_client", None)
    # conftest blocks get_client in unit tests: build the way it builds, and check
    # that its code really asks for the same options
    client = _build(mod)
    yield mod, client
    if client.auth._refresh_token_timer:
        client.auth._refresh_token_timer.cancel()


def _build(mod):
    from supabase import create_client
    from supabase.lib.client_options import ClientOptions
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "backend/supabase_config.py").read_text(encoding="utf-8")
    body = src[src.index("def get_client():"):src.index("def _forget_auth_session")]
    assert "auto_refresh_token=False, persist_session=False" in body
    assert "_client.auth._auto_refresh_token = False" in body
    client = create_client("https://example.supabase.co", "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.x",
                           options=ClientOptions(auto_refresh_token=False, persist_session=False))
    client.auth._auto_refresh_token = False
    return client


def test_no_timer_renews_someone_elses_session(real_client):
    mod, client = real_client
    assert client.auth._auto_refresh_token is False

    client.auth._save_session(_session())

    assert client.auth._refresh_token_timer is None, "a background timer would consume the refresh token"


def test_every_auth_call_leaves_nothing_behind(real_client):
    mod, client = real_client
    client.auth._save_session(_session())

    mod._forget_auth_session(client)

    assert client.auth._in_memory_session is None


def test_sign_in_sign_up_and_refresh_all_forget():
    import inspect
    for fn in (db.sign_in, db.sign_up, db.refresh_session):
        assert "_forget_auth_session(client)" in inspect.getsource(fn), fn.__name__
