"""The project list showed an amount and an ✕, and both were in the wrong place.

**The amount** needs context the list doesn't have: ₪4,200 out of what? Over how
long? Against which target? Inside the project all of that is shown anyway.

**And the ✕** was the most irreversible action on the page, one tap away from the
project's name, with no information about what is about to be deleted. It asked
two questions in a row because there was nothing else to tell what would be lost.

Both moved into the project. There the user has already seen the amounts and the
transactions, and the delete area says explicitly how many transactions are at
stake.
"""
import re
from pathlib import Path

import pytest

from backend import supabase_config as db
from backend.app import app, limiter
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_LIST = (_ROOT / "frontend/templates/projects.html").read_text(encoding="utf-8")
_EDIT = (_ROOT / "frontend/templates/project_edit.html").read_text(encoding="utf-8")
# deletion moved to the bottom of the project page (Matan, 9.10)
_DETAIL = (_ROOT / "frontend/templates/project_detail.html").read_text(encoding="utf-8")
_JS = _ROOT / "frontend/static/js"

_FAM, _ME = "f" * 8, "m" * 8


# ─── The list: names only ────────────────────────────────────────────────────

def test_finished_projects_show_no_amounts():
    """A finished project: only the name. (For active ones: "כמה נשאר", Matan 12.10:
    below.)"""
    finished = _LIST[_LIST.index('id="finishedProjects"'):]
    assert "₪" not in finished and "money" not in finished
    assert "hero-totals" not in _LIST and "הוצאתם" not in _LIST


def _render_tiles(projects):
    from backend.app import app
    with app.test_request_context("/projects"):
        return app.jinja_env.get_template("projects.html").render(
            active_page="projects", user={"id": "x", "name": "מתן", "avatar_initial": "מ"},
            projects=projects, finished_projects=[])


def _proj(**kw):
    base = {"id": "p1", "name": "טיול", "description": None, "icon": "✈️", "is_personal": False,
            "budget_target": None, "spent": 0.0, "saved": 0.0, "amount": 0.0,
            "track_expense": True, "track_income": False, "track_savings": False}
    return {**base, **kw}


def test_a_tile_with_a_target_says_how_much_is_left():
    """Matan (12.10), reversing 5.10: "נשארו ₪7,800 מתוך ₪12,000", and a bar, as on
    the project page."""
    html = _render_tiles([_proj(budget_target=12000.0, spent=4200.0)])
    assert "נשארו <b>₪7,800</b> מתוך ₪12,000" in html
    assert 'class="project-tile-bar"' in html and "width: 35%;" in html


def test_a_tile_over_its_target_says_so():
    html = _render_tiles([_proj(budget_target=1000.0, spent=1250.0)])
    assert "חריגה של <b>₪250</b>" in html and "width: 100%;" in html and 'class="danger"' in html


def test_a_tile_without_a_target_says_what_went_out():
    assert "יצאו <b>₪18,350</b> עד עכשיו" in _render_tiles([_proj(spent=18350.0)])
    assert "נחסכו <b>₪900</b> עד עכשיו" in _render_tiles(
        [_proj(track_expense=False, track_savings=True, saved=900.0)])


def test_an_empty_project_gets_no_line():
    assert "project-tile-progress" not in _render_tiles([_proj()])


def test_the_list_has_no_delete_button():
    """An ✕ next to the name, without knowing what will be deleted."""
    assert "delete-project-btn" not in _LIST


def test_nothing_still_listens_for_that_button():
    """An orphan listener is a bug waiting for whoever brings the HTML back."""
    owners = [f.name for f in _JS.glob("*.js")
              if "closest('.delete-project-btn')" in f.read_text(encoding="utf-8")]

    assert owners == [], f"there is still a listener in: {owners}"


def test_the_tile_opens_the_project():
    """The whole box is the link to the project (Matan, 5.10)."""
    tile = _LIST[_LIST.index("{% macro tile(p, i) %}"):]
    assert tile.index('<a class="project-tile') < tile.index("project_detail")


def test_the_name_and_description_survived():
    """Control: we removed the amount and deletion, not the content."""
    tile = _LIST[_LIST.index("{% macro tile(p, i) %}"):_LIST.index("{% endmacro %}", _LIST.index("{% macro tile(p, i) %}"))]
    assert "p.name" in tile and "p.description" in tile


# ─── Inside the project: the amount and deletion ─────────────────────────────

def test_the_project_page_still_shows_the_totals():
    """This is where the amount moved to."""
    detail = (_ROOT / "frontend/templates/project_detail.html").read_text(encoding="utf-8")

    assert "hero-totals" in detail
    assert "project.spent" in detail


def test_deletion_lives_at_the_bottom_of_the_project_page():
    """Not on the edit page: there, editing only (Matan, 9.10)."""
    assert "project-danger" not in _EDIT and "confirmDeleteProjectBtn" not in _EDIT
    assert 'id="confirmDeleteProjectBtn"' in _DETAIL
    assert _DETAIL.index("project-danger") > _DETAIL.rindex("כל העסקאות ("), "deletion isn't at the bottom"


def test_finishing_comes_before_deleting_in_the_same_card():
    """Finishing is the soft way: it is seen before deleting."""
    card = _DETAIL[_DETAIL.index('class="chart-card project-finish"'):]
    assert card.index("data-archive-project") < card.index("project-danger")


def test_a_finished_project_can_still_be_deleted():
    card = _DETAIL[_DETAIL.index('class="chart-card project-finish"'):]
    finish = card[card.index("{% if not project.archived %}"):]
    finish = finish[:finish.index("{% endif %}")]
    assert "data-archive-project" in finish, "finishing doesn't depend on the project's state"
    assert "project-danger" not in finish, "deletion is hidden in a finished project"


def test_it_says_how_many_transactions_will_be_affected():
    """The ✕ in the list carried no number."""
    block = _DETAIL[_DETAIL.index("project-danger"):]

    assert "tx_count" in block
    assert "אין לפרויקט עסקאות" in block, "an empty project gets its own wording"


def test_there_is_no_choice_to_keep_the_transactions_any_more():
    """"Keep" promised "they'll go back to the normal category", and they landed in
    the household as "no category" and inflated the past months. Matan decided:
    deleting a project deletes its transactions."""
    block = _DETAIL[_DETAIL.index("project-danger"):]
    js = (_JS / "project-detail.js").read_text(encoding="utf-8")

    assert "data-tx-mode" not in block
    assert "הקטגוריה הרגילה" not in block + js
    assert "txMode" not in js


def test_the_warning_says_what_goes_and_what_does_not_change():
    block = _DETAIL[_DETAIL.index("project-danger"):]

    assert "יחד עם" in block and "project.spent" in block, "it wasn't said what is deleted and how much money"
    assert "ההוצאות של הבית לא ישתנו" in block


# ─── And that the route really does both actions ─────────────────────────────

@pytest.fixture
def project(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    fake = FakeSupabase(
        projects=[{"id": "p1", "family_id": _FAM, "name": "שיפוץ", "owner_id": None,
                   "archived": False, "track_expense": True,
                   "track_income": False, "track_savings": False}],
        transactions=[{"id": f"t{i}", "family_id": _FAM, "project_id": "p1",
                       "amount": 100.0, "type": "expense", "date": "2026-09-01"}
                      for i in range(3)],
    )
    monkeypatch.setattr(db, "get_client", lambda: fake)
    monkeypatch.setattr(db, "get_project_for_transaction",
                        lambda pid, fid: {"id": pid, "owner_id": None,
                                          "track_expense": True, "track_income": False,
                                          "track_savings": False})
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield c, fake


@pytest.mark.parametrize("body", [{}, {"delete_transactions": False}])
def test_deleting_a_project_always_deletes_its_transactions(project, body):
    """Also an old tab still sending "keep": the server doesn't trust it."""
    c, fake = project

    res = c.delete("/api/projects/p1", json=body)

    assert res.status_code == 200
    assert res.get_json()["deleted"] == 3
    assert fake.rows("transactions") == [], "transactions were left without a project: they'll land in the household"
    assert fake.rows("projects") == []


def test_deleting_them_reports_the_real_number(project):
    """The number comes from the server and not the browser; otherwise it is an
    unchecked promise."""
    c, fake = project

    res = c.delete("/api/projects/p1", json={})

    assert res.get_json()["deleted"] == 3
    assert fake.rows("transactions") == []


def test_the_receipts_go_with_the_transactions(project, monkeypatch):
    """The nightly cleanup of orphan files failed on every run (Supabase blocks direct
    deletion from storage), so after deleting the project nobody would delete the
    images."""
    c, fake = project
    fake.tables["transactions"][0]["receipt_path"] = f"{_FAM}/a.jpg"
    fake.tables["transactions"][1]["receipt_path"] = f"{_FAM}/b.jpg"
    gone = []
    monkeypatch.setattr(db, "delete_receipts", lambda token, paths: gone.extend(paths))

    c.delete("/api/projects/p1", json={})

    assert sorted(gone) == [f"{_FAM}/a.jpg", f"{_FAM}/b.jpg"]


def test_a_project_that_is_already_gone_deletes_no_files(project, monkeypatch):
    c, fake = project
    fake.tables["transactions"][0]["receipt_path"] = f"{_FAM}/a.jpg"
    fake.tables["projects"] = []
    monkeypatch.setattr(db, "delete_receipts",
                        lambda *a: pytest.fail("files of a project that wasn't deleted were deleted"))

    c.delete("/api/projects/p1", json={})


# ─── Managing the project is one menu ───────────────────────────────────────
#
# The page was four separate chart-cards, each with margin-bottom: 16px, and the
# fourth (deletion) in a completely different style, so one settings screen
# looked like four screens glued together.

def test_the_management_page_is_one_container():
    """One card, not four."""
    assert 'class="project-settings"' in _EDIT
    assert 'class="chart-card"' not in _EDIT, \
        "there is still a separate card: the gap between them comes back"


def test_every_section_became_a_block_inside_it():
    """Each part stays a part, just without its own frame."""
    blocks = _EDIT.count("project-settings-block")
    # 5 since "finish project" (Matan, 30.9); 3 since finishing and deleting moved
    # to the project page (9.10): details, ownership, categories
    assert blocks == 3, f"found {blocks} blocks instead of 3"


def test_the_sections_are_separated_by_a_line_and_not_by_a_gap():
    """"One continuous menu with separations between them": the line is the
    separation."""
    body = _EDIT[_EDIT.index('class="project-settings"'):]
    dividers = body.count('class="group-divider"')

    assert dividers == 2, f"{dividers} dividers between 3 parts"


def test_the_container_carries_the_card_styling_now():
    """If the container doesn't carry a background and border, the four parts simply
    float on the background."""
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    rule = css[css.index(".project-settings {"):]
    rule = rule[:rule.index("}")]

    for prop in ("background", "border", "border-radius"):
        assert prop in rule, f"{prop} is missing: the parts won't look like one card"


def test_the_dividers_reach_the_edges():
    """A line with margins looks like a line inside a paragraph, not like a border
    between two parts."""
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")

    assert ".project-settings > .group-divider { margin: 0; }" in css


# ─── The settings screen was crowded, and one field in it didn't explain itself ─
#
# Three things Matan pointed at in a screenshot:
#
# 1. The floating + ("הוסף עסקה חדשה") sat in the bottom-left corner, exactly on
#    "מחק פרויקט". The only irreversible action on the page was half hidden
#    behind a button that has no meaning on a settings screen.
# 2. The add-category row touched the last row in the list, because
#    .category-row:last-child resets the bottom padding.
# 3. "יעד תקציב": an amount field without a single word of explanation.

_DETAIL = (_ROOT / "frontend/templates/project_detail.html").read_text(encoding="utf-8")
_BASE = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _visible(html):
    """Without comments. A Hebrew comment explaining a fix almost always contains the
    bug's words, and so a check "passes" on text no user sees. That already happened
    here, and this time it also failed this very test."""
    return re.sub(r"\{#.*?#\}|<!--.*?-->", "", html, flags=re.S)


def test_the_budget_field_is_called_the_same_thing_in_both_places():
    """One name for the field. Reversed word order between two pages reads like two
    things."""
    for name, html in (("עריכה", _EDIT), ("פרויקט", _DETAIL)):
        shown = _visible(html)
        assert "תקציב יעד" in shown, f"the new name doesn't appear in {name}"
        assert "יעד תקציב" not in shown, f"the old name is still shown in {name}"


def test_the_old_budget_name_is_gone_everywhere():
    """The test above covered two pages, and the old name survived in three others:
    the new-project form, settings, and the server's error message (found 30.9)."""
    places = list((_ROOT / "frontend/templates").glob("*.html")) + \
        list((_ROOT / "frontend/static/js").glob("*.js")) + [_ROOT / "backend/app.py"]
    for f in places:
        text = f.read_text(encoding="utf-8")
        text = re.sub(r"\{#.*?#\}|<!--.*?-->|/\*.*?\*/", "", text, flags=re.S)
        text = "\n".join(l for l in text.split("\n") if not l.strip().startswith(("#", "//")))
        assert "יעד תקציב" not in text, f.name


def test_the_budget_field_explains_what_it_does():
    """An amount field without explanation leaves the user guessing whether it is a
    ceiling, a forecast or money already set aside."""
    hint = _EDIT[_EDIT.index('id="budgetTargetHint"'):]
    hint = hint[:hint.index("</p>")]
    # without the tag and without indentation; otherwise spaces count as an
    # explanation, and "סכום." on an indented line "passes" the length check.
    words = " ".join(hint[hint.index(">") + 1:].split()).split(" ")

    assert len(words) >= 12, f"the explanation is {len(words)} words: that isn't an explanation"
    assert 'aria-describedby="budgetTargetHint"' in _EDIT, \
        "the explanation exists but isn't linked to the field, so a screen reader won't hear it"


def test_the_floating_plus_is_gone_from_the_settings_screen():
    """It covered the delete button."""
    assert "{% block body_class %}no-fab{% endblock %}" in _EDIT
    assert "{% block body_class %}{% endblock %}" in _BASE, \
        "the body doesn't carry the block, so the class never arrives"
    assert "body.no-fab .fab" in _CSS


def test_the_plus_button_itself_still_exists():
    """transactions.js does fabBtn.addEventListener with no guard at all. Deleting
    the element from the template would throw and kill the rest of the file,
    including the transaction window. Hide, don't delete."""
    assert 'id="fabBtn"' in _BASE

    js = (_JS / "transactions.js").read_text(encoding="utf-8")
    assert "fabBtn.addEventListener" in js, \
        "if it is already guarded, the element can be deleted and this test is redundant"


def test_adding_a_category_is_visibly_a_separate_thing():
    """Without separation, "הוסף" looks like a button of the last category in the list."""
    rule = _CSS[_CSS.index("#projectCategoriesArea .add-cat-form"):]
    rule = rule[:rule.index("}")]

    import re
    gap = re.search(r"margin-top\s*:\s*(\d+)", rule)
    assert gap and int(gap.group(1)) >= 12, "no real gap between the list and the form"
    assert "border-top" in rule, "only a gap: no line separating the list and the form"


# ─── And the tabs touched the first row below them ───────────────────────────
#
# .type-toggle has no margin-bottom, and .category-row:first-child resets the top
# padding. Each is reasonable on its own; together they give zero gap, and the
# active button ("הוצאות") sticks to the first row as if they were one component.
# Exactly the same in category management in settings.

def _decl(css, selector, prop):
    m = re.search(r"^" + re.escape(selector) + r"[^{]*\{([^}]*)\}", css, re.M)
    assert m, f"no rule starts with {selector}"
    d = re.search(rf"\b{prop}\s*:\s*([^;]+)", m.group(1))
    return d.group(1).strip() if d else None


def test_the_tabs_do_not_touch_the_row_below_them():
    gap = _decl(_CSS, ".project-cat-tabs,", "margin-bottom")

    assert gap and int(gap.replace("px", "")) >= 8, f"the gap is {gap}"


def test_the_settings_screen_got_the_same_fix():
    """The same fault, the same code, a different screen. Fixing only one leaves the
    other broken without anyone checking it again."""
    rule = re.search(r"^\.project-cat-tabs,\n\.cat-type-tabs\s*\{", _CSS, re.M)

    assert rule, "only the project tabs were fixed"


def test_the_first_row_gets_its_padding_back():
    for area in ("#projectCategoriesArea", "#categoriesArea"):
        pad = _decl(_CSS, f"{area} .category-row:first-child", "padding-top")
        assert pad and int(pad.replace("px", "")) > 0, f"{area}: {pad}"


def test_the_new_rule_actually_wins():
    """.category-row:first-child { padding-top: 0 } is still in the stylesheet. A
    rule made of classes only wouldn't beat it by order, because it is defined long
    after it but with the same specificity: an #id is needed."""
    assert ".category-row:first-child { padding-top: 0; }" in _CSS, \
        "the reset was removed: the new rule is redundant, and this test should go with it"

    # every selector in the stylesheet that restores top padding to the first row
    # must carry an #id.
    winners = [
        sel for sel, body in re.findall(r"([^{}]*\.category-row:first-child[^{}]*)\{([^}]*)\}", _CSS)
        if re.search(r"padding-top\s*:\s*[1-9]", body)
    ]
    assert winners, "no rule restores padding to the first row"
    for sel in winners:
        assert "#" in sel, f"a selector without an #id won't beat the reset: {sel.strip()}"


# ─── Coloured boxes (Matan, 5.10) ────────────────────────────────────────────

def test_four_colours_always_in_the_same_order():
    """Blue, peach, pink, green, and again (Matan, 5.10). By position in the list, so
    two identical ones never touch, even in two columns."""
    from backend.app import app
    projects = [{"id": f"p{i}", "name": f"פרויקט {i}", "icon": "🎯", "description": None,
                 "is_personal": False} for i in range(6)]
    with app.test_request_context():
        html = app.jinja_env.from_string(_LIST).blocks["content"]
        html = "".join(html(app.jinja_env.from_string(_LIST).new_context(
            {"projects": projects, "finished_projects": []})))
    tints = [int(t) for t in re.findall(r'class="project-tile tint-(\d)"', html)]
    assert tints == [0, 1, 2, 3, 0, 1]
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    assert "--tint-4" not in css, "only four colours"


# ─── Creating a project on its own screen (Matan, 5.10) ──────────────────────

def test_new_project_and_ideas_lead_to_their_own_screen():
    """Not a form that opens inside the list: a new screen."""
    assert 'id="newProjectForm"' not in _LIST and "projects.js" not in _LIST
    assert _LIST.count("url_for('project_new')") == 2
    assert "url_for('project_new', icon=icon, name=name)" in _LIST


@pytest.fixture
def new_screen(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase())
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield c


def test_the_new_project_screen_has_the_form_and_a_way_back(new_screen):
    html = new_screen.get("/projects/new").get_data(as_text=True)

    assert 'id="newProjectForm"' in html and "js/projects.js" in html
    assert '<h1 class="hero-title">פרויקט חדש</h1>' in html
    assert 'href="/projects"' in html and 'aria-label="חזרה לפרויקטים"' in html
    assert 'class="no-fab' in html or "no-fab" in html


def test_an_idea_arrives_filled_in(new_screen):
    html = new_screen.get("/projects/new?icon=✈️&name=טיול").get_data(as_text=True)

    assert 'value="✈️" data-auto-icon="1"' in html
    assert 'value="טיול"' in html


def test_whatever_comes_in_the_address_is_text_and_short(new_screen):
    html = new_screen.get('/projects/new?name="><script>x</script>' + "א" * 80).get_data(as_text=True)

    assert "<script>x</script>" not in html
    assert "א" * 51 not in html


def test_after_creating_it_goes_into_the_new_project():
    js = (_JS / "projects.js").read_text(encoding="utf-8")
    assert "window.location.href = data.id ? '/projects/' + encodeURIComponent(data.id) : '/projects';" in js


# ─── Finishing the project on the project page, saving in a bottom bar (Matan, 9.10) ─


def test_finishing_a_project_moved_to_the_bottom_of_the_project_page():
    assert "data-archive-project" not in _EDIT, "finishing the project is still on the edit page"
    tail = _DETAIL[_DETAIL.rindex("כל העסקאות ("):]
    assert 'data-archived="true"' in tail, "no finish button at the bottom of the project page"


def test_the_finish_button_is_not_offered_for_a_finished_project():
    """A finished project already has "reopen" in the top bar."""
    tail = _DETAIL[_DETAIL.rindex("כל העסקאות ("):]
    block = tail[tail.index("{% if not project.archived %}"):]
    assert block.index('data-archived="true"') < block.index("{% endif %}")


def test_save_sits_in_a_bar_outside_the_details_form():
    form = _EDIT[_EDIT.index('id="editProjectForm"'):_EDIT.index("</form>")]
    assert 'type="submit"' not in form, "saving is still inside the details section"
    bar = _EDIT[_EDIT.index('class="project-save-bar"'):]
    assert 'form="editProjectForm"' in bar, "the button in the bar isn't connected to the form"
    assert _EDIT.index('class="project-save-bar"') > _EDIT.index('</section>'), \
        "the bar should be last on the page"
    js = (_ROOT / "frontend/static/js/project-edit.js").read_text(encoding="utf-8")
    assert "button[form=\"editProjectForm\"]" in js, "the code looks for the button inside the form and won't find it"


# ─── Every edit is saved only on "שמירת השינויים" (Matan, 9.10) ─────────

_EDIT_JS = (_JS / "project-edit.js").read_text(encoding="utf-8")


def _handler(marker):
    """The body of the handler starting at marker, to its end."""
    body = _EDIT_JS[_EDIT_JS.index(marker):]
    return body[:body.index("\n});")]


def test_category_actions_do_not_reach_the_server_on_their_own():
    """Adding, renaming and deleting only mark; one fetch, on save."""
    adding = _EDIT_JS[_EDIT_JS.index("addForm.addEventListener('submit'"):]
    adding = adding[:adding.index("\n});")]
    assert "fetch(" not in adding and "call(" not in adding, "adding a category still sends immediately"
    for marker in (".delete-cat-btn')", ".cat-edit-ok')", ".undo-cat-btn')"):
        h = _handler(marker)
        assert "fetch(" not in h and "call(" not in h, f"{marker} sends to the server immediately"


def test_ownership_waits_for_save_too():
    for btn in ("shareBtn.addEventListener", "unshareBtn.addEventListener"):
        h = _EDIT_JS[_EDIT_JS.index(btn):]
        h = h[:h.index("\n}")]
        assert "fetch(" not in h and "call(" not in h, f"{btn} changes ownership without saving"
    assert "'/' + ownership" in _EDIT_JS, "saving doesn't send the ownership change"


def test_new_categories_are_saved_before_deletions():
    """Transactions of a deleted category can move to a category added in the same
    edit, so it has to exist on the server before the deletion."""
    steps = _EDIT_JS[_EDIT_JS.index("function saveSteps()"):]
    assert steps.index("'POST'") < steps.index("'DELETE'")


def test_leaving_with_unsaved_changes_asks_first():
    assert "beforeunload" in _EDIT_JS
    assert "לצאת בלי לשמור?" in _EDIT_JS


def test_the_categories_section_starts_closed_with_a_count():
    assert 'id="projectCatToggle"' in _EDIT and 'aria-expanded="false"' in _EDIT
    assert '<div id="projectCatBody" hidden>' in _EDIT
    assert 'id="projectCatCount"' in _EDIT
