"""Who recorded each transaction, and "make personal again" that says upfront
what will disappear from whom.

Matan's decision (28.9.2026): a project that goes back to being personal belongs
only to whoever opened it. The transactions other family members recorded in it
stay, with a "נרשם ע״י אור" mark, and the confirmation says beforehand: "אור
רשם כאן 10 עסקאות".

Until now nothing knew who *recorded* a transaction: only `user_id`, "whose
money", which is reset when a project becomes shared. The `created_by` column is
filled in the database (`default auth.uid()`; checked in a simulation on the real
database).
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_OR  = "33333333-3333-3333-3333-333333333333"
_GONE = "44444444-4444-4444-4444-444444444444"
_NAMES = {_ME: "מתן", _OR: "אור"}


def _tx(i, created_by, **over):
    return {"id": f"t{i}", "type": "expense", "amount": 100.0, "date": "2026-09-0%d" % (i % 9 + 1),
            "description": f"עסקה {i}", "category_name": "חומרים", "category_icon": "🧱",
            "category_id": None, "project_category_id": "pc", "user_id": None,
            "user_name": "משותף", "is_recurring": False, "recurring_frequency": None,
            "recurring_end_date": None, "recurring_parent_id": None, "project_id": "p1",
            "has_receipt": False, "workplace": None, "created_by": created_by, **over}


def test_the_warning_names_who_entered_what():
    project = {"transactions": [_tx(1, _OR), _tx(2, _OR), _tx(3, _ME), _tx(4, None), _tx(5, _GONE)]}

    note = app_module._others_contributions(project, _ME, _NAMES)

    assert note == "2 עסקאות נרשמו כאן על ידי אור · עסקה אחת נרשמה כאן על ידי בן משפחה לשעבר"


def test_no_warning_when_nobody_else_entered_anything():
    """Unknown (`None`) isn't "someone else": transactions from before the change
    aren't counted."""
    project = {"transactions": [_tx(1, _ME), _tx(2, None)]}

    assert app_module._others_contributions(project, _ME, _NAMES) == ""


def test_the_field_reaches_the_screen():
    rows = db._format_transactions([{
        "id": "t1", "type": "expense", "amount": 5, "date": "2026-09-01",
        "created_by": _OR, "categories": None, "profiles": None, "projects": None}])

    assert rows[0]["created_by"] == _OR


# ─── The mark on the project page ───────────────────────────────────────────

@pytest.fixture
def page(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {})
    monkeypatch.setattr(db, "get_family_members",
                        lambda fid: [{"id": _ME, "name": "מתן"}, {"id": _OR, "name": "אור"}])
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: dict(db.DEFAULT_FAMILY_SETTINGS))

    def render(owner_id):
        project = {"id": "p1", "name": "שיפוץ", "description": None, "icon": "🔨",
                   "is_personal": bool(owner_id), "owner_id": owner_id, "created_by": _ME,
                   "track_expense": True, "track_income": False, "track_savings": False,
                   "budget_target": None, "spent": 300.0, "income": 0, "savings": 0,
                   "remaining": None, "breakdown": {"expense": [], "income": [], "savings": []},
                   "transactions": [_tx(1, _OR, description="קבלן"),
                                    _tx(2, _ME, description="צבע"),
                                    _tx(3, None, description="ישנה")]}
        monkeypatch.setattr(db, "get_project_detail", lambda *a, **k: project)
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            return c.get("/projects/p1").get_data(as_text=True)
    return render


def _row(html, description):
    i = html.index(description)
    # The rows are `<div class="cat-tx-row">` (not `<li>`: VoiceOver, 13.10)
    return html[html.rindex('class="cat-tx-row', 0, i):html.index("</div>", i)]


def test_a_personal_project_marks_what_someone_else_entered(page):
    html = page(owner_id=_ME)

    assert "נרשמה ע״י אור" in _row(html, "קבלן")
    assert "נרשמה ע״י" not in _row(html, "צבע"), "what I recorded myself doesn't need a mark"
    assert "נרשמה ע״י" not in _row(html, "ישנה"), "unknown isn't someone else"


def test_a_shared_project_has_no_marks(page):
    """In a shared project everyone records: the mark is meant for a project that
    went back to being personal."""
    assert "נרשם ע״י" not in page(owner_id=None)
