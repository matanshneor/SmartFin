"""The project's edit screen that loads its own data: the fourth screen of the
frontend/backend split (see `test_screen_project_api.py` for the project's page).

A form more than a page: what matters is that every field starts with the value
the server-built form had, that the sentences around it say the same (what
the amount is called, who sees the project, what deleting it takes with it),
and that the editing script starts only once the form is filled, so "save" is
not woken by the filling itself.
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
_ME, _OR = "u0", "u1"


def _tx(n, created_by=_ME):
    return {"id": f"t{n}", "type": "expense", "amount": 10.0, "date": "2026-11-01", "created_by": created_by}


def _project(**over):
    p = {"id": _PID, "name": "חופשה <i>ביוון</i>", "description": "שבוע \"באתונה\"", "icon": "✈️",
         "is_personal": False, "owner_id": None, "created_by": _ME,
         "track_expense": True, "track_income": True, "track_savings": False, "archived": False,
         "budget_target": 12000.0, "spent": 1200.5, "income": 300.0, "savings": 0.0,
         "transactions": [_tx(1), _tx(2, _OR), _tx(3, _OR)]}
    p.update(over)
    return p


_KINDS = {
    "shared-mine": {},
    "shared-not-mine": dict(created_by=_OR),
    "personal": dict(is_personal=True, owner_id=_ME, icon=None, description=None),
    "savings-empty": dict(track_expense=False, track_income=False, track_savings=True, budget_target=5000.5,
                          spent=0.0, income=0.0, savings=0.0, transactions=[]),
    "one-transaction-no-target": dict(budget_target=None, transactions=[_tx(1)], income=0.0),
    "everything": dict(track_savings=True, savings=50.0, budget_target=None),
}


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.delenv("SF_API_SCREENS", raising=False)
    monkeypatch.delenv("SF_API_SCREENS_PREVIEW", raising=False)
    state = {"project": _project()}
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
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


@pytest.fixture(params=list(_KINDS), ids=list(_KINDS))
def kind(request, client):
    client.state["project"] = _project(**_KINDS[request.param])
    return client


def _api(client):
    r = client.get(f"/api/screens/project/{_PID}/edit")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _old(client):
    r = client.get(f"/projects/{_PID}/edit")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _read(fragment: str) -> str:
    return re.sub(r"\s+", "", html_lib.unescape(re.sub(r"<[^>]+>", "", fragment)))


def _plain(*texts) -> str:
    return re.sub(r"\s+", "", "".join(t for t in texts if t))


def _attr(page, element_id, attr="value"):
    tag = re.search(rf'<[^>]*id="{element_id}"[^>]*>', page, re.S).group(0)
    m = re.search(rf'\s{attr}="([^"]*)"', tag)
    return html_lib.unescape(m.group(1)) if m else None


# ─── The form ───────────────────────────────────────────────────────────────

def test_every_field_starts_with_the_old_forms_value(kind):
    f, old = _api(kind)["fields"], _old(kind)
    assert _attr(old, "editProjectIcon") == f["icon"]
    assert _attr(old, "editProjectName") == f["name"]
    assert _attr(old, "editProjectBudget") == f["budget"]
    desc = re.search(r'id="editProjectDescription"[^>]*>(.*?)</textarea>', old, re.S).group(1)
    assert html_lib.unescape(desc) == f["description"]
    for name in ("Expense", "Income", "Savings"):
        tag = re.search(rf'<input[^>]*id="editProjectTrack{name}"[^>]*>', old).group(0)
        assert ("checked" in tag) == f[f"track_{name.lower()}"]


def test_the_kind_its_lock_and_what_the_amount_is_called(kind):
    api, old = _api(kind), _old(kind)
    for k in ("expense", "savings"):
        card = re.search(rf'<button[^>]*data-kind="{k}"[^>]*>', old).group(0)
        assert f'aria-checked="{"true" if api["kind"] == k else "false"}"' in card
        assert (" disabled" in card) == api["kind_locked"]
    assert ("אי אפשר להחליף בין הוצאות לחיסכון" in old) == api["kind_locked"]
    label = re.search(r'id="editProjectBudgetLabel">(.*?)</label>', old).group(1)
    assert label == ("יעד חיסכון" if api["kind"] == "savings" else "תקציב") + " (אופציונלי)"
    hint = re.search(r'id="budgetTargetHint"[^>]*?data-expense="([^"]*)" data-savings="([^"]*)">(.*?)</p>',
                     old, re.S)
    assert hint.group(3).strip() == hint.group(2 if api["kind"] == "savings" else 1), \
        "the hint the script writes is the data-* of its kind"


def test_who_sees_the_project_reads_as_on_the_old_page(kind):
    own, old = _api(kind)["ownership"], _old(kind)
    block = old[old.index("<h2 class=\"chart-title\">בעלות</h2>"):old.index('id="ownershipPending"')]
    label = re.search(r'<p class="group-row-label">(.*?)</p>', block, re.S).group(1)
    assert _read(label) == _plain(own["label"]) and ("ui-icon" in label) == own["personal"]
    hint = re.search(r'<p class="group-row-hint">(.*?)</p>', block, re.S).group(1)
    assert _read(hint) == _plain(own["hint"])
    assert ('id="shareProjectBtn"' in block) == (own["button"] == "share")
    assert ('id="unshareProjectBtn"' in block) == (own["button"] == "unshare")
    if own["button"] == "unshare":
        assert _attr(block, "unshareProjectBtn", "data-others-note") == own["others_note"]


def test_the_others_note_names_who_recorded_what(client):
    assert _api(client)["ownership"]["others_note"] == "2 עסקאות נרשמו כאן על ידי אור"


def test_the_category_tabs_are_the_tracked_types(kind):
    tabs, old = _api(kind)["tabs"], _old(kind)
    buttons = re.findall(r'<button type="button" class="toggle-btn ?(active)?" data-cat-tab="(\w+)"\s*'
                         r'role="tab" aria-selected="(\w+)">(.*?)</button>', old)
    assert [(b[1], b[3]) for b in buttons] == [(t["type"], t["label"]) for t in tabs]
    assert [b[0] == "active" for b in buttons] == [i == 0 for i in range(len(tabs))]
    assert f"grid-template-columns: repeat({len(tabs)}, 1fr)" in old


def test_deleting_says_what_goes_with_it(kind):
    d, old = _api(kind)["delete"], _old(kind)
    danger = old[old.index('class="project-danger"'):]
    hint = re.search(r'<p class="group-row-hint">(.*?)</p>', danger, re.S).group(1)
    assert _read(hint) == _plain(d["hint"])
    warning = re.search(r'<p class="delete-account-warning">(.*?)</p>', danger, re.S).group(1)
    assert _read(warning) == _plain(d["warning"])
    assert _attr(old, "confirmDeleteProjectBtn", "data-name") == _api(kind)["name"]


def test_the_delete_sentences(client):
    assert _api(client)["delete"] == {
        "hint": "לפרויקט יש 3 עסקאות",
        "warning": "הפרויקט יימחק יחד עם 3 העסקאות שבו (הוצאות ₪1,200.50, הכנסות ₪300). "
                   "ההוצאות של הבית לא ישתנו — כסף של פרויקט ממילא לא נספר בהן."}


def test_the_links_back_and_the_title(client):
    api, old = _api(client), _old(client)
    assert api["detail_href"] == f"/projects/{_PID}"
    assert old.count(f'href="{api["detail_href"]}"') == 2
    assert f"<title>{html_lib.escape(api['title'], quote=False)}</title>" in old


# ─── A project that isn't there, the switch, and who may ask ────────────────

def test_a_missing_project_sends_back_to_the_list(client):
    client.state["project"] = None
    r = client.get(f"/api/screens/project/{_PID}/edit")
    assert r.status_code == 404 and r.get_json()["redirect"] == "/projects"
    assert r.get_json()["error"] == "הפרויקט הזה כבר לא קיים"
    # Carried by the page (`sfToastAfterReload`), not left in the session: a
    # request running alongside could write the cookie back without it
    with client.session_transaction() as sess:
        assert "sf_notice" not in sess
    assert client.get(f"/projects/{_PID}/edit").status_code == 302


def test_the_switch_decides_which_page_is_served(client, monkeypatch):
    assert 'id="projectEditView"' not in _old(client)
    monkeypatch.setenv("SF_API_SCREENS", "project_edit")
    assert f'data-view-url="/api/screens/project/{_PID}/edit"' in _old(client)
    monkeypatch.setenv("SF_API_SCREENS", "project")
    assert 'id="projectEditView"' not in _old(client), "the project's page is a screen of its own"


def test_the_fixed_page_fetches_no_project(client, monkeypatch):
    monkeypatch.setenv("SF_API_SCREENS", "project_edit")

    def boom(*a, **k):
        raise AssertionError("the fixed page fetched the project")
    monkeypatch.setattr(app_module, "_project_edit_view", boom)
    page = _old(client)
    region = re.search(r'<header class="page-hero">.*?</main>', page, re.S).group(0)
    assert "יוון" not in region and "1,200" not in region and "על ידי" not in region


def test_the_api_refuses_whoever_is_not_signed_in():
    limiter.reset()
    assert app.test_client().get(f"/api/screens/project/{_PID}/edit").status_code == 401


# ─── The fixed page and its script ──────────────────────────────────────────

_VIEW = (_ROOT / "frontend/templates/project_edit_view.html").read_text(encoding="utf-8")
_OLD = (_ROOT / "frontend/templates/project_edit.html").read_text(encoding="utf-8")
_JS = (_ROOT / "frontend/static/js/project-edit.js").read_text(encoding="utf-8")


def test_nothing_in_the_fixed_page_belongs_to_a_project():
    text = re.sub(r"\{#.*?#\}", "", _VIEW, flags=re.S)
    for expr in re.findall(r"\{\{(.*?)\}\}", text, re.S):
        assert re.match(r"\s*(url_for|ui_icon)\(|\s*view_url\s*$", expr), f"data inside the fixed page: {expr.strip()}"
    for tag, rest in re.findall(r"\{%\s*(\w+)(.*?)%\}", text, re.S):
        assert tag in ("extends", "block", "endblock"), f"logic inside the fixed page: {tag}{rest}"


def test_every_id_the_editing_script_holds_on_to_is_in_both_pages():
    ids = set(re.findall(r"getElementById\('([A-Za-z]+)'\)", _JS))
    for i in ids:
        if i.startswith("projectEdit") or i in ("projectBackLink", "projectCancelLink", "tplLock", "tplCatTab",
                                               "ownershipLabel", "ownershipHint", "deleteProjectHint",
                                               "deleteProjectWarning", "editProjectKindLocked"):
            continue
        assert f'id="{i}"' in _VIEW, f"#{i} is missing from the page that loads its own data"
        assert f'id="{i}"' in _OLD or i in ("shareProjectBtn", "unshareProjectBtn"), f"control: #{i}"
    for hook in ('class="kind-card"', 'data-expense=', 'data-savings=', 'class="project-save-bar"',
                 'form="editProjectForm"', "data-thousands", "no-fab no-nav", 'class="project-danger"'):
        assert hook in _VIEW, hook


def test_the_editing_starts_only_once_the_form_is_filled():
    """It snapshots the fields as it starts ("nothing changed"): started on an
    empty form, filling it would count as a change and wake "save"."""
    loader = _JS[_JS.index("const holder = document.getElementById('projectEditView');"):]
    assert loader.index("fill(view);") < loader.index("sfStartProjectEdit(view.detail_href);")
    head = _JS[:_JS.index("/* ═══ Deleting the project")]
    assert "if (!document.getElementById('projectEditView')) sfStartProjectEdit(" in head
    assert "budget.defaultValue = budget.value" in loader and "el.defaultChecked = on" in loader


def test_a_missing_project_is_said_on_the_list():
    loader = _JS[_JS.index("const holder = document.getElementById('projectEditView');"):]
    assert loader.index("window.sfToastAfterReload(d.error)") < loader.index("window.location.replace(d.redirect")


def test_the_page_does_not_redraw_itself_over_unsaved_edits():
    assert "sfViews" not in _JS
    assert "data-soft-reload" not in _VIEW


def test_the_script_writes_text_and_never_html_from_the_api():
    loader = _JS[_JS.index("/* ═══ The page that loads its own data"):]
    code = re.sub(r"/\*.*?\*/", "", loader, flags=re.S)
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code
