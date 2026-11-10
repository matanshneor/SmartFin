"""Picking a colour for each family member (Matan, 30.9, round 6, item 13).

Whoever didn't pick keeps the colour they have today (by join order). No two
people in the same colour. Everyone picks for themselves; the family manager, for
everyone.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, assign_member_colors, _OWNER_HEX

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME, _HER, _KID = ("22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333",
                   "44444444-4444-4444-4444-444444444444")
_MEMBERS = [{"id": _ME}, {"id": _HER}, {"id": _KID}]


def test_there_are_eight_colors():
    assert sorted(_OWNER_HEX) == list(range(8))
    assert len(set(_OWNER_HEX.values())) == 8


def test_without_choices_nothing_changes():
    assert assign_member_colors(_MEMBERS, {}) == {_ME: 0, _HER: 1, _KID: 2}


def test_a_choice_wins_and_the_others_step_aside():
    """Or picked Matan's blue: Matan moves to the next free colour; there aren't two
    blues left."""
    colors = assign_member_colors(_MEMBERS, {_HER: 0})
    assert colors[_HER] == 0
    assert len(set(colors.values())) == 3


def test_a_stale_or_broken_choice_is_ignored():
    colors = assign_member_colors(_MEMBERS, {"someone-who-left": 1, _KID: 99, _ME: "x"})
    assert colors == {_ME: 0, _HER: 1, _KID: 2}


# ── The route ──────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    state = {"settings": dict(db.DEFAULT_FAMILY_SETTINGS), "manager": _HER}
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM, "manager_id": state["manager"]})
    monkeypatch.setattr(db, "get_family_members", lambda fid: [{**m, "name": "x"} for m in _MEMBERS])
    monkeypatch.setattr(app_module, "family_settings", lambda: state["settings"])
    monkeypatch.setattr(db, "get_family_settings", lambda *a, **k: state["settings"])

    def update(fid, patch):
        state["settings"] = {**state["settings"], **patch}
        return True
    monkeypatch.setattr(db, "update_family_settings", update)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        c.state = state
        yield c


def _pick(client, member, color):
    return client.put("/api/family/member-color", json={"member_id": member, "color": color})


def test_i_choose_my_own_color(client):
    assert _pick(client, _ME, 5).status_code == 200
    assert client.state["settings"]["member_colors"] == {_ME: 5}


def test_a_taken_color_is_refused(client):
    res = _pick(client, _ME, 1)                      # Or's colour
    assert res.status_code == 409 and "תפוס" in res.get_json()["error"]


def test_nobody_chooses_for_someone_else_not_even_the_manager(client):
    """Matan (30.9): everyone only their own colour."""
    assert _pick(client, _KID, 6).status_code == 403
    client.state["manager"] = _ME
    assert _pick(client, _KID, 6).status_code == 403


@pytest.mark.parametrize("color", [-1, 8, "2", None, 2.5])
def test_bad_colors_are_refused(client, color):
    assert _pick(client, _ME, color).status_code == 422


def test_someone_outside_the_family_is_refused(client):
    assert _pick(client, "55555555-5555-5555-5555-555555555555", 6).status_code == 404


def test_every_color_has_a_pill_in_both_themes():
    """`_OWNER_HEX` and `.owner-N` must stay in sync."""
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "frontend/static/css/style.css").read_text(encoding="utf-8")
    for i in range(len(_OWNER_HEX)):
        assert f".owner-{i} {{" in css
        assert f':root[data-theme="dark"] .owner-{i} {{' in css


def test_the_settings_page_offers_the_colors():
    from pathlib import Path
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/settings.html").read_text(encoding="utf-8")
    # A "my colour" row in "my account" (Matan, 11.10; until then a button on my
    # row in the family member list), and a sheet with the name tag in every
    # colour, not circles under the name
    assert 'class="member-colors"' not in tpl
    mine = tpl[tpl.index('id="my-details"'):]
    mine = mine[:mine.index("</section>")]
    assert "{% if member_colors and user.id in member_colors %}" in mine
    assert 'id="memberColorOpen"' in mine
    assert 'id="memberColorSheet"' in tpl and 'class="owner-pill owner-{{ idx }}"' in tpl
    assert "{% if taken %}disabled{% endif %}" in tpl


def test_the_color_window_opens_above_everything():
    """Inside the settings group the sheet is trapped under the bottom menu and the
    + button."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend/static/js/settings.js").read_text(encoding="utf-8")
    assert "document.body.appendChild(sheet);" in js
