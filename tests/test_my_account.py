""""החשבון שלי" (Matan, 13.10), and the removed Face ID.

"החשבון שלי": like "עסקאות קבועות", a whole row is the button, without "עדכון"
buttons. Face ID was removed entirely (Matan, 13.10): on an iPhone a website can't
open Face ID directly; Apple first shows a "sign in to railway.app" window and
waits for a tap. "I want the site to simply recognise me": the sign-in is kept
(web_sessions), so you go straight in."""
from pathlib import Path

import pytest

from backend.app import app


def _noop():
    return "ok"


app.add_url_rule("/__test/old-lock", "test_old_lock", _noop)

pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parent.parent


def test_my_account_rows_look_like_the_recurring_list():
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    mine = html[html.index('id="my-details"'):]
    mine = mine[:mine.index("</section>")]
    for el in ("editProfileBtn", "memberColorOpen", "toggleEmailBtn", "togglePhoneBtn", "togglePasswordBtn"):
        assert 'class="acct-row' in mine and f'id="{el}"' in mine, el
    for old in ("עדכון כתובת מייל", "עדכון מספר טלפון", ">עדכון סיסמה<", ">עדכון</button>"):
        assert old not in mine, old
    for title in ("פרטים", "התחברות"):
        assert f'<p class="recurring-group-title">{title}</p>' in mine


def test_face_id_is_gone_everywhere():
    for rel in ("frontend/static/js/passkey.js", "frontend/static/js/face-lock.js",
                "frontend/templates/unlock.html"):
        assert not (_ROOT / rel).exists(), rel
    for rel in ("frontend/templates/login.html", "frontend/templates/settings.html",
                "frontend/templates/base.html", "frontend/static/js/auth-guard.js",
                "frontend/static/js/whats-new.js", "frontend/templates/landing.html",
                "backend/app.py", "backend/supabase_config.py"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        for word in ("passkey", "face_lock", "/unlock", "data-face-lock"):
            assert word not in text, (rel, word)
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert not [r for r in rules if "passkey" in r or "unlock" in r or r == "/api/alive"]


def test_an_old_session_that_was_locked_just_goes_in(monkeypatch):
    """Whoever had face_lock in the cookie before the removal goes straight in, with
    no lock screen."""
    import time
    from backend import app as app_module
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda _t: None)
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = "u1"; s["family_id"] = None; s["access_token"] = "a"; s["refresh_token"] = "r"
        s["token_expires_at"] = time.time() + 3000
        s["face_lock"] = True; s["locked"] = True; s["last_seen"] = 0
    assert c.get("/__test/old-lock").status_code == 200


def test_fixed_and_variable_sit_small_beside_the_donut():
    """Matan (13.10): "without the bar and without the caption; let it be written
    small at the side of the chart"."""
    tpl = (_ROOT / "frontend/templates/month.html").read_text(encoding="utf-8")
    assert "fixed-split" not in tpl
    corner = tpl[tpl.index('<div class="split-corner">'):]
    corner = corner[:corner.index("</div>")]
    assert "קבועות" in corner and "משתנות" in corner          # both on the same side
    src = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    assert 'fixed_split = {"fixed": fixed_total, "variable": round(exp_total - fixed_total, 2)}' in src


def test_opening_a_category_closes_the_others():
    """Matan (13.10): "when one category is tapped, the others close immediately"."""
    for name in ("month.js", "project-detail.js"):
        js = (_ROOT / "frontend/static/js" / name).read_text(encoding="utf-8")
        assert "if (isOpen) closeOtherCategories(trigger, wrap);" in js, name


def test_the_split_is_separated_from_the_list_and_budgets_have_no_bar():
    """Matan (13.10): "let there be a separation between them and the category
    list"; "even when there is an overrun: no line, just the note"."""
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    assert ".donut-row.has-split { padding-bottom: 30px; margin-bottom: 6px; border-bottom: 1px solid var(--divider-subtle); }" in css
    tpl = (_ROOT / "frontend/templates/month.html").read_text(encoding="utf-8")
    assert "cat-budget-track" not in tpl and "cat-budget-fill" not in tpl
    assert "cat-budget-track" not in css


def test_logout_and_contact_sit_at_the_bottom_of_the_settings_page():
    """Matan (13.10): "let the sign-out and the write-to-us sentence be at the
    bottom of the page"."""
    tpl = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    footer = tpl[tpl.index('<div class="settings-footer">'):]
    assert footer.index("logout-form") < footer.index("settings-contact") < footer.index("</div>\n\n</div>")
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    assert ".settings-footer { margin-top: auto;" in css
    assert "body.settings-page { display: flex; flex-direction: column; }" in css
    # Matan (13.10): 100dvh here brought back the bug of 30.9: the bottom menu rose
    rule = css[css.index("body.settings-page { display: flex"):]
    assert "dvh" not in rule[:rule.index("}")]
