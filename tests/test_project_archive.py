""""Project finished" (Matan, 30.9: round 6, item 3).

A finished project leaves the main list, the add form and settings, and stays
available: the "פרויקטים שהסתיימו" section on the projects page, its own page, and
an old transaction in it that is edited stays in it.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_P_ACTIVE = "aaaaaaaa-0000-0000-0000-000000000001"
_P_DONE   = "aaaaaaaa-0000-0000-0000-000000000002"


def _projects():
    base = {"family_id": _FAM, "owner_id": None, "track_expense": True, "track_income": False,
            "track_savings": False, "budget_target": None, "description": None, "icon": "🔨",
            "created_at": "2026-01-01"}
    return [{**base, "id": _P_ACTIVE, "name": "טיול", "archived": False},
            {**base, "id": _P_DONE, "name": "שיפוץ", "archived": True}]


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase(projects=_projects(), transactions=[])
    monkeypatch.setattr(db, "get_client", lambda: f)
    return f


def test_the_active_list_leaves_out_finished_projects(fake):
    assert [p["name"] for p in db.get_projects(_FAM, _ME)] == ["טיול"]


def test_finished_projects_have_their_own_list(fake):
    done = db.get_projects(_FAM, _ME, archived=True)
    assert [p["name"] for p in done] == ["שיפוץ"] and done[0]["archived"] is True


def test_the_form_list_can_include_them_marked(fake):
    """The add form needs them, for an old transaction being edited. Marked, so they
    aren't offered for a new one."""
    allp = db.get_projects(_FAM, _ME, archived=None)
    assert {(p["name"], p["archived"]) for p in allp} == {("טיול", False), ("שיפוץ", True)}


def test_marking_finished_and_back(fake):
    assert db.set_project_archived(_P_ACTIVE, _FAM, True)
    assert [p["name"] for p in db.get_projects(_FAM, _ME)] == []
    assert db.set_project_archived(_P_ACTIVE, _FAM, False)
    assert [p["name"] for p in db.get_projects(_FAM, _ME)] == ["טיול"]


def test_a_missing_project_is_not_marked(fake):
    assert not db.set_project_archived("aaaaaaaa-0000-0000-0000-00000000ffff", _FAM, True)


# ── The routes ─────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, fake):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield c


def test_the_archive_route(client):
    res = client.put(f"/api/projects/{_P_ACTIVE}/archive", json={"archived": True})
    assert res.status_code == 200
    assert client.get("/api/projects").get_json() == []
    res = client.put(f"/api/projects/{_P_ACTIVE}/archive", json={"archived": "yes"})
    assert res.status_code == 422


def test_the_form_list_route_includes_finished_ones_marked(client):
    body = client.get("/api/projects?include_archived=1").get_json()
    assert {(p["name"], p["archived"]) for p in body} == {("טיול", False), ("שיפוץ", True)}


def test_the_projects_page_has_two_sections():
    """Matan (30.9): the active ones, and below them the finished ones, each section
    on its own, and not a closed section at the bottom. Since 5.10 the active ones
    are coloured boxes, and they are the main part of the page."""
    from pathlib import Path
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/projects.html").read_text(encoding="utf-8")
    assert 'aria-label="פרויקטים פעילים"' in tpl
    assert '<h2 class="projects-section-title">הסתיימו</h2>' in tpl
    assert "<details" not in tpl
    assert tpl.index('id="activeProjects"') < tpl.index('id="finishedProjects"')


def test_the_page_renders_both_lists(client):
    html = client.get("/projects").get_data(as_text=True)
    active = html[html.index('id="activeProjects"'):html.index('id="finishedProjects"')]
    finished = html[html.index('id="finishedProjects"'):]
    assert "טיול" in active and "שיפוץ" not in active
    assert "שיפוץ" in finished


def test_no_active_projects_invites_to_start_one(client, fake):
    """With no active projects: not "there's nothing", but an invitation to start,
    with ideas (Matan, 5.10). When there are finished ones, the box says "פרויקט
    חדש" and not "the first"."""
    client.put(f"/api/projects/{_P_ACTIVE}/archive", json={"archived": True})
    html = client.get("/projects").get_data(as_text=True)
    active = html[html.index('id="activeProjects"'):]
    active = active[:active.index("</section>")]
    assert "רעיונות להתחלה" in active
    assert active.count('class="project-tile project-idea') == 4
    assert "פרויקט חדש" in active and "הפרויקט הראשון שלכם" not in active


def test_one_active_project_is_the_default_for_plus(client):
    """Matan (1.10): on the projects page, with one active project, + opens a form
    with it selected."""
    html = client.get("/projects").get_data(as_text=True)
    assert f'<span id="sfPageProject" data-project-id="{_P_ACTIVE}" hidden></span>' in html


def test_several_active_projects_have_no_default(client, fake):
    client.put(f"/api/projects/{_P_DONE}/archive", json={"archived": False})
    html = client.get("/projects").get_data(as_text=True)
    assert 'id="sfPageProject"' not in html


def test_no_active_project_no_default(client):
    client.put(f"/api/projects/{_P_ACTIVE}/archive", json={"archived": True})
    html = client.get("/projects").get_data(as_text=True)
    assert 'id="sfPageProject"' not in html


def test_settings_has_no_plus():
    """Matan (1.10): settings have no + for adding a transaction."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tpl = (root / "frontend/templates/settings.html").read_text(encoding="utf-8")
    assert "{% block body_class %}no-fab settings-page{% endblock %}" in tpl
    assert "body.no-fab .fab { display: none; }" in (root / "frontend/static/css/style.css").read_text(encoding="utf-8")


def test_finished_projects_are_small_but_still_open(client):
    """Matan (30.9): the finished ones are smaller than the active ones, but still a
    link to the project page. Since 5.10: boxes for active ones, tags for finished
    ones."""
    html = client.get("/projects").get_data(as_text=True)
    active = html[html.index('id="activeProjects"'):html.index('id="finishedProjects"')]
    finished = html[html.index('id="finishedProjects"'):]
    finished = finished[:finished.index("</section>")]
    assert 'class="project-tile tint-' in active
    assert "project-tile" not in finished and 'class="finished-chip"' in finished
    assert f'href="/projects/{_P_DONE}"' in finished


def test_settings_says_no_recurring_instead_of_zero():
    """Matan (30.9): "אין עסקאות קבועות", not "0 עסקאות חוזרות פעילות". Since 11.10
    there is no count row under the heading, and the empty group says it inside."""
    from pathlib import Path
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/settings.html").read_text(encoding="utf-8")
    assert "עסקאות קבועות פעילות" not in tpl and "0 עסקאות" not in tpl
    assert '<p class="empty-text">אין עסקאות קבועות עדיין</p>' in tpl


# ─── A project deleted a moment earlier (review of 1.10) ─────────────────────

_GONE = "aaaaaaaa-0000-0000-0000-00000000dead"


def test_sharing_a_just_deleted_project_says_so(fake):
    ok, err = db.share_project(_GONE, _FAM, _ME)
    assert not ok and err == "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"


def test_unsharing_a_just_deleted_project_says_so(fake):
    ok, err = db.unshare_project(_GONE, _FAM, _ME)
    assert not ok and err == "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"


def test_editing_a_just_deleted_project_is_a_404(client):
    """The project access check (project_access_required) already answers 404 before
    the action: the same "not found" as for someone else's personal project, on
    purpose."""
    res = client.put(f"/api/projects/{_GONE}", json={"name": "טיול", "track_expense": True})
    assert res.status_code == 404


def test_the_update_itself_tells_a_vanished_project_apart(fake):
    """And in the split second between the check and the action: None, and the route
    answers 404 and not 500."""
    assert db.update_project(_GONE, _FAM, name="טיול") is None
