"""Three sections on the month page that didn't do what their name promised.

**"קבוע כל חודש"**: a name that described one frequency out of five. A weekly or
biweekly template appeared under a heading saying it is monthly. And next to it a
"ניהול" button that was a link to settings: whoever stood here and saw the rent
was wrong was sent to another page, to scroll, and find the very same row there,
instead of touching the one in front of them.

**"לאן הלך הכסף החודש"**: the card shows two slices, expenses versus savings.
The name promised a breakdown of destinations.

**"פרויקטים החודש"**: a flat list of all project transactions together. In a
month with a renovation and a trip they couldn't be told apart, and that is
exactly the question being asked.
"""
import re
from pathlib import Path

import pytest

from backend.supabase_config import project_breakdown_from_rows

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_HTML = (_ROOT / "frontend/templates/month.html").read_text(encoding="utf-8")
_JS = _ROOT / "frontend/static/js"


def _tx(project_id, name, kind, amount, icon="🎯"):
    return {"project_id": project_id, "project_name": name, "project_icon": icon,
            "type": kind, "amount": amount, "id": f"{project_id}-{kind}-{amount}"}


# ═══ Grouping by project ═════════════════════════════════════════════════════

def test_each_project_is_its_own_row():
    """The heart: a renovation and a trip are two separate questions."""
    rows = project_breakdown_from_rows([
        _tx("a", "שיפוץ", "expense", 800),
        _tx("a", "שיפוץ", "expense", 3400),
        _tx("b", "טיול", "expense", 1100),
    ])

    assert [r["name"] for r in rows] == ["שיפוץ", "טיול"]
    assert rows[0]["expense"] == 4200
    assert rows[1]["expense"] == 1100


def test_two_projects_with_the_same_name_stay_apart():
    """Grouping by name would have merged their money: exactly the bug that was in
    the category breakdown, and there it hid a whole budget."""
    rows = project_breakdown_from_rows([
        _tx("a", "טיול", "expense", 1000),
        _tx("b", "טיול", "expense", 2000),
    ])

    assert len(rows) == 2, "two different projects were merged by their name"
    assert {r["expense"] for r in rows} == {1000.0, 2000.0}


def test_income_is_not_netted_against_expense():
    """A project with a refund would look "cheaper", and a balanced two-way project
    would show as ₪0: a number that hides both sides."""
    rows = project_breakdown_from_rows([
        _tx("a", "טיול", "expense", 1100),
        _tx("a", "טיול", "income", 300),
    ])

    assert rows[0]["expense"] == 1100
    assert rows[0]["income"] == 300


def test_a_transaction_without_a_project_is_left_out():
    """Normal transactions are already counted in the monthly balance. Counting
    them twice here is exactly what the section exists to prevent."""
    rows = project_breakdown_from_rows([
        _tx("a", "שיפוץ", "expense", 800),
        {"project_id": None, "type": "expense", "amount": 50},
    ])

    assert len(rows) == 1
    assert rows[0]["expense"] == 800


def test_projects_are_ordered_by_what_actually_went_out():
    rows = project_breakdown_from_rows([
        _tx("small", "קטן", "expense", 100),
        _tx("big", "גדול", "expense", 5000),
        _tx("mid", "בינוני", "savings", 900),
    ])

    assert [r["name"] for r in rows] == ["גדול", "בינוני", "קטן"]


def test_every_row_carries_its_own_transactions():
    """Without this there is nothing to open."""
    rows = project_breakdown_from_rows([
        _tx("a", "שיפוץ", "expense", 800),
        _tx("a", "שיפוץ", "expense", 3400),
        _tx("b", "טיול", "expense", 1100),
    ])

    assert len(rows[0]["transactions"]) == 2
    assert len(rows[1]["transactions"]) == 1


def test_a_project_with_no_name_still_gets_a_row():
    """A missing name is missing data, not a reason to make ₪3,000 vanish from the
    screen."""
    rows = project_breakdown_from_rows([
        {"project_id": "x", "type": "expense", "amount": 3000},
    ])

    assert len(rows) == 1 and rows[0]["expense"] == 3000


def test_nothing_in_means_nothing_out():
    assert project_breakdown_from_rows([]) == []


# ═══ What the template shows ═════════════════════════════════════════════════

def test_the_month_page_has_no_recurring_section():
    """Matan (2.10): "עסקאות קבועות" was removed from the month page: these
    transactions are already among the month's other transactions, and they are
    managed in settings. And the ✕ that was there deleted the series' first month."""
    html = re.sub(r"\{#.*?#\}", "", _HTML, flags=re.S)
    assert "עסקאות קבועות</h2>" not in html
    assert "fixedList" not in html and "fixedManageBtn" not in html
    assert "fixedList" not in (_JS / "transactions.js").read_text(encoding="utf-8")
    assert "fixedManageBtn" not in (_JS / "month.js").read_text(encoding="utf-8")


def test_there_is_no_expenses_versus_savings_chart():
    """Matan (30.9): "I don't want a chart of expenses versus savings, it feels
    irrelevant to me"."""
    html = re.sub(r"\{#.*?#\}", "", _HTML, flags=re.S)
    assert "הוצאות מול חיסכון" not in html
    assert "overviewChart" not in html and "overviewLegend" not in html
    js = (_ROOT / "frontend/static/js/month.js").read_text(encoding="utf-8")
    assert "overviewChart" not in js and "buildOverviewLegend" not in js


def _section(title):
    """The section from its heading to the next section. A fixed length cuts in the
    middle, and such a cut makes the test report the absence of what it simply
    didn't reach."""
    start = _HTML.index(title)
    nxt = _HTML.find("<!-- Chart", start)
    return _HTML[start:nxt if nxt != -1 else len(_HTML)]


def test_the_projects_section_is_one_row_per_project():
    block = _section("פרויקטים החודש")

    assert "for proj in project_month.projects" in block, "still a flat list"
    assert 'aria-expanded="false"' in block, "the row doesn't open"
    assert "for tx in proj.transactions" in block, "there is nothing to open into"


def test_the_expanded_project_links_to_its_own_page():
    assert "project_detail" in _section("פרויקטים החודש")


# ═══ No second copy of the delete code ═══════════════════════════════════════

def test_the_series_is_stopped_from_one_place():
    """The ✕ on the row is gone (Matan, 11.10, option A). Stopping moved to a button
    in the edit window and to the swipe, and both go through the same function:
    stopSeriesFromSettings."""
    owners = [f.name for f in _JS.glob("*.js")
              if "function stopSeriesFromSettings" in f.read_text(encoding="utf-8")]
    assert owners == ["transactions.js"], f"the function is found in: {owners}"
    js = (_JS / "transactions.js").read_text(encoding="utf-8")
    assert "window.sfStopSeries = stopSeriesFromSettings;" in js
    assert "delete-recurring-btn" not in js


def test_stopping_a_series_from_settings_does_not_delete_money():
    """Control. "Stop without deleting" is a real action that has to exist somewhere,
    and settings are where the series is managed, not a particular month. The
    delete button in the window, when opened from "עסקאות קבועות", stops the
    series: it doesn't delete the template."""
    js = (_JS / "transactions.js").read_text(encoding="utf-8")
    stop = js[js.index("function stopSeriesFromSettings"):][:2000]
    assert "/api/recurring/" in stop and "deleteWithUndo" not in stop
    handler = js[js.index("deleteBtn.addEventListener('click'"):]
    handler = handler[:handler.index("const row = document.querySelector")]
    assert "if (editingSeries)" in handler and "window.sfStopSeries(seriesRow)" in handler
    assert "deleteWithUndo" not in handler


def test_removing_a_row_on_the_month_page_refreshes_the_totals():
    """The totals above the list are derived from the rows, so removing the row alone
    leaves them at the old value. deleteWithUndo refreshes by itself after the
    "בטל" window."""
    js = (_JS / "transactions.js").read_text(encoding="utf-8")
    block = js[js.index("function deleteWithUndo"):]
    block = block[:block.index("\n    function ")]

    assert "refreshAfterDelete" in block, "the numbers stay stuck after a delete"


def test_the_sections_come_in_matans_order():
    """Matan (30.9): monthly balance in a wide box, three equal boxes, then expenses,
    income, savings, by family member, all transactions, projects this month.
    (Recurring transactions removed, 2.10; expenses before income, 12.10.)"""
    order = ['id="expense-breakdown"', 'id="income-breakdown"', 'id="savings-breakdown"',
             "{{ mb.label }} לפי בן משפחה",
             '<h2 class="chart-title">כל העסקאות</h2>', '<h2 class="chart-title">פרויקטים החודש</h2>']
    where = [_HTML.index(m) for m in order]
    assert where == sorted(where), [m for _, m in sorted(zip(where, order))]
    top = _HTML[:_HTML.index("{% endblock %}", _HTML.index("{% block hero %}"))]
    assert top.index('class="month-net') < top.index('kpi-chips kpi-chips-three')
    assert "הכנסות פחות הוצאות וחיסכון" not in re.sub(r"\{#.*?#\}", "", top, flags=re.S), \
        "Matan: no explanation line under the balance"
    import re as _re
    assert len(_re.findall(r'class="kpi-chip[ "]', top)) == 6   # three, in each of the a/div branches
