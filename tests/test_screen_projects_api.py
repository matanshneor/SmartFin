"""The projects list that loads its own data: the second screen of the
frontend/backend split (see `test_screen_months_api.py` for the first).

The list is sentences more than numbers: "יצאו ₪8,200 מתוך ₪12,000", "חריגה של
₪300", "נחסכו ₪4,000 עד עכשיו". Which sentence a project gets follows its kind
and whether it has a target, and that was decided inside the template. Here it
is checked that `/api/screens/projects` reaches the same decision, for every
kind of project, by reading both pages and comparing what a person would read.
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


def _p(pid, name, **over):
    base = {"id": pid, "name": name, "icon": "✈️", "description": None, "budget_target": None,
            "spent": 0, "saved": 0, "income": 0, "is_personal": False,
            "track_expense": True, "track_income": True, "track_savings": False}
    base.update(over)
    return base


def _savings(pid, name, **over):
    return _p(pid, name, track_expense=False, track_income=False, track_savings=True, **over)


# One of every kind the template tells apart
_ACTIVE = [
    _p("a", "חופשה ביוון", budget_target=12000, spent=8200.5, description="שבוע באתונה"),
    _p("b", "שיפוץ <b>מטבח</b>", budget_target=5000, spent=5300.49, is_personal=True),
    _savings("c", "קרן לרכב", budget_target=40000, saved=10000),
    _savings("d", "חיסכון לילדים", saved=4000.5, icon=None),
    _p("e", "אירוע", spent=950),
    _p("f", "עוד לא התחיל"),
    _p("g", "חצי אחוז", budget_target=200, spent=1),
]
_LAST = {"a": "2026-11-21", "c": "2026-11-28", "e": "2026-08-01"}
_FINISHED = [
    _p("x", "טיול שהסתיים", spent=55363, income=10000),
    _p("y", "מכירת רכב", spent=1000, income=4000.4),
    _p("z", "התאזן", spent=500, income=500),
    _p("w", "ריק"),
    _savings("v", "חיסכון שהסתיים", saved=7000),
    _savings("u", "חיסכון ריק"),
]


@pytest.fixture
def client(monkeypatch):
    from datetime import date, datetime
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.delenv("SF_API_SCREENS", raising=False)
    monkeypatch.delenv("SF_API_SCREENS_PREVIEW", raising=False)
    monkeypatch.setattr(app_module.clock, "now", lambda: datetime(2026, 11, 28, 12, 0))
    monkeypatch.setattr(app_module.clock, "today", lambda: date(2026, 11, 28))
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    state = {"active": _ACTIVE, "finished": _FINISHED, "last": _LAST}

    def no_rpc(*a, **k):
        raise db.DataUnavailable("no rpc in this test")
    monkeypatch.setattr(app_module.db, "fetch_projects_page", no_rpc)
    monkeypatch.setattr(app_module.db, "get_projects",
                        lambda fid, uid, archived=False: [dict(p) for p in state["finished" if archived else "active"]])
    monkeypatch.setattr(app_module.db, "project_last_activity", lambda fid: dict(state["last"]))
    monkeypatch.setattr(app_module.db, "get_family_members", lambda fid: [{"id": "u0", "name": "מתן"}])
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = "u0"
        sess["family_id"] = _FAM
    c.state = state
    return c


def _api(client):
    r = client.get("/api/screens/projects")
    assert r.status_code == 200
    return r.get_json()


def _read(fragment: str) -> str:
    """What a person reads in a piece of HTML: no tags, no spacing."""
    return re.sub(r"\s+", "", html_lib.unescape(re.sub(r"<[^>]+>", "", fragment)))


def _plain(text: str) -> str:
    """The API's own text, compared as it is: a name like `<b>x</b>` is a name."""
    return re.sub(r"\s+", "", text)


def _old(client):
    return client.get("/projects").get_data(as_text=True)


# ─── The tiles ──────────────────────────────────────────────────────────────

def _old_tiles(page):
    return re.findall(r'<div class="project-tile tint-.*?</div>', page, re.S)


def _as_read(tile: dict) -> str:
    """The tile as `projects-list.js` writes it, in the template's order."""
    n = tile["numbers"]
    parts = [tile["icon"], tile["name"], f'{tile["pct"]}%' if tile["pct"] is not None else "",
             tile["description"] or "", f'{n["lead"]} {n["amount"]} {n["tail"]}' if n else "",
             tile["over_tag"] or "", "אישי" if tile["personal"] else "",
             "חיסכון" if tile["savings"] else "", tile["last"] or "", "＋"]
    return _plain("".join(parts))


def test_every_tile_reads_as_on_the_old_page(client):
    old = _old_tiles(_old(client))
    new = _api(client)["tiles"]
    assert len(old) == len(new) == len(_ACTIVE)
    assert [_as_read(t) for t in new] == [_read(t) for t in old]


def test_the_order_the_tints_and_the_overrun_mark_match(client):
    old = _old_tiles(_old(client))
    for tile, markup in zip(_api(client)["tiles"], old):
        head = re.match(r'<div class="([^"]*)" data-id="([^"]*)"', markup)
        classes = head.group(1).split()
        assert head.group(2) == tile["id"]
        assert f'tint-{tile["tint"]}' in classes
        assert ("is-over" in classes) == tile["over"]
        assert f'href="{tile["href"]}"' in markup
        assert f'data-add-to-project="{tile["id"]}"' in markup
        assert html_lib.unescape(re.search(r'aria-label="([^"]*)"', markup).group(1)) == tile["add_label"]


def test_the_progress_bar_is_as_wide_and_as_red_as_on_the_old_page(client):
    for tile, markup in zip(_api(client)["tiles"], _old_tiles(_old(client))):
        bar = re.search(r'<span class="project-tile-bar"[^>]*><span class="([^"]*)" style="width: (\d+)%;">', markup)
        if tile["bar"] is None:
            assert bar is None, f'{tile["name"]}: a bar on the old page and none in the API'
        else:
            assert (bar.group(1), int(bar.group(2))) == (tile["bar"]["kind"], tile["bar"]["width"])


def test_an_overrun_is_capped_at_a_full_bar_and_half_a_percent_rounds_to_even(client):
    tiles = {t["id"]: t for t in _api(client)["tiles"]}
    assert tiles["b"]["pct"] == 106 and tiles["b"]["bar"] == {"width": 100, "kind": "danger"}
    assert tiles["b"]["over_tag"] == "חריגה של ₪300"
    assert tiles["g"]["pct"] == 0, "0.5% rounds to 0 in the template; a browser would say 1"
    assert tiles["f"]["numbers"] is None and tiles["f"]["bar"] is None


def test_the_most_recent_project_comes_first_in_both(client):
    assert [t["id"] for t in _api(client)["tiles"]][:3] == ["c", "a", "e"]


# ─── The hero ───────────────────────────────────────────────────────────────

def _old_hero(page):
    return re.search(r'<header class="page-hero">(.*?)</header>', page, re.S).group(1)


def test_several_projects_show_how_many_and_no_money(client):
    hero = _api(client)["hero"]
    assert hero == {"new_button": True, "count": "7 פרויקטים פעילים", "summary": None}
    old = _old_hero(_old(client))
    assert f'<p class="projects-count">{hero["count"]}</p>' in old
    assert 'id="newProjectBtn"' in old and "projects-summary" not in old


@pytest.mark.parametrize("project", [_ACTIVE[0], _ACTIVE[1], _ACTIVE[2], _ACTIVE[3], _ACTIVE[4], _ACTIVE[5]],
                         ids=["under-budget", "over-budget", "savings-goal", "savings-no-goal",
                              "no-budget", "nothing-yet"])
def test_one_project_shows_its_own_numbers_as_on_the_old_page(client, project):
    client.state["active"] = [project]
    api = _api(client)
    hero, old = api["hero"], _old_hero(_old(client))
    assert api["single_id"] == project["id"]
    assert f'data-project-id="{project["id"]}"' in _old(client)
    assert hero["count"] is None

    block = re.search(r'<div class="projects-summary">(.*?)</div>', old, re.S)
    if hero["summary"] is None:
        assert block is None
        return
    summary = hero["summary"]
    assert _read(block.group(1)) == _plain(summary["name"] + summary["main"] + (summary["sub"] or ""))
    assert ("projects-summary-main is-over" in block.group(1)) == summary["over"]
    assert ("projects-summary-sub" in block.group(1)) == (summary["sub"] is not None)


def test_no_active_project_is_an_invitation_and_no_new_button_up_top(client):
    client.state["active"] = []
    api = _api(client)
    assert api["tiles"] == [] and api["single_id"] is None
    assert api["hero"] == {"new_button": False, "count": None, "summary": None}
    old = _old(client)
    assert "projects-new-btn" not in _old_hero(old)
    assert "פרויקט חדש" in old and "הפרויקט הראשון שלכם" not in old, "there are finished ones"


# ─── Finished projects ──────────────────────────────────────────────────────

def test_every_finished_project_ends_the_way_the_old_page_said(client):
    old = re.findall(r'<a class="finished-row".*?</a>', _old(client), re.S)
    new = _api(client)["finished"]
    assert len(old) == len(new) == len(_FINISHED)
    for row, markup in zip(new, old):
        assert _read(markup) == _plain(row["icon"] + row["name"] + (row["sum"] or ""))
        assert f'href="{row["href"]}"' in markup
        assert ("finished-row-sum" in markup) == (row["sum"] is not None)
    assert [r["sum"] for r in new] == ["עלות ₪45,363", "+₪3,000", "₪0", None, "נחסכו ₪7,000", None]


# ─── The switch, and who may ask ────────────────────────────────────────────

def test_the_switch_decides_which_page_is_served(client, monkeypatch):
    assert 'id="projectsView"' not in _old(client)
    monkeypatch.setenv("SF_API_SCREENS", "projects")
    page = _old(client)
    assert 'id="projectsView"' in page
    with app.test_request_context("/"):
        assert app_module._api_screen("months") is False, "one screen, not all"


def test_the_fixed_page_fetches_no_project(client, monkeypatch):
    monkeypatch.setenv("SF_API_SCREENS", "projects")

    def boom(*a, **k):
        raise AssertionError("the fixed page fetched the projects")
    monkeypatch.setattr(app_module, "_projects_view", boom)
    page = client.get("/projects").get_data(as_text=True)
    region = re.search(r'<header class="page-hero">.*?</main>', page, re.S).group(0)
    assert "₪" not in region and "יוון" not in region


def test_the_api_refuses_whoever_is_not_signed_in():
    limiter.reset()
    assert app.test_client().get("/api/screens/projects").status_code == 401


def test_a_failed_query_is_a_failure_and_not_an_invitation_to_start(client, monkeypatch):
    registered = next(c for c in app.error_handler_spec[None][None] if c.__name__ == "DataUnavailable")

    def down(*a, **k):
        raise registered("down")
    monkeypatch.setattr(app_module.db, "get_projects", down)
    r = client.get("/api/screens/projects")
    assert r.status_code == 503 and "tiles" not in r.get_json()


# ─── The fixed page, its script and the refresh ─────────────────────────────

_VIEW = (_ROOT / "frontend/templates/projects_view.html").read_text(encoding="utf-8")
_JS = (_ROOT / "frontend/static/js/projects-list.js").read_text(encoding="utf-8")


def test_nothing_in_the_fixed_page_belongs_to_a_family():
    """Addresses, drawings, and one list written in the page itself (the four
    ideas for a first project): the same for everyone."""
    text = re.sub(r"\{#.*?#\}", "", _VIEW, flags=re.S)
    for expr in re.findall(r"\{\{(.*?)\}\}", text, re.S):
        assert re.match(r"\s*(url_for|ui_icon)\(|\s*(icon|name|loop\.index0 % 4)\s*$", expr), \
            f"data inside the fixed page: {expr.strip()}"
    for tag, rest in re.findall(r"\{%\s*(\w+)(.*?)%\}", text, re.S):
        assert tag in ("extends", "block", "endblock", "endfor") or (tag == "for" and " in [(" in rest), \
            f"logic inside the fixed page: {tag}{rest}"


def test_what_the_styles_and_the_add_window_hold_on_to_is_in_both_pages():
    old = (_ROOT / "frontend/templates/projects.html").read_text(encoding="utf-8")
    for hook in ('id="activeProjects"', 'id="finishedProjects"', 'id="projectsList"', 'id="sfPageProject"',
                 'id="newProjectBtn"', 'class="project-tile-foot"', 'class="project-tile-tags"',
                 'class="finished-row"', 'class="hero-note"', "data-soft-reload", "project-idea tint-"):
        assert hook in _VIEW, f"{hook} is missing from the page that loads its own data"
    for hook in ('id="activeProjects"', 'id="finishedProjects"', 'id="sfPageProject"', "data-soft-reload"):
        assert hook in old, f"control: {hook} left the old page"
    assert "dataset.addToProject = p.id" in _JS, "the + on a tile opens the add window on its project"


def test_the_script_writes_text_and_never_html_from_the_api():
    code = re.sub(r"/\*.*?\*/", "", _JS, flags=re.S)
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code


def test_after_a_save_the_page_asks_its_api_and_not_for_itself():
    """`softReload` fetched the page's HTML and swapped it in. The fixed page's
    HTML never changes, so that would swap in an empty list."""
    core = (_ROOT / "frontend/static/js/core.js").read_text(encoding="utf-8")
    reload = core[core.index("window.softReload = function"):core.index("window.softNavigate = function")]
    assert "window.sfViews[view.id]" in reload
    assert reload.index("window.sfViews[view.id]") < reload.index("sfFetchPage(window.location.href)")
    assert ".then(function () {}, fullReload)" in reload, "a failed redraw falls back to a full load"
    assert "window.sfViews[VIEW] = function" in _JS


def test_arriving_from_another_tab_fills_the_empty_page_once():
    assert "window.addEventListener('sf:refreshed', function () { load(false); });" in _JS
    guard = _JS[_JS.index("function load(opening)"):_JS.index("// After a save")]
    assert "holder.dataset.filled" in guard, "the event this script raises must not make it load again"


def test_hidden_holds_inside_a_view():
    """`.projects-new-btn` sets `display`, and that beats the `hidden` attribute:
    a family with no projects saw the button anyway."""
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    rule = css[css.index("[data-view-url] [hidden],"):]
    assert ".projects-new-btn[hidden]" in rule[:200] and "display: none !important" in rule[:200]


def test_the_cards_of_a_view_enter_when_its_data_arrives():
    motion = (_ROOT / "frontend/static/js/motion.js").read_text(encoding="utf-8")
    assert "window.addEventListener('sf:view-entered'" in motion
    assert "!el.closest(WAITING)" in motion, "a view still waiting for its data is left alone"
    for script in (_JS, (_ROOT / "frontend/static/js/months.js").read_text(encoding="utf-8")):
        assert "new CustomEvent('sf:view-entered', { detail: { root: holder } })" in script
