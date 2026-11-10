"""
Tests for opening an attached receipt.

The 📎 icon did nothing on iPhone. The code fetched a URL from the server with
fetch and then called window.open, and on iOS that is always blocked: the browser
allows opening a window only as a direct result of a tap, and waiting for the
server breaks the link. No window, no error, no hint.

The actual result: scanned receipts weren't accessible from the app on a phone at
all.
"""
from pathlib import Path

import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TX   = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client(monkeypatch):
    # the transaction isn't in someone else's personal project (see tx_visible_required)
    monkeypatch.setattr(app_module.db, "personal_project_owner", lambda *a: None)
    # editing checks the current attributions: here a normal family transaction
    monkeypatch.setattr(app_module.db, "transaction_links",
                        lambda *a: {"user_id": None, "project_id": None})
    # touches the shared client, and a unit test has none
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(app_module.db, "get_family", lambda *a, **k: {})
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]      = "22222222-2222-2222-2222-222222222222"
            sess["family_id"]    = "33333333-3333-3333-3333-333333333333"
            sess["access_token"] = "tok"
        yield c


# ─── The fix itself ──────────────────────────────────────────────────────────

def test_the_badge_opens_the_in_app_viewer_and_voiceover_gets_a_real_link(monkeypatch):
    """The receipt opens inside the app (receipt-viewer.js), so iOS window blocking is
    no longer relevant. The icon on the row sits inside the edit button, so it
    isn't a link: a link inside a button is a place VoiceOver doesn't reach (Matan,
    13.10). The screen reader has a real "צפייה בקבלה" in the edit window."""
    for page in ("index.html", "month.html", "project_detail.html"):
        html = (_ROOT / "frontend/templates" / page).read_text(encoding="utf-8")
        assert '<span class="receipt-badge" data-href=' in html, f"{page}: the icon is still a link"
        assert '<a class="receipt-badge"' not in html
        assert 'data-receipt-id' not in html, f"{page}: the old button remained"
    base = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
    assert 'id="txReceiptLink"' in base
    viewer = (_ROOT / "frontend/static/js/receipt-viewer.js").read_text(encoding="utf-8")
    assert "open(a.dataset.href || a.href);" in viewer


def test_no_javascript_opens_the_receipt_any_more():
    """Comments are ignored: the word window.open appears there in the explanation of
    the fix, and a test that fails on an explanation is a test someone will delete
    instead of fixing."""
    import re
    js = (_ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)          # comment blocks
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)         # line comments

    assert "window.open" not in code, "a window is still opened from JS: it will be blocked on iOS"


def test_the_row_handlers_still_ignore_the_badge():
    """Without the exclusion, tapping the icon also opens the edit modal."""
    js = (_ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")

    # the ✕ of a recurring transaction is gone (Matan, 11.10), so it is no longer in
    # the exclusion list
    assert js.count(".receipt-badge, .swipe-action") >= 3


def test_the_badge_has_a_real_tap_target():
    """22px inside a row that is both dragged and tapped: a miss opens editing
    instead of the receipt."""
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    block = css[css.index(".receipt-badge::before"):][:220]

    assert "width: 40px" in block and "height: 40px" in block


# ─── The route ───────────────────────────────────────────────────────────────

def test_it_redirects_to_the_signed_url(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "get_transaction_receipt_path",
                        lambda tx, fam: "fam/abc.webp")
    monkeypatch.setattr(app_module.db, "get_receipt_signed_url",
                        lambda token, path: ("https://x.supabase.co/signed", None))

    response = client.get(f"/receipts/{_TX}")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://x.supabase.co/signed"


def test_the_signed_url_is_never_cached(client, monkeypatch):
    """Short-lived and personal: it shouldn't sit in a proxy cache."""
    monkeypatch.setattr(app_module.db, "get_transaction_receipt_path",
                        lambda tx, fam: "fam/abc.webp")
    monkeypatch.setattr(app_module.db, "get_receipt_signed_url",
                        lambda token, path: ("https://x.supabase.co/signed", None))

    assert client.get(f"/receipts/{_TX}").headers["Cache-Control"] == "no-store"


def test_a_missing_receipt_shows_a_page_not_json(client, monkeypatch):
    """This is a tab opened in the browser. Raw JSON there is a broken screen."""
    monkeypatch.setattr(app_module.db, "get_transaction_receipt_path",
                        lambda tx, fam: None)

    response = client.get(f"/receipts/{_TX}")

    assert response.status_code == 404
    assert response.mimetype == "text/html"


def test_it_is_not_under_api_so_an_expired_session_goes_to_the_login_page():
    """Under /api/ an expired session would return 401 JSON to a new tab, instead of
    sending to sign-in. This is a browser navigation, not a background call."""
    app.config["TESTING"] = True
    response = app.test_client().get(f"/receipts/{_TX}")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
