"""One project's page that loads its own data: the third screen of the
frontend/backend split (see `test_screen_projects_api.py` for the list).

The page decides a lot in its template: which tiles the hero has (a goal or a
ceiling, "נשאר" or "חריגה"), how wide and how red the bar is, which mark a row
gets (instalments, recurring, "הוזנה ע״י", whose it is, a receipt), which rows
sit under which category. Here it is checked that `/api/screens/project/<id>`
reaches the same decisions, by reading both pages and comparing what a person
would read, and the `data-*` the edit window opens with.
"""
import html as html_lib
import re
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_FAM = "fam-1"
_PID = "11111111-2222-3333-4444-555555555555"
_ME, _OR, _GONE = "u0", "u1", "u9"


def _tx(n, typ, amount, date, cat="אוכל", icon="🍽️", **over):
    base = {"id": f"t{n}", "type": typ, "amount": float(amount), "description": f"עסקה {n}", "date": date,
            "category_id": None, "project_category_id": f"pc-{cat}", "category_name": cat, "category_icon": icon,
            "user_id": _ME, "user_name": "מתן", "workplace": None, "is_recurring": False,
            "recurring_frequency": None, "installments": None, "installment_no": None,
            "recurring_end_date": None, "recurring_parent_id": None, "project_id": _PID,
            "created_by": _ME, "has_receipt": False, "projected": False}
    base.update(over)
    return base


_TXS = [
    _tx(1, "expense", 1200.5, "2026-11-20", description="מלון <b>באתונה</b>", has_receipt=True),
    _tx(2, "expense", 300, "2026-11-18", cat="טיסות", icon="✈️", installments=3, installment_no=2),
    _tx(3, "expense", 80, "2026-11-10", is_recurring=True, recurring_frequency="weekly", user_id=None,
        user_name="משותפת"),
    _tx(4, "income", 5000, "2026-11-05", cat="משכורת", icon="💼", workplace="אינטל", user_id=_OR,
        user_name="אור", created_by=_OR),
    _tx(5, "expense", 45.25, "2026-10-30", description="", recurring_parent_id="t3",
        recurring_frequency="odd", created_by=_GONE),
    _tx(6, "expense", 60, "2026-10-01", cat="אחר", icon="📦", created_by=_OR),
    _tx(7, "savings", 700, "2026-09-12", cat="קרן", icon="🐷", user_id=_OR, user_name="אור"),
]


def _breakdown(txs):
    out = {}
    for typ in ("expense", "income", "savings"):
        cats = {}
        for t in txs:
            if t["type"] == typ:
                c = cats.setdefault(t["category_name"] or "אחר",
                                    {"name": t["category_name"] or "אחר", "icon": t["category_icon"], "total": 0.0})
                c["total"] += t["amount"]
        tot = sum(c["total"] for c in cats.values()) or 1
        items = sorted(cats.values(), key=lambda c: c["total"], reverse=True)
        for c in items:
            c["pct"] = round(c["total"] / tot * 100)
            c["total"] = round(c["total"], 2)
        out[typ] = items
    return out


def _project(txs=None, **over):
    txs = _TXS if txs is None else txs
    p = {"id": _PID, "name": "חופשה <i>ביוון</i>", "description": "שבוע באתונה", "icon": "✈️",
         "is_personal": False, "owner_id": None, "created_by": _ME,
         "track_expense": True, "track_income": True, "track_savings": True, "archived": False,
         "budget_target": 2000.0, "transactions": [dict(t) for t in txs]}
    p.update(over)
    totals = {k: round(sum(t["amount"] for t in p["transactions"] if t["type"] == k), 2)
              for k in ("expense", "income", "savings")}
    p.update(spent=totals["expense"], income=totals["income"], savings=totals["savings"],
             breakdown=_breakdown(p["transactions"]))
    p["remaining"] = round(p["budget_target"] - p["spent"], 2) if p["budget_target"] is not None else None
    return p


@pytest.fixture
def client(monkeypatch):
    from datetime import date, datetime
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.delenv("SF_API_SCREENS", raising=False)
    monkeypatch.delenv("SF_API_SCREENS_PREVIEW", raising=False)
    monkeypatch.setattr(app_module.clock, "now", lambda: datetime(2026, 11, 28, 12, 0))
    monkeypatch.setattr(app_module.clock, "today", lambda: date(2026, 11, 28))
    state = {"project": _project(), "settings": dict(db.DEFAULT_FAMILY_SETTINGS, pinned_projects={_ME: _PID})}
    monkeypatch.setattr(app_module, "family_settings", lambda: state["settings"])
    monkeypatch.setattr(app_module.db, "get_project_detail",
                        lambda pid, fid, uid: (dict(state["project"]) if state["project"] and pid == _PID else None))
    monkeypatch.setattr(app_module.db, "get_family_members",
                        lambda fid: [{"id": _ME, "name": "מתן"}, {"id": _OR, "name": "אור"}])
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = _ME
        sess["family_id"] = _FAM
    c.state = state
    return c


def _api(client):
    r = client.get(f"/api/screens/project/{_PID}")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _old(client):
    r = client.get(f"/projects/{_PID}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _read(fragment: str) -> str:
    """What a person reads in a piece of HTML: no tags, no spacing."""
    return re.sub(r"\s+", "", html_lib.unescape(re.sub(r"<[^>]+>", "", fragment)))


def _plain(*texts) -> str:
    """The API's own text, compared as it is: a name like `<b>x</b>` is a name."""
    return re.sub(r"\s+", "", "".join(t for t in texts if t))


def _hero(page):
    return re.search(r'<header class="page-hero">(.*?)</header>', page, re.S).group(1)


# ─── The hero ───────────────────────────────────────────────────────────────

_KINDS = {
    "expense-under-budget": dict(track_savings=False, budget_target=9000.0),
    "expense-over-budget": dict(track_savings=False),
    "expense-only-no-target": dict(track_income=False, track_savings=False, budget_target=None),
    "savings-goal-missing": dict(track_expense=False, track_income=False, budget_target=1000.0),
    "savings-goal-passed": dict(track_expense=False, track_income=False, budget_target=500.0),
    "everything-no-target": dict(budget_target=None),
    "personal": dict(is_personal=True, owner_id=_ME, icon=None, description=None),
    "nothing-yet": dict(budget_target=None, txs=[]),
}


@pytest.fixture(params=list(_KINDS), ids=list(_KINDS))
def kind(request, client):
    over = dict(_KINDS[request.param])
    client.state["project"] = _project(over.pop("txs", None), **over)
    return client


def test_the_hero_reads_as_on_the_old_page(kind):
    hero, old = _api(kind)["hero"], _hero(_old(kind))
    title = re.search(r'<h1 class="hero-title">(.*?)</h1>', old, re.S).group(1)
    assert _read(title) == _plain(hero["heading"], "אישי" if hero["personal"] else "")
    assert ('class="hero-badge"' in title) == hero["personal"]
    sub = re.search(r'<p class="hero-subtitle">(.*?)</p>', old, re.S).group(1)
    assert _read(sub) == _plain(hero["subtitle"])
    assert f'href="{hero["edit_href"]}"' in old


def test_the_tiles_are_the_same_tiles_in_the_same_columns(kind):
    hero, old = _api(kind)["hero"], _hero(_old(kind))
    cols = re.search(r'grid-template-columns: repeat\((\d+), minmax', old).group(1)
    assert int(cols) == hero["columns"]
    chips = re.findall(r'<div class="kpi-chip">\s*<p class="kpi-label">(.*?)</p>\s*<p class="([^"]*)"'
                       r'(?: data-countup="(\d+)")?>(.*?)</p>', old, re.S)
    assert len(chips) == len(hero["chips"])
    for (label, cls, countup, value), chip in zip(chips, hero["chips"]):
        assert _read(label) == _plain(chip["label"])
        assert cls.split() == ["kpi-value"] + ([chip["kind"]] if chip["kind"] else [])
        assert (int(countup) if countup else None) == chip["countup"]
        assert _read(value) == _plain(chip["value"])


def test_the_net_row_and_the_bar_match(kind):
    hero, old = _api(kind)["hero"], _hero(_old(kind))
    net = re.search(r'<div class="project-net ?([^"]*)">.*?<p class="project-net-value">(.*?)</p>', old, re.S)
    if hero["net"] is None:
        assert net is None
    else:
        assert (net.group(1).strip(), _read(net.group(2))) == (hero["net"]["kind"], _plain(hero["net"]["text"]))
    bar = re.search(r'aria-valuenow="(\d+)".*?aria-label="([^"]*)".*?class="balance-bar-fill ?([^"]*)"\s*'
                    r'style="width: (\d+)%;"', old, re.S)
    if hero["bar"] is None:
        assert bar is None
    else:
        b = hero["bar"]
        assert (int(bar.group(1)), html_lib.unescape(bar.group(2)), bar.group(3).strip(), int(bar.group(4))) \
            == (b["pct"], b["text"], b["kind"], b["width"])


def test_the_pin_follows_the_family_settings_and_leaves_a_finished_project(client):
    assert _api(client)["hero"]["pin"] == {"pinned": True}
    assert 'data-pinned="true"' in _old(client)
    client.state["settings"] = dict(db.DEFAULT_FAMILY_SETTINGS)
    assert _api(client)["hero"]["pin"] == {"pinned": False}
    client.state["project"] = _project(archived=True)
    assert _api(client)["hero"]["pin"] is None and "data-pin-project" not in _old(client)


# ─── The rows ───────────────────────────────────────────────────────────────

_ROW = re.compile(r'<div class="cat-tx-row([^"]*)"(.*?)>(.*?)</div>', re.S)


def _as_read(r) -> str:
    """A row as `project-detail.js` writes it, in the template's order."""
    return _plain(r["day"], r["text"], r["installment"], " · " + r["recurring"] if r["recurring"] else "",
                  " · " + r["entered_by"] if r["entered_by"] else "",
                  r["owner"]["name"] if r["owner"] else "", r["amount"])


def _same_row(markup, row):
    extra, attrs, body = markup
    data = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', attrs))
    for key, value in row["data"].items():
        old = html_lib.unescape(data[key.replace("_", "-")])
        if key == "amount":
            assert float(old) == value
        else:
            assert old == str(value), f"data-{key}"
    assert _read(body) == _as_read(row)
    owner = re.search(r'class="owner-pill owner-pill-sm owner-([^"]+)"', body)
    assert (owner.group(1) if owner else None) == (str(row["owner"]["color"]) if row["owner"] else None)
    receipt = re.search(r'class="receipt-badge" data-href="([^"]*)"', body)
    assert (receipt.group(1) if receipt else None) == row["receipt"]
    return extra


def test_every_transaction_row_reads_and_opens_as_on_the_old_page(client):
    page = _old(client)
    listed = page[page.index('id="projectTxList"'):]
    old = _ROW.findall(listed)
    new = _api(client)["transactions"]
    assert len(old) == len(new) == len(_TXS)
    extras = [_same_row(m, r) for m, r in zip(old, new)]
    assert [e.strip() == "tx-extra" for e in extras] == [i >= 5 for i in range(len(new))]


def test_the_rows_under_each_category_are_the_old_ones(client):
    page = _old(client)
    cards = re.findall(r'<section class="chart-card">\s*<h2 class="chart-title">(.*?)</h2>(.*?)</section>', page, re.S)
    charts = _api(client)["charts"]
    assert [_plain(c["title"]) for c in charts] == [_read(t) for t, _ in cards[:len(charts)]]
    for chart, (_, card) in zip(charts, cards):
        total = re.search(r'<p class="doughnut-total ([a-z]+)">(.*?)</p>', card)
        assert (total.group(1), _read(total.group(2))) == (chart["type"], _plain(chart["total"]))
        items = re.findall(r'<li class="legend-item-wrap">(.*?)</li>', card, re.S)
        assert len(items) == len(chart["items"])
        for item, markup in zip(chart["items"], items):
            head = markup.split('<div role="group"')[0]
            assert f'var(--chart-color-{item["color"]})' in head
            assert ("legend-item clickable" in head) == bool(item["rows"])
            assert _plain(item["icon"], item["name"], f'{item["pct"]}%', item["amount"]) in _read(head)
            rows = _ROW.findall(markup)
            assert len(rows) == len(item["rows"])
            for m, r in zip(rows, item["rows"]):
                _same_row(m, r)


def test_the_marks_a_row_can_carry(client):
    rows = {r["data"]["id"]: r for r in _api(client)["transactions"]}
    in_cats = {r["data"]["id"]: r for c in _api(client)["charts"] for i in c["items"] for r in i["rows"]}
    assert in_cats["t2"]["installment"] == "תשלום 2 מתוך 3"
    assert in_cats["t3"]["recurring"] == "כל שבוע"
    assert in_cats["t5"]["recurring"] == "חודשי", "an unknown frequency reads as monthly"
    assert in_cats["t4"]["text"] == "עסקה 4 · אינטל"
    assert rows["t1"]["amount"] == "-₪1,200.50" and in_cats["t1"]["amount"] == "₪1,200.50"
    assert rows["t4"]["amount"] == "+₪5,000" and rows["t7"]["amount"] == "₪700"
    assert rows["t3"]["owner"] == {"color": "shared", "name": "משותפת"}
    assert rows["t7"]["owner"] is None, "savings aren't attributed by default"
    assert all(r["entered_by"] is None for r in rows.values()), "only in a personal project"


def test_in_a_personal_project_others_rows_say_who_entered_them(client):
    client.state["project"] = _project(is_personal=True, owner_id=_ME)
    rows = {r["data"]["id"]: r for r in _api(client)["transactions"]}
    assert rows["t4"]["entered_by"] == "הוזנה ע״י אור"
    assert rows["t5"]["entered_by"] == "הוזנה ע״י בן משפחה לשעבר"
    assert rows["t1"]["entered_by"] is None
    test_every_transaction_row_reads_and_opens_as_on_the_old_page(client)


def test_whose_disappears_in_a_family_of_one(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "get_family_members", lambda fid: [{"id": _ME, "name": "מתן"}])
    assert all(r["owner"] is None for r in _api(client)["transactions"])
    assert "owner-pill" not in _old(client)


def test_the_frequencies_are_the_templates_own():
    tpl = (_ROOT / "frontend/templates/project_detail.html").read_text(encoding="utf-8")
    block = tpl[tpl.index("{% set freq_labels = {"):tpl.index("} %}", tpl.index("{% set freq_labels = {"))]
    assert dict(re.findall(r"'(\w+)':\s*'([^']+)'", block)) == app_module._FREQ_LABELS


# ─── The banners, the empty project, the bottom card ────────────────────────

def test_a_quiet_project_asks_if_it_is_finished(client):
    client.state["project"] = _project([_tx(1, "expense", 10, "2026-08-20")])
    text = _api(client)["quiet_since"]
    assert text == "לא נוספו כאן עסקאות מאז אוגוסט. לסמן שהפרויקט הסתיים?"
    old = re.search(r'<div class="project-quiet-banner".*?<span>(.*?)</span>', _old(client), re.S).group(1)
    assert _read(old) == _plain(text)
    client.state["project"] = _project([_tx(1, "expense", 10, "2025-08-20")])
    assert "אוגוסט 2025" in _api(client)["quiet_since"]


def test_an_active_project_is_not_quiet_and_a_finished_one_says_so(client):
    assert _api(client)["quiet_since"] is None
    client.state["project"] = _project([_tx(1, "expense", 10, "2026-01-01")], archived=True)
    api, old = _api(client), _old(client)
    assert api["archived"] is True and api["quiet_since"] is None
    assert "project-archived-banner" in old and "project-finish" not in old and "project-quiet-banner" not in old


def test_an_empty_project_invites_the_first_transaction(client):
    client.state["project"] = _project([], budget_target=None)
    api, old = _api(client), _old(client)
    assert api["transactions"] == [] and api["charts"] == []
    assert api["count_title"] == "כל העסקאות (0)" and "כל העסקאות (0)" in old
    sub = re.search(r'<p class="empty-sub">(.*?)</p>', old[old.index("עדיין אין עסקאות"):], re.S).group(1)
    assert _read(sub) == _plain(api["empty_sub"])


def test_the_show_all_button_counts_as_the_old_one(client):
    api, old = _api(client), _old(client)
    assert f'data-more="{api["show_all"]}"' in old
    assert api["count_title"] == f"כל העסקאות ({len(_TXS)})"


# ─── A project that isn't there, the switch, and who may ask ────────────────

def test_a_missing_project_sends_back_to_the_list_with_the_same_notice(client):
    client.state["project"] = None
    r = client.get(f"/api/screens/project/{_PID}")
    assert r.status_code == 404 and r.get_json()["redirect"] == "/projects"
    with client.session_transaction() as sess:
        assert sess["sf_notice"] == "הפרויקט הזה כבר לא קיים"
    old = client.get(f"/projects/{_PID}")
    assert old.status_code == 302 and old.headers["Location"].endswith("/projects")


def test_the_switch_decides_which_page_is_served(client, monkeypatch):
    assert 'id="projectView"' not in _old(client)
    monkeypatch.setenv("SF_API_SCREENS", "project")
    assert f'data-view-url="/api/screens/project/{_PID}"' in _old(client)
    monkeypatch.setenv("SF_API_SCREENS", "projects")
    assert 'id="projectView"' not in _old(client), "the list is a screen of its own"


def test_the_fixed_page_fetches_no_project(client, monkeypatch):
    monkeypatch.setenv("SF_API_SCREENS", "project")

    def boom(*a, **k):
        raise AssertionError("the fixed page fetched the project")
    monkeypatch.setattr(app_module, "_project_detail_view", boom)
    page = client.get(f"/projects/{_PID}").get_data(as_text=True)
    region = re.search(r'<header class="page-hero">.*?</main>', page, re.S).group(0)
    assert "₪" not in region and "יוון" not in region and "עסקה" not in region
    assert "<title>SmartFin – פרויקט</title>" in page


def test_the_api_refuses_whoever_is_not_signed_in():
    limiter.reset()
    assert app.test_client().get(f"/api/screens/project/{_PID}").status_code == 401


def test_a_failed_query_is_a_failure_and_not_an_empty_project(client, monkeypatch):
    registered = next(c for c in app.error_handler_spec[None][None] if c.__name__ == "DataUnavailable")

    def down(*a, **k):
        raise registered("down")
    monkeypatch.setattr(app_module.db, "get_project_detail", down)
    r = client.get(f"/api/screens/project/{_PID}")
    assert r.status_code == 503 and "transactions" not in r.get_json()


# ─── The fixed page and its script ──────────────────────────────────────────

_VIEW = (_ROOT / "frontend/templates/project_detail_view.html").read_text(encoding="utf-8")
_JS = (_ROOT / "frontend/static/js/project-detail.js").read_text(encoding="utf-8")


def test_nothing_in_the_fixed_page_belongs_to_a_project():
    text = re.sub(r"\{#.*?#\}", "", _VIEW, flags=re.S)
    for expr in re.findall(r"\{\{(.*?)\}\}", text, re.S):
        assert re.match(r"\s*(url_for|ui_icon|empty_illus)\(|\s*view_url\s*$", expr), \
            f"data inside the fixed page: {expr.strip()}"
    for tag, rest in re.findall(r"\{%\s*(\w+)(.*?)%\}", text, re.S):
        assert tag in ("extends", "block", "endblock"), f"logic inside the fixed page: {tag}{rest}"


def test_what_the_other_scripts_hold_on_to_is_in_the_fixed_page():
    """`core.js` (pin, finish, "not now"), `transactions.js` (+, editing a row),
    the show-all button and the styles all find the page by these."""
    for hook in ('id="sfPageProject"', 'id="projectTxList"', 'id="showAllProjectTx"', 'class="project-net"',
                 'class="hero-totals"', 'class="balance-bar-track"', 'class="project-archived-banner"',
                 'class="project-quiet-banner"', "data-quiet-later", "project-pin-btn", 'class="cat-tx-row"',
                 'class="legend-item-wrap"', 'class="doughnut-wrap"', "cat-tx-list-standalone", "data-less=",
                 "project-finish", "data-soft-reload"):
        assert hook in _VIEW, f"{hook} is missing from the page that loads its own data"
    for attr in ("dataset.archiveProject", "dataset.pinProject", "dataset.projectQuiet", "dataset.projectId",
                 "dataset.more", "receipt.dataset.href"):
        assert attr in _JS, attr


def test_the_script_writes_text_and_never_html_from_the_api():
    code = re.sub(r"/\*.*?\*/", "", _JS, flags=re.S)
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code


def test_after_a_save_the_page_asks_its_api_and_redraws_its_charts():
    assert "window.sfViews[VIEW] = function" in _JS
    assert "Chart.getChart(ctx)" in _JS and "before.destroy()" in _JS, "a redraw must not leak the old charts"
    assert "new CustomEvent('sf:view-entered', { detail: { root: holder } })" in _JS
    assert "r.status === 404" in _JS and "window.location.replace(d.redirect" in _JS


def test_not_now_is_honoured_on_a_banner_built_after_the_page_opened():
    core = (_ROOT / "frontend/static/js/core.js").read_text(encoding="utf-8")
    quiet = core[core.index("const KEY = 'sf_quiet_later';"):]
    assert "window.addEventListener('sf:view-entered', applyQuiet);" in quiet[:2000]


def test_without_the_chart_library_the_built_cards_say_so():
    setup = (_ROOT / "frontend/static/js/chart-setup.js").read_text(encoding="utf-8")
    assert "unavailable: unavailable" in setup
    assert "forEach(window.sfCharts.unavailable)" in _JS


def test_hidden_holds_for_the_pieces_in_the_hero():
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    rule = css[css.index(".page-hero .stats-nav-btn[hidden]"):][:200]
    assert ".project-net[hidden]" in rule and ".hero-totals[hidden]" in rule and "display: none !important" in rule
