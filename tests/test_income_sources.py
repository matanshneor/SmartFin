""""Income: where it came from": a salary per person, and everything else together
(Matan, 30.9, proposal D).

Instead of a row per category ("משכורת", "הכנסה נוספת"), a row per salary by
whoever it is recorded under, and one "other income" row for everything else.
"""
import pytest

from backend import supabase_config as db

pytestmark = pytest.mark.unit


def _tx(amount, category, user_id=None, user_name=None, type_="income"):
    return {"id": f"t{amount}", "type": type_, "amount": amount, "category_name": category,
            "category_icon": "💼" if "משכורת" in category else "➕",
            "user_id": user_id, "user_name": user_name or ("משותפת" if not user_id else None)}


def test_a_salary_per_person_and_the_rest_together():
    sources = db.income_sources([
        _tx(12400, "משכורת", "u1", "מתן"),
        _tx(3741, "משכורת", "u2", "אור"),
        _tx(700, "הכנסה נוספת", "u2", "אור"),
        _tx(500, "החזרים"),
        _tx(90, "סופר", type_="expense"),
    ])

    assert [(s["kind"], s["who"], s["total"]) for s in sources] == [
        ("משכורת", "מתן", 12400), ("משכורת", "אור", 3741), ("הכנסות נוספות", "", 1200)]
    assert len(sources[2]["transactions"]) == 2


def test_two_salary_payments_of_the_same_person_are_one_row():
    sources = db.income_sources([_tx(6000, "משכורת", "u1", "מתן"), _tx(6400.5, "משכורת", "u1", "מתן")])

    assert [(s["who"], s["total"]) for s in sources] == [("מתן", 12400.5)]


def test_a_salary_nobody_is_attached_to_is_just_salary():
    """Income attribution off, or shared income: "משכורת" with no name, not
    "משכורת משותפת"."""
    sources = db.income_sources([_tx(9000, "משכורת")])

    assert [(s["kind"], s["who"]) for s in sources] == [("משכורת", "")]


def test_salaries_come_first_biggest_first_and_extra_last():
    sources = db.income_sources([_tx(300, "מתנות"), _tx(3000, "משכורת", "u2", "אור"),
                                 _tx(9000, "משכורת", "u1", "מתן")])

    assert [s["who"] or s["kind"] for s in sources] == ["מתן", "אור", "הכנסות נוספות"]


def test_no_income_no_rows():
    assert db.income_sources([_tx(50, "סופר", type_="expense")]) == []


def _section(sec_id):
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "frontend/templates/month.html").read_text(encoding="utf-8")
    section = html[html.index(f'id="{sec_id}"'):]
    return section[:section.index("</section>")]


def test_the_income_card_is_a_doughnut_like_expenses():
    """Matan (30.9): the heading "income" only, a donut like expenses, and a colour
    for each source (the family member, or grey) and a percentage. In the
    browser: tests/browser/income_sources.py."""
    section = _section("income-breakdown")
    assert '<h2 class="chart-title">הכנסות</h2>' in section
    assert 'id="incomeChart"' in section and "נכנס החודש" in section
    assert "{{ src.pct }}%" in section and "background: {{ src.color }}" in section


def test_the_savings_card_is_a_doughnut_with_its_share_of_income():
    """Matan (30.9): "savings" only, a donut, "X% of this month's income", and a
    percentage for each goal. In the browser: tests/browser/savings_section.py."""
    section = _section("savings-breakdown")
    assert '<h2 class="chart-title">חיסכון</h2>' in section
    # No donut (Matan, 30.9): "there's no comparison involved here"
    assert "<canvas" not in section and "סה״כ חיסכון החודש" in section
    assert "% מההכנסות החודש" in section and "{{ item.share }}%" in section


def test_the_label_names_the_person():
    sources = db.income_sources([_tx(100, "משכורת", "u1", "אור"), _tx(50, "מתנה")])
    assert [s["label"] for s in sources] == ["משכורת אור", "הכנסות נוספות"]
