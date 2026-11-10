""""עברתם את התקציב של סופר ומזון ב-₪120" after adding an expense (Matan, 3.10,
idea 17)."""
import pytest

from backend import app as app_module
from backend.app import _budget_note, app

pytestmark = pytest.mark.unit

_FAM, _CAT = "fam", "food"


@pytest.fixture
def setup(monkeypatch):
    state = {"spent": 0.0, "limits": {_CAT: {"amount": 2000}}}
    monkeypatch.setattr(app_module, "family_settings", lambda: {"limits": state["limits"]})
    monkeypatch.setattr(app_module.db, "category_month_spent", lambda fid, cid, y, m: state["spent"])
    monkeypatch.setattr(app_module.db, "get_categories", lambda fid: [{"id": _CAT, "name": "סופר ומזון"}])
    return state


def _pay(amount, **kw):
    p = {"type": "expense", "category_id": _CAT, "amount": amount, "date": "2026-10-03", "project_id": None}
    p.update(kw)
    return p


def _note(state, spent_after, amount, **kw):
    state["spent"] = spent_after
    with app.test_request_context():
        return _budget_note(_FAM, _pay(amount, **kw))


def test_the_expense_that_crosses_the_line(setup):
    assert _note(setup, 2120, 300) == "עברתם את התקציב של סופר ומזון ב-₪120"


def test_already_over_before_this_expense(setup):
    assert _note(setup, 2340, 100) == "התקציב של סופר ומזון כבר נגמר — ₪340 מעליו"


def test_exactly_at_the_budget_is_not_over(setup):
    assert _note(setup, 2000, 300) is None


@pytest.mark.parametrize("kw", [{"type": "income"}, {"type": "savings"}, {"project_id": "p1"}],
                         ids=["income", "savings", "project"])
def test_not_for_income_savings_or_projects(setup, kw):
    assert _note(setup, 5000, 300, **kw) is None


def test_a_category_without_a_budget_says_nothing(setup):
    setup["limits"] = {}
    assert _note(setup, 5000, 300) is None


def test_the_month_comes_from_the_transaction_date(setup, monkeypatch):
    seen = []
    monkeypatch.setattr(app_module.db, "category_month_spent",
                        lambda fid, cid, y, m: seen.append((y, m)) or 2500)
    with app.test_request_context():
        _budget_note(_FAM, _pay(100, date="2026-09-30"))
    assert seen == [(2026, 9)]


def test_a_failed_lookup_skips_the_note_not_the_save(setup, monkeypatch):
    def boom(*a):
        raise app_module.db.DataUnavailable("x")
    monkeypatch.setattr(app_module.db, "category_month_spent", boom)
    with app.test_request_context():
        assert _budget_note(_FAM, _pay(100)) is None


def test_the_page_shows_it_only_for_a_new_expense():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    assert "if (isNew && data.budget_note) {" in js
    assert "followUp = { text: 'העסקה נוספה. ' + data.budget_note };" in js


def test_the_edit_window_shows_87_not_87_point_0():
    import subprocess
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    fn = js[js.index("function plainAmount(v) {"):]
    fn = fn[:fn.index("\n    }") + 6]
    out = subprocess.run(["node", "-e", fn + "\nconsole.log(JSON.stringify(['87.0','87.5','1200.00','abc'].map(plainAmount)))"],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == '["87","87.5","1200","abc"]'
    assert "txAmount.value       = plainAmount(tx.amount);" in js
