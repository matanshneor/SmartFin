"""
Tests for the shape of the API routes' response when something goes wrong.

The bug this covers: login_required returned a redirect for the 38 API routes
too. fetch follows redirects silently, so the client got the sign-in page as
HTML with status 200, tried to parse it as JSON, and fell into a catch saying
"network error". The user tried again and got exactly the same, forever, with no
hint that the fix is to sign in again. The same happened on 404 and on 500.

The rule checked here: a call to /api/ gets JSON with a real error status,
never HTML, and never 200 when it failed. Ordinary navigation keeps getting a
page.
"""
import json

import pytest

from backend.app import app

pytestmark = pytest.mark.unit


@pytest.fixture
def anon():
    app.config["TESTING"] = True
    return app.test_client()


# ─── An expired session ─────────────────────────────────────────────────────

# A representative sample of the three kinds: read, write and delete
_GUARDED = [
    ("GET",    "/api/categories"),
    ("GET",    "/api/family/members"),
    ("GET",    "/api/projects"),
    ("POST",   "/api/transactions"),
    ("DELETE", "/api/transactions/00000000-0000-0000-0000-000000000000"),
]


@pytest.mark.parametrize("method,path", _GUARDED)
def test_an_expired_session_returns_401_json_not_a_login_page(anon, method, path):
    response = anon.open(path, method=method)

    assert response.status_code == 401, (
        f"{method} {path} returned {response.status_code}: "
        "the client will read a redirect to the sign-in page as a 'network error' and get stuck"
    )
    assert response.mimetype == "application/json"
    assert json.loads(response.get_data(as_text=True))["error"]


def test_a_normal_page_still_redirects_to_the_login_form(anon):
    """Control: the split is between API and navigation, not cancelling the redirect."""
    response = anon.get("/settings")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# ─── Server errors ──────────────────────────────────────────────────────────

def test_a_missing_api_route_returns_json(anon):
    response = anon.get("/api/no-such-thing")

    assert response.status_code == 404
    assert response.mimetype == "application/json"


def test_a_missing_page_still_returns_html(anon):
    response = anon.get("/no-such-page")

    assert response.status_code == 404
    assert response.mimetype == "text/html"


def _always_raises():
    raise RuntimeError("boom")


# Registered at module level: Flask doesn't allow adding a route after the first request
app.add_url_rule("/api/__boom", "test_api_boom", _always_raises)


def test_a_server_error_on_an_api_route_returns_json():
    """A real server failure must arrive as a recognisable error. When it arrives
    as HTML the user sees "network error" and retries a request that will never
    succeed."""
    app.config["TESTING"] = False          # otherwise Flask raises the exception out
    try:
        response = app.test_client().get("/api/__boom")
        assert response.status_code == 500
        assert response.mimetype == "application/json"
        assert json.loads(response.get_data(as_text=True))["error"]
    finally:
        app.config["TESTING"] = True


# ─── The client-side guard ──────────────────────────────────────────────────

def test_every_page_behind_the_login_loads_the_401_guard():
    """A server returning 401 doesn't help if nobody listens to it. The standalone
    pages (onboarding) don't load core.js, so the guard is in a separate file."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent

    for template in ("base.html", "onboarding.html"):
        html = (root / "frontend/templates" / template).read_text(encoding="utf-8")
        assert "js/auth-guard.js" in html, f"{template} doesn't load the guard"

    guard = (root / "frontend/static/js/auth-guard.js").read_text(encoding="utf-8")
    assert "401" in guard and "/login" in guard
