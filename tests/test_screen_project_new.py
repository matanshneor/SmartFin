"""The "new project" screen as a fixed page (the frontend/backend split).

The screen shows nothing that belongs to a family, so it needs no API. The one
thing the server wrote into it was the prefill from a starter idea
(`?icon=✈️&name=טיול`). Behind the switch the server stops, the page becomes
the same bytes for every address, and `projects.js` reads the address itself.
"""
import re
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_JS = (Path(__file__).resolve().parent.parent / "frontend/static/js/projects.js").read_text(encoding="utf-8")


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.delenv("SF_API_SCREENS", raising=False)
    monkeypatch.delenv("SF_API_SCREENS_PREVIEW", raising=False)
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module.db, "get_family_members", lambda fid: [{"id": "u0", "name": "מתן"}])
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = "u0"
        sess["family_id"] = "fam-1"
    return c


def _field(html, element_id):
    return re.search(rf'<input[^>]*id="{element_id}"[^>]*>', html).group(0)


def test_with_no_switch_the_server_fills_the_form_as_always(client):
    html = client.get("/projects/new?icon=✈️&name=טיול").get_data(as_text=True)
    assert 'value="✈️" data-auto-icon="1"' in _field(html, "projectIcon")
    assert 'value="טיול"' in _field(html, "projectName")


def test_behind_the_switch_the_page_is_the_same_for_every_address(client, monkeypatch):
    monkeypatch.setenv("SF_API_SCREENS", "project_new")
    plain = client.get("/projects/new").get_data(as_text=True)
    idea = client.get("/projects/new?icon=✈️&name=טיול").get_data(as_text=True)
    assert plain == idea, "a page that differs by address can't be served as a file"
    assert 'value=""' in _field(idea, "projectIcon") and "data-auto-icon" not in _field(idea, "projectIcon")
    assert 'value=""' in _field(idea, "projectName")


def test_the_script_reads_the_idea_from_the_address():
    block = _JS[_JS.index("// From a starter idea"):_JS.index("[ownerSharedBtn, ownerPersonalBtn]")]
    assert "new URLSearchParams(location.search)" in block
    assert "take('icon', 16), name = take('name', 50)" in block, "the server's limits"
    assert "Array.from(" in block, "16 characters, not 16 halves of an emoji"
    assert "iconEl.dataset.autoIcon = '1'" in block, "a suggested icon may still be replaced by the name's"
    # Written as a value, never as HTML: `?name="><script>` stays a name
    assert ".value = " in block and "innerHTML" not in block


def test_a_value_the_server_already_wrote_is_left_alone():
    block = _JS[_JS.index("// From a starter idea"):_JS.index("[ownerSharedBtn, ownerPersonalBtn]")]
    assert "if (icon && !iconEl.value)" in block and "if (name && !nameEl.value)" in block
