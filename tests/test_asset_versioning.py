"""
Tests for the static asset fingerprint.

Every file used to be sent with "no-cache", i.e. the browser asked the server on
every page load whether its copy was still valid. Six questions on every page,
all answered "not modified", and each one occupies one of the two workers: six
out of seven requests in an ordinary load were a conversation about nothing.

The risk in the fix is bigger than the problem: a URL cached for a year whose
fingerprint doesn't change is an old file there is no way to refresh remotely
(not by reloading, not by clearing, only by waiting a year). So the fingerprint
is derived from the file's content and not a manual number, and the central
check here is that it really changes when the content changes.
"""
import re
from pathlib import Path

import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_hashes():
    app_module._ASSET_HASHES.clear()
    yield
    app_module._ASSET_HASHES.clear()


def _rendered(filename="css/style.css"):
    from flask import render_template_string
    with app.test_request_context("/"):
        return render_template_string(
            "{{ url_for('static', filename='" + filename + "') }}")


# ─── The heart: the fingerprint follows the content ─────────────────────────

def test_the_version_changes_when_the_file_changes(tmp_path, monkeypatch):
    """If this isn't true, users get an old file for a year and there is no way to fix it."""
    static = tmp_path / "static"
    static.mkdir()
    asset = static / "x.css"
    monkeypatch.setattr(app, "static_folder", str(static))

    asset.write_text("body { color: red }", encoding="utf-8")
    before = app_module._asset_version("x.css")

    app_module._ASSET_HASHES.clear()
    asset.write_text("body { color: blue }", encoding="utf-8")
    after = app_module._asset_version("x.css")

    assert before and after
    assert before != after, "the content changed and the fingerprint didn't: an old file will be served for a year"


def test_the_version_is_derived_from_content_not_a_manual_number():
    """A number you have to remember to bump is a number that will be forgotten."""
    import inspect
    src = inspect.getsource(app_module._asset_version)

    assert "sha256" in src
    assert "read()" in src


def test_the_same_file_keeps_the_same_version():
    """Control: a fingerprint that changes for nothing cancels the cache on every deploy."""
    assert _rendered() == _rendered()


# ─── The URLs produced ──────────────────────────────────────────────────────

def test_static_urls_carry_a_version():
    url = _rendered()

    assert re.search(r"/static/css/style\.css\?v=[0-9a-f]{8}$", url), url


def test_templates_did_not_need_to_change():
    """48 calls to url_for in templates. An override and not a new function, so a
    call written tomorrow gets this too without anyone remembering."""
    html = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")

    assert "url_for('static'" in html
    assert "?v=" not in html, "the fingerprint was written by hand in the template"


def test_a_missing_file_does_not_take_the_page_down():
    """A missing asset is a bug to be seen, not a reason for a 500 on the whole page."""
    assert app_module._asset_version("does/not/exist.css") == ""
    assert _rendered("does/not/exist.css") == "/static/does/not/exist.css"


# ─── The cache headers ──────────────────────────────────────────────────────

def test_a_versioned_url_is_cached_for_a_year():
    app.config["TESTING"] = True
    headers = app.test_client().get("/static/css/style.css?v=abc123").headers

    assert "max-age=31536000" in headers["Cache-Control"]
    assert "immutable" in headers["Cache-Control"]


def test_an_unversioned_url_is_not():
    """This is the guard: a URL without a fingerprint is a URL that can't be
    refreshed, so it isn't cached for a year. Without this condition one mistake
    locks a file for a year."""
    app.config["TESTING"] = True
    headers = app.test_client().get("/static/css/style.css").headers

    assert "max-age=31536000" not in headers.get("Cache-Control", "")


# ─── The Service Worker was adapted ─────────────────────────────────────────

def test_the_service_worker_does_not_precache_unversioned_urls():
    """A fixed list can't know the fingerprint. It would download URLs no page
    requests: a double download, and a cache nobody touches."""
    sw = (_ROOT / "frontend/static/sw.js").read_text(encoding="utf-8")
    assert "addAll" not in sw


def test_the_service_worker_treats_versioned_assets_as_immutable():
    sw = (_ROOT / "frontend/static/sw.js").read_text(encoding="utf-8")

    assert "searchParams.has('v')" in sw
    assert "if (cached && immutable) return cached;" in sw
