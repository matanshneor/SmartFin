"""
Tests for the public pages: the landing page and the legal pages.

Until now "/" was protected by login_required and redirected straight to /login,
so anyone who got a link landed on a sign-in form without knowing what the app
does or why they should give it financial data. That was the first blocker to
sharing it.

What must be covered is exactly the property that breaks easily in a refactor:
these pages must be accessible **without** a session. If someone brings back
login_required or changes the condition, the tests here will fail.
"""
import re

import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit


@pytest.fixture
def anon():
    """A client without a session: a visitor seeing the site for the first time."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def signed_in(monkeypatch):
    app.config["TESTING"] = True
    # What is tested here is which page is served, not what is in it. Without
    # this fake the test fetched categories from the real database.
    monkeypatch.setattr(app_module.db, "get_categories", lambda *a, **k: [])
    monkeypatch.setattr(app_module.db, "ensure_family", lambda *a, **k: None)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]        = "00000000-0000-0000-0000-000000000000"
            sess["user_name"]      = "בדיקה"
            sess["avatar_initial"] = "ב"
            sess["family_id"]      = None
        yield c


@pytest.mark.parametrize("path", ["/", "/privacy", "/terms"])
def test_public_pages_need_no_session(anon, path):
    response = anon.get(path)

    assert response.status_code == 200, f"{path} isn't accessible to an anonymous visitor"


def test_the_landing_page_explains_and_invites(anon):
    """A landing page that doesn't say what the app does and doesn't offer to sign up
    is just a page."""
    body = anon.get("/").get_data(as_text=True)

    assert "SmartFin" in body
    assert "/signup" in body, "a sign-up call to action is missing"
    assert "/login" in body, "a link for those already registered is missing"


def test_the_landing_page_links_to_the_legal_pages(anon):
    """These links are the reason the pages exist: without them nobody will reach
    them."""
    body = anon.get("/").get_data(as_text=True)

    assert "/privacy" in body
    assert "/terms" in body


def test_the_landing_page_is_never_cached(anon):
    """The same URL serves two different things depending on sign-in state, so
    caching one of them would show it to the wrong audience."""
    assert "no-store" in anon.get("/").headers.get("Cache-Control", "")


def test_a_signed_in_user_gets_the_app_not_the_landing_page(signed_in):
    """The same URL, a different audience: whoever is signed in should see the app."""
    body = signed_in.get("/").get_data(as_text=True)

    assert "פתיחת חשבון — בחינם" not in body, "a signed-in user got the marketing page"


def test_signup_points_at_the_legal_pages(anon):
    """Consent to the terms should appear where you sign up, not only in the footer."""
    body = anon.get("/signup").get_data(as_text=True)

    assert "/terms" in body
    assert "/privacy" in body


@pytest.mark.parametrize("path", ["/login", "/signup", "/reset-password"])
def test_auth_screens_offer_a_way_back(anon, path):
    """Tapping "פתיחת חשבון" from the landing page was a one-way path: the only way
    back was the browser's back button, which in an app installed to the home
    screen doesn't always exist at all."""
    body = anon.get(path).get_data(as_text=True)

    assert 'class="auth-back"' in body, f"{path} leaves the user stuck"


def test_the_way_back_leads_to_the_landing_page(anon):
    """The link must bring you to the landing page, even from a device that signed in
    once, which the root otherwise sends back to the sign-in form. So we check
    where it actually leads and not how it is written."""
    body = anon.get("/signup").get_data(as_text=True)
    back = body[body.index('class="auth-back"'):]
    href = re.search(r'href="([^"]+)"', back).group(1)

    anon.set_cookie("sf_returning", "1")
    landed = anon.get(href)

    assert landed.status_code == 200, "the back button doesn't reach the page: probably a redirect loop"
    assert "lp-hero" in landed.get_data(as_text=True)
