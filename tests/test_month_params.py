"""
Tests for handling the month page's year/month parameters.

Found in production while verifying Sentry: `/month?month=99999999` returned 500.
type=int checks that the value is a number, not that it is an existing month, and
from there _month_label did `_HEBREW_MONTHS[99999999]` and raised IndexError.
Anyone with the URL could crash the page for a user.

The more dangerous case was the silent one: a negative index is valid in Python,
so `?month=-5` returned "אוגוסט" without complaint: a page that looks fine and
shows the wrong month.

The tests don't touch Supabase: they set up a session without family_id, a path
that renders the empty page and doesn't call the database.
"""
import pytest

from backend.app import _month_label, app

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            # A signed-in user with no family: passes login_required and stops
            # before any query, so the code processing the parameters does run
            sess["user_id"]        = "00000000-0000-0000-0000-000000000000"
            sess["user_name"]      = "בדיקה"
            sess["avatar_initial"] = "ב"
            sess["family_id"]      = None
        yield c


@pytest.mark.parametrize("query", [
    "month=99999999",            # the value that crashed production
    "month=13",
    "month=0",
    "month=-5",                  # returned "אוגוסט" silently
    "year=99999999",
    "year=-1",
    "year=99999999&month=99999999",
    "month=abc",                 # not a number: Flask falls back to the default
    "month=",
])
def test_out_of_range_params_render_instead_of_crashing(client, query):
    response = client.get(f"/month?{query}")

    assert response.status_code == 200, f"?{query} wasn't rendered ({response.status_code})"


def test_a_valid_month_is_respected(client):
    """Control: the hardening shouldn't trample valid input."""
    response = client.get("/month?year=2026&month=3")

    assert response.status_code == 200
    assert "מרץ 2026" in response.get_data(as_text=True)


@pytest.mark.parametrize("month", [13, 0, -5, 99999999])
def test_month_label_never_indexes_out_of_range(month):
    """`_month_label` is called from several routes, so it is hardened separately
    from the validation at the entrance, and doesn't rely on it."""
    label = _month_label(2026, month)

    assert label.endswith("2026")
    assert label.split()[0], "the month name shouldn't come out empty"


@pytest.mark.parametrize("month,expected", [(1, "ינואר"), (12, "דצמבר")])
def test_month_label_keeps_valid_months(month, expected):
    assert _month_label(2026, month) == f"{expected} 2026"
