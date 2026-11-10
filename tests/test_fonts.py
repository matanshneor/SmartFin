"""
Tests for the self-hosted font.

Rubik was loaded from Google, with three results: it blocked rendering until it
arrived; Google caches it for one day only, so a daily user downloaded it again
every day; and the PWA fell back to a system font offline, because the Service
Worker doesn't intercept external origins.

And also: every user's IP address was sent to Google on every load, in an app
with a published privacy policy about household finances.
"""
import re
from pathlib import Path

import pytest

from backend.app import app

pytestmark = pytest.mark.unit

_ROOT  = Path(__file__).resolve().parent.parent
_FONTS = _ROOT / "frontend/static/fonts"
_CSS   = (_FONTS / "rubik.css").read_text(encoding="utf-8")


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


# ─── Nothing is loaded from Google ──────────────────────────────────────────

@pytest.mark.parametrize("page", [
    p.name for p in (_ROOT / "frontend/templates").glob("*.html")
])
def test_no_template_reaches_out_to_google(page):
    html = (_ROOT / "frontend/templates" / page).read_text(encoding="utf-8")

    assert "fonts.googleapis" not in html, f"{page} still loads a font from Google"
    assert "fonts.gstatic" not in html


def test_the_policy_no_longer_allows_google_either(client):
    """A policy that allows unused sources is needless attack surface."""
    csp = client.get("/login").headers["Content-Security-Policy"]

    assert "googleapis" not in csp and "gstatic" not in csp
    assert "font-src 'self'" in csp


# ─── The files are really there ─────────────────────────────────────────────

def test_every_font_the_css_asks_for_exists_and_is_a_real_font(client):
    """A broken link would fall back to a system font, i.e. exactly what was
    fixed, only without anyone noticing."""
    urls = re.findall(r"url\('([^']+)'\)", _CSS)

    assert len(urls) == 8, f"{len(urls)} files: 8 expected (4 weights × 2 subsets)"
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.get_data()[:4] == b"wOF2", f"{url} isn't a valid woff2"


def test_only_hebrew_and_latin_are_shipped():
    """Google serves six subsets per weight: Cyrillic, Greek, Arabic and more.
    None of them is relevant, and each one is another file."""
    subsets = set(re.findall(r"/static/fonts/rubik-(\w+)-\d+\.woff2", _CSS))

    assert subsets == {"hebrew", "latin"}


def test_the_weights_match_what_the_stylesheet_uses():
    """A weight that isn't loaded is faked by the browser and looks different; a
    weight loaded and not used is a download for nothing."""
    app_css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    used   = set(re.findall(r"font-weight:\s*(\d00)", app_css))
    loaded = set(re.findall(r"font-weight:\s*(\d+)", _CSS))

    assert used <= loaded, f"used and not loaded: {used - loaded}"
    assert loaded - used == set(), f"loaded and not used: {loaded - used}"


def test_text_is_visible_while_the_font_loads():
    """Without swap, the browser hides text until the font arrives: a blank screen
    instead of text in a system font."""
    # Only inside @font-face rules: the mention in the comment explaining this
    # doesn't count
    faces = re.findall(r"@font-face\s*\{[^}]*\}", _CSS)

    assert len(faces) == 8
    assert all("font-display: swap" in f for f in faces)


# ─── And what this solves ───────────────────────────────────────────────────

def test_the_font_is_served_from_our_own_origin(client):
    """That is what makes it cached by the Service Worker, i.e. work offline: it
    intercepts only our own origins."""
    for url in re.findall(r"url\('([^']+)'\)", _CSS):
        assert url.startswith("/static/"), url


def test_the_font_css_is_cached_for_a_year_like_any_other_asset(client):
    """It is loaded through url_for, so it gets a fingerprint and the same cache
    policy, as opposed to one day only at Google."""
    from flask import render_template_string
    with app.test_request_context("/"):
        url = render_template_string(
            "{{ url_for('static', filename='fonts/rubik.css') }}")

    assert "?v=" in url
    assert "max-age=31536000" in client.get(url).headers["Cache-Control"]
