"""The comparison page that loads its own data: the first screen of the
frontend/backend split.

Until now `/months` was built on the server. Behind a switch it becomes a fixed
page (`months_view.html`) that asks `/api/screens/months` for its data. Three
things have to hold for that to be safe:

  · With the switch off nothing changes, for anyone.
  · The numbers the API sends are the numbers the old page showed, digit for
    digit: both read `_months_view`, and the formatting follows the template.
  · The fixed page really is fixed: nothing in it belongs to a family.
"""
import re
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_FAM = "fam-1"

# Newest first, as the archive arrives. Amounts chosen to catch a rounding that
# differs between the two pages: x.5 goes to the even neighbour in the template
# (`| round`), and half a shekel must not flip a balance's sign.
_ARCHIVE = [
    {"year": 2026, "month": 11, "income": 18250.5, "expense": 9400.49, "savings": 1500},
    {"year": 2026, "month": 10, "income": 17000,   "expense": 21340.2, "savings": 0},
    {"year": 2026, "month": 9,  "income": 2.5,     "expense": 0,       "savings": 0},
    {"year": 2026, "month": 8,  "income": 16000,   "expense": 12000,   "savings": 4000},
    {"year": 2025, "month": 12, "income": 15000,   "expense": 11000.75, "savings": None},
]


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.delenv("SF_API_SCREENS", raising=False)
    monkeypatch.delenv("SF_API_SCREENS_PREVIEW", raising=False)
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module, "_sync_recurring", lambda *a: None)

    def no_rpc(*a, **k):
        raise db.DataUnavailable("no rpc in this test")
    monkeypatch.setattr(app_module.db, "fetch_months_page", no_rpc)
    monkeypatch.setattr(app_module.db, "get_months_archive", lambda fid: list(_ARCHIVE))
    monkeypatch.setattr(app_module.db, "get_categories", lambda *a, **k: [])
    monkeypatch.setattr(app_module.db, "category_trend", lambda *a, **k: {
        "months": [{"year": 2026, "month": 11, "label": "נוב׳", "name": "נובמבר"}],
        "categories": [{"key": "c1", "name": "סופר <b>ומזון</b>", "icon": "🛒", "values": [1200], "items": [[]]}]})
    monkeypatch.setattr(app_module.db, "get_family_members", lambda fid: [{"id": "u0"}])
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = "u0"
        sess["family_id"] = _FAM
    return c


def _api(client):
    r = client.get("/api/screens/months")
    assert r.status_code == 200
    return r.get_json()


# ─── The switch ─────────────────────────────────────────────────────────────

def test_with_no_switch_the_page_is_built_on_the_server_as_always(client):
    html = client.get("/months").get_data(as_text=True)
    assert 'id="sf-view-data"' in html
    assert 'id="monthsView"' not in html


@pytest.mark.parametrize("value", ["months", "projects, months", "*"])
def test_the_switch_opens_the_screen_to_everyone(client, monkeypatch, value):
    monkeypatch.setenv("SF_API_SCREENS", value)
    html = client.get("/months").get_data(as_text=True)
    assert 'id="monthsView"' in html
    assert 'id="sf-view-data"' not in html


def test_a_preview_family_gets_it_and_the_family_next_door_does_not(client, monkeypatch):
    monkeypatch.setenv("SF_API_SCREENS_PREVIEW", f"other-family, {_FAM}")
    assert 'id="monthsView"' in client.get("/months").get_data(as_text=True)

    with client.session_transaction() as sess:
        sess["family_id"] = "fam-2"
    assert 'id="monthsView"' not in client.get("/months").get_data(as_text=True)


def test_a_screen_that_was_not_converted_cannot_be_switched_on(monkeypatch):
    """`*` in the environment opens what was converted, not what wasn't: a typo
    there must not send a family to a page that doesn't exist."""
    monkeypatch.setenv("SF_API_SCREENS", "*,settings")
    with app.test_request_context("/"):
        assert app_module._api_screen("settings") is False
        assert app_module._api_screen("months") is True


def test_the_fixed_page_asks_the_database_nothing_about_the_months(client, monkeypatch):
    """The whole point: the page arrives without the data, and doesn't pay for it."""
    monkeypatch.setenv("SF_API_SCREENS", "months")

    def boom(*a, **k):
        raise AssertionError("the fixed page fetched the months")
    monkeypatch.setattr(app_module, "_months_view", boom)
    assert client.get("/months").status_code == 200


# ─── Who may ask ────────────────────────────────────────────────────────────

def test_the_api_refuses_whoever_is_not_signed_in():
    limiter.reset()
    r = app.test_client().get("/api/screens/months")
    assert r.status_code == 401
    assert "error" in r.get_json()


def test_a_failed_query_is_a_failure_and_not_an_empty_page(client, monkeypatch):
    """A family with two years of history must not be told "no data"."""
    # The class the app registered its handler for. `test_receipt_scan_ceiling`
    # reloads the data module, and after it `db.DataUnavailable` is a new class
    # the handler doesn't know: only in a test run, never in a running app.
    registered = next(c for c in app.error_handler_spec[None][None] if c.__name__ == "DataUnavailable")

    def down(fid):
        raise registered("down")
    monkeypatch.setattr(app_module.db, "get_months_archive", down)
    r = client.get("/api/screens/months")
    assert r.status_code == 503
    assert "error" in r.get_json() and "empty" not in r.get_json()


# ─── The same numbers as the page built on the server ───────────────────────

def _old_rows(html):
    """The archive table of the old page: per row, the month's link, the four
    amounts, and the balance's class."""
    rows = []
    for tr in re.findall(r'<tr class="month-row[^"]*"\s+data-href="[^"]*">.*?</tr>', html, re.S):
        href = re.search(r'data-href="([^"]*)"', tr).group(1).replace("&amp;", "&")
        cells = [re.sub(r"\s+", "", c) for c in re.findall(r'<td class="n [^"]*">(.*?)</td>', tr, re.S)]
        rows.append({"href": href, "name": re.search(r"<a [^>]*>(.*?)</a>", tr, re.S).group(1).strip(),
                     "current": "is-current" in tr, "cells": cells,
                     "deficit": 'class="n bal deficit"' in tr})
    return rows


def test_every_row_of_the_archive_matches_the_old_page(client):
    old = _old_rows(client.get("/months").get_data(as_text=True))
    assert len(old) == len(_ARCHIVE)

    new = [m for y in _api(client)["years"] for m in y["months"]]
    assert [{"href": m["href"], "name": m["name"], "current": m["current"],
             "cells": [m["income"], m["expense"], m["savings"], m["balance"]],
             "deficit": m["deficit"]} for m in new] == old


def test_half_a_shekel_rounds_the_way_the_template_rounds(client):
    """`2.5 | round` is 2 in the template (Python rounds half to even), and 3 in
    a browser. Formatting on the server is what keeps the two pages equal."""
    september = [m for y in _api(client)["years"] for m in y["months"] if m["month"] == 9][0]
    assert september["income"] == "2"
    assert september["balance"] == "+2"


def test_the_years_are_newest_first_and_only_the_current_one_is_open(client, monkeypatch):
    from datetime import date, datetime
    monkeypatch.setattr(app_module.clock, "now", lambda: datetime(2026, 11, 28, 12, 0))
    monkeypatch.setattr(app_module.clock, "today", lambda: date(2026, 11, 28))
    years = _api(client)["years"]
    assert [(y["year"], y["open"]) for y in years] == [(2026, True), (2025, False)]
    assert [m["month"] for m in years[0]["months"]] == [11, 10, 9, 8], "the archive's own order"

    html = client.get("/months").get_data(as_text=True)
    assert len(re.findall(r'<details class="year-block chart-card" open>', html)) == 1


def test_the_verdict_line_says_what_the_old_page_said(client, monkeypatch):
    verdict = {"month_name": "אוקטובר", "expense": 21340, "avg": 10500, "count": 2,
               "pct": 103, "direction": "up"}
    monkeypatch.setattr(app_module.db, "trend_verdict", lambda trend: dict(verdict))
    got = _api(client)["verdict"]
    assert got == {"month_name": "אוקטובר", "expense": "21,340", "avg": "10,500",
                   "of": "ממוצע חודשיים", "pct": 103, "direction": "up"}

    line = re.search(r'<p class="trend-verdict">(.*?)</p>', client.get("/months").get_data(as_text=True), re.S)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", line.group(1))).strip()
    assert text == f'{got["month_name"]}: ₪{got["expense"]} · {got["of"]}: ₪{got["avg"]} · {got["pct"]}% יותר'


def test_no_verdict_is_no_line(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "trend_verdict", lambda trend: None)
    assert _api(client)["verdict"] is None


@pytest.mark.parametrize("month,title", [(11, "2026 עד עכשיו"), (1, "סיכום 2025")])
def test_the_year_card_reads_as_on_the_old_page(client, monkeypatch, month, title):
    from datetime import date, datetime
    monkeypatch.setattr(app_module.clock, "now", lambda: datetime(2026, month, 15, 12, 0))
    monkeypatch.setattr(app_module.clock, "today", lambda: date(2026, month, 15))
    recap = _api(client)["recap"]
    assert recap["title"] == title

    html = client.get("/months").get_data(as_text=True)
    assert f'data-year-recap="{recap["year"]}"' in html
    assert f'<span class="year-card-title">{recap["title"]}</span>' in html
    assert f'<span class="year-card-sub">{recap["sub"]}</span>' in html


def test_the_charts_get_exactly_what_the_page_put_in_its_data_island(client):
    import json
    html = client.get("/months").get_data(as_text=True)
    island = json.loads(re.search(r'<script type="application/json" id="sf-view-data">(.*?)</script>',
                                  html, re.S).group(1))
    api = _api(client)
    assert api["trend"] == island["trend"]
    assert api["cat_trend"] == island["cat_trend"]
    assert api["members"] == island["members"]


def test_a_family_with_no_history_is_told_so(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "get_months_archive", lambda fid: [])
    api = _api(client)
    assert api["empty"] is True and api["years"] == []


# ─── The fixed page is fixed ────────────────────────────────────────────────

_VIEW = (_ROOT / "frontend/templates/months_view.html").read_text(encoding="utf-8")
_VIEW_NO_COMMENTS = re.sub(r"\{#.*?#\}", "", _VIEW, flags=re.S)


def test_nothing_in_the_fixed_page_belongs_to_a_family():
    """Every `{{ … }}` in it is an address or a drawing: the same for everyone.
    One variable from the route here, and the page can't be served as a file."""
    for expr in re.findall(r"\{\{(.*?)\}\}", _VIEW_NO_COMMENTS, re.S):
        assert re.match(r"\s*(url_for|empty_illus)\(", expr), f"data inside the fixed page: {expr.strip()}"
    for tag in re.findall(r"\{%\s*(\w+)", _VIEW_NO_COMMENTS):
        assert tag in ("extends", "block", "endblock", "include"), f"logic inside the fixed page: {tag}"


def test_the_fixed_page_carries_no_amount_and_no_month(client, monkeypatch):
    monkeypatch.setenv("SF_API_SCREENS", "months")
    main = re.search(r"<main.*?</main>", client.get("/months").get_data(as_text=True), re.S).group(0)
    assert "₪" not in main
    assert "נובמבר" not in main and "סופר" not in main


def test_what_the_script_and_the_styles_hold_on_to_is_in_both_pages():
    """The two pages are drawn by one script and one stylesheet while both exist."""
    old = (_ROOT / "frontend/templates/months.html").read_text(encoding="utf-8")
    for hook in ('id="compareChart"', 'id="categoryChart"', 'id="memberTrendChart"', 'id="cat-trend"',
                 'id="catTrendStats"', 'id="memberTrendAvgs"', 'id="monthsArchive"',
                 'class="range-chips"', 'class="tx-cat-chips cat-trend-chips"',
                 'class="months-table"', 'class="year-block chart-card"',
                 'class="year-card months-year-card"', 'data-add-type="expense"',
                 '{% include "_year_recap.html" %}', "js/chart-setup.js", "js/months.js"):
        assert hook in old, f"control: {hook} left the old page"
        assert hook in _VIEW, f"{hook} is missing from the page that loads its own data"


def test_the_script_writes_text_and_never_html_from_the_api():
    """A category's name is the user's text. On the old page Jinja escaped it;
    here the script must."""
    js = (_ROOT / "frontend/static/js/months.js").read_text(encoding="utf-8")
    loader = js[js.index("/* ═══ Where the data comes from ═══"):]
    assert "innerHTML" not in loader and "insertAdjacentHTML" not in loader
    assert "sfMonthsCharts(view);" in loader
    assert loader.index("byId('monthsBody').hidden = false;") < loader.index("sfMonthsCharts(view);"), \
        "a chart drawn on a hidden canvas is 0×0"


def test_a_failed_load_offers_another_try_and_shows_no_empty_state():
    js = (_ROOT / "frontend/static/js/months.js").read_text(encoding="utf-8")
    assert ".catch(failed);" in js
    assert "byId('monthsRetry').addEventListener('click', load);" in js
    failed = js[js.index("function failed()"):js.index("function load()")]
    assert "monthsEmpty" not in failed
