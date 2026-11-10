""""-₪0" in red on the home page.

The home page computed "left in the account" in a function that doesn't round
(`get_monthly_summary`), and the month page in one that does
(`summary_from_rows`). Floating-point fractions aren't exact: 513.92 minus 44.15 +
332.22 + 54.86 + 82.69 comes out as `-1.1e-13` and not 0. The template saw
"negative", coloured it red, and showed it rounded: "-₪0".

And in the same template, a real deficit of 40 agorot showed "-₪0" in red: the
colour says "deficit", the number says zero.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"


def _rows(income, *expenses):
    rows = [{"id": "i", "family_id": _FAM, "type": "income", "amount": income,
             "date": "2026-09-05", "project_id": None}]
    rows += [{"id": f"e{n}", "family_id": _FAM, "type": "expense", "amount": a,
              "date": "2026-09-06", "project_id": None} for n, a in enumerate(expenses)]
    return rows


def test_the_dashboard_summary_is_rounded_like_the_month_page(monkeypatch):
    monkeypatch.setattr(db, "get_client",
                        lambda: FakeSupabase(transactions=_rows(513.92, 44.15, 332.22, 54.86, 82.69)))

    summary = db.get_monthly_summary(_FAM, 2026, 9)

    assert summary["remaining"] == 0.0, summary["remaining"]


@pytest.fixture
def hero(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    for fn, val in (("get_family_settings", dict(db.DEFAULT_FAMILY_SETTINGS)),
                    ("get_categories", [{"id": "c", "name": "x", "type": "expense"}]),
                    ("get_family_members", []), ("family_has_no_transactions", False),
                    ("get_recent_transactions", []), ("week_spending", None), ("fetch_month_rows", [])):
        monkeypatch.setattr(db, fn, lambda *a, _v=val, **k: _v)

    def render(income, expense):
        remaining = income - expense
        monkeypatch.setattr(db, "get_monthly_summary", lambda *a: {
            "income": income, "expense": expense, "savings": 0.0, "balance": remaining,
            "remaining": remaining, "expense_pct": 50})
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            html = c.get("/").get_data(as_text=True)
        return html[html.index('class="hero-amount'):][:400]
    return render


def test_a_deficit_under_half_a_shekel_is_not_shown_as_minus_zero(hero):
    block = hero(1000.00, 1000.40)

    assert "deficit" not in block.split(">")[0], "a deficit colour on ₪0"
    assert "-₪0" not in block


def test_a_real_deficit_is_still_red(hero):
    """Control."""
    block = hero(1000.00, 1001.20)

    assert "deficit" in block.split(">")[0]
    assert "-₪1" in block


def test_a_month_with_no_income_is_not_shown_as_a_deficit(hero):
    """Almost every new user enters an expense before a salary, and their first
    screen was `-₪120` in red. (Moved here from test_ux_polish, which checked the
    source.)"""
    block = hero(0.0, 120.0)

    assert "deficit" not in block.split(">")[0]
    assert "-₪" not in block and "₪120" in block


# ─── The big number in whole shekels (Matan, 30.9) ─────────────────────────

@pytest.mark.parametrize("income,expense,shown,countup", [
    (34567.89, 33086.52, "₪1,481", 'data-countup="1481"'),    # 1,481.37
    (1000.00, 498.50, "₪502", 'data-countup="502"'),          # 501.50 → 502
    (1000.00, 1001.60, "-₪2", 'data-countup="2"'),            # a deficit of 1.60
], ids=["rounds-down", "half-rounds-up", "deficit"])
def test_the_big_balance_has_no_agorot(hero, income, expense, shown, countup):
    block = hero(income, expense)
    number = block[block.index(">") + 1:block.index("</p>")]

    assert shown in number.replace(" ", "").replace("\n", ""), number
    assert "." not in number, "agorot in the big number"
    assert countup in block
