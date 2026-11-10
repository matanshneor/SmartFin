"""Five frontend fixes, each of which was silent and invisible until someone
ran into it.

**C1: a receipt that sticks to the wrong transaction.** `resetScanUI` didn't
clear `txReceiptPath`; only `resetForm` (which runs only from `openAddModal`)
did. An abandoned scan left a path in the field, and any other edit sent it along.

**C3: the sign-in page shows two forms.** `showTab` didn't touch `forgotPanel`,
so switching tabs after "forgot password" showed a reset form above another form.

**C4: `.zero-toggle` dead on the keyboard.** Declared `role="button"
tabindex="0"` and handled on `click` only: an element that isn't a `<button>`
doesn't produce a click from Enter.

**C5: the sound couldn't be turned off.** The escape hatch was in the code;
nothing wrote to it.

**C9: the dashboard bar lies in the first month.** `income == 0` showed "used 0%
of income" next to a red minus: exactly a new user's first screen.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "frontend/static/js"
_TPL = _ROOT / "frontend/templates"


def _read(rel):
    return (_ROOT / rel).read_text(encoding="utf-8")


def _strip_comments(js):
    """Without this, a comment explaining why something isn't done counts as doing
    it: exactly what let three of the mutations here slip through the first time:
    the fix's documentation contained the same keyword the test searched for in
    the code itself."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


# ─── C1: the receipt doesn't stick to the wrong transaction ─────────────────

def test_reset_scan_ui_clears_the_receipt_path():
    """The heart. If this still doesn't happen here, an abandoned scan sticks its
    file onto the next transaction opened for editing."""
    js = _read("frontend/static/js/transactions.js")
    fn = js[js.index("function resetScanUI()"):]
    fn = fn[:fn.index("\n    }")]

    assert "txReceiptPath.value = ''" in fn, \
        "resetScanUI doesn't clear txReceiptPath: a receipt will stick to another transaction"


def test_close_modal_goes_through_reset_scan_ui():
    """Control: `closeModal` is the path that runs on every close (including ✕ on a
    scan), and it must call the function that clears."""
    js = _read("frontend/static/js/transactions.js")
    fn = js[js.index("function closeModal()"):]
    fn = fn[:fn.index("\n    }")]

    assert "resetScanUI()" in fn


# ─── C3: the sign-in page doesn't show two forms ────────────────────────────

def test_switching_tabs_hides_the_forgot_password_panel():
    js = _strip_comments(_read("frontend/static/js/login.js"))
    fn = js[js.index("function showTab(tab)"):]
    fn = fn[:fn.index("\n    }")]

    assert re.search(r"forgotPanel.*style\.display\s*=\s*['\"]none['\"]", fn, re.S), \
        "showTab doesn't close the forgot-password screen: two forms will be shown together"


# ─── C4: .zero-toggle responds to the keyboard ──────────────────────────────

def test_zero_toggle_responds_to_the_keyboard():
    """`role="button" tabindex="0"` with no Enter/Space listener is a lie to the
    screen reader: it is announced as a button and does nothing."""
    js = _strip_comments(_read("frontend/static/js/month.js"))
    keydown = js[js.index("document.addEventListener('keydown'"):]
    keydown = keydown[:keydown.index("\n});") + 4]

    assert re.search(r"closest\(['\"]\.zero-toggle['\"]\)", keydown), \
        "no code locates .zero-toggle in the keyboard handler"
    assert re.search(r"zero\.click\(\)", keydown), \
        "no keyboard handling for .zero-toggle: a Tab stop that does nothing"


def test_zero_toggle_still_marked_as_a_button_in_the_template():
    """Control: if the markup is removed, the test above checks something that doesn't exist."""
    for tpl in ("month.html",):
        html = _read(f"frontend/templates/{tpl}")
        assert 'class="zero-toggle"' in html
        block = html[html.index('class="zero-toggle"') - 40:][:120]
        assert 'role="button"' in block and 'tabindex="0"' in block


# ─── C5: the sound can be turned off ────────────────────────────────────────
# The sound itself was removed (Matan, 11.10); see
# `test_dark_mode.test_there_is_no_sound_and_so_no_sound_toggle`.


# ─── C9: the dashboard bar doesn't lie in the first month ───────────────────

def test_the_progress_bar_does_not_render_without_income():
    """`used_pct = 0` when `income == 0` is a default value, not a fact. The bar
    shouldn't exist when there is nothing to compute a percentage from."""
    html = _read("frontend/templates/index.html")
    bar_block = html[html.index('<div class="hero-bar">') - 200:
                     html.index('<div class="hero-bar">') + 900]

    assert "summary.income > 0" in bar_block, \
        "the bar is shown even when income is 0: 'used 0%' next to a red minus"


def test_a_message_explains_the_missing_bar_instead_of_showing_nothing():
    """Matan (1.11): "no income entered this month yet" is enough; the second line,
    "add income to see how the month was split", was removed."""
    html = _read("frontend/templates/index.html")
    assert "עדיין לא הוזנו הכנסות החודש" in html
    assert "הוסיפו הכנסה" not in html


# ─── C2: a server failure on the first transaction doesn't wipe what was typed ─

def test_a_server_error_reopens_the_modal_when_it_was_closed_optimistically():
    """On a **network** failure the sheet already reopened; on a **server** failure
    this was missed at exactly the same point: the branch handling `data.error`.
    The fields are never reset (`closeModal` doesn't touch them), so the only
    problem was that there was no way back to them except the FAB, which
    `openAddModal` clears."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    err_branch = js[js.index("if (data.error) {"):]
    err_branch = err_branch[:err_branch.index("\n            }")]

    assert re.search(r"if\s*\(placeholderRow\)\s*\{[^}]*reopenAfterFailure\(\)", err_branch, re.S), \
        "a server failure doesn't reopen the modal when it was closed optimistically"


def test_the_network_failure_path_still_reopens_too():
    """Control: the previous fix (network failure) wasn't overwritten. Anchored from
    the start of the save chain (`send(false)`) because the file contains several
    other unrelated `.catch`es."""
    import re
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    chain = js[js.index("send(false)"):]
    # The anchor used to be "the first call to `sfNetError`", and that stopped
    # being true when "update from now on" started showing its own network
    # failure. The outer catch is now identified by what is unique to it: it
    # removes the in-between row and opens the sheet.
    # And inside it, before, a branch for a form already replaced (adding in a
    # row), so "right after" became "inside"
    assert re.search(r"\.catch\(function \(\) \{[\s\S]{0,400}?if \(placeholderRow\) \{\s*"
                     r"placeholderRow\.remove\(\);\s*reopenAfterFailure\(\);", chain)


# ─── C6: no more browser dialogs ────────────────────────────────────────────

def test_no_javascript_file_uses_the_browsers_own_confirm():
    """The app built its own dialog, and there was one call to the native `confirm`,
    on switching family of all things, which cuts you off from all your
    transactions. In an installed PWA it is rendered with the host name above it,
    left to right and with no styling: the moment the app looks least like itself."""
    offenders = []
    for f in sorted(_JS.glob("*.js")):
        code = _strip_comments(f.read_text(encoding="utf-8"))
        if re.search(r"(?<![.\w])confirm\s*\(", code):
            offenders.append(f.name)

    assert not offenders, f"a browser dialog in: {offenders}"


def test_the_family_switch_still_asks_before_it_moves_you():
    """Control: replacing the dialog didn't turn it into an action without confirmation."""
    js = _strip_comments(_read("frontend/static/js/settings.js"))

    assert "appConfirm" in js
    assert "לעבור למשפחה אחרת?" in js


# ─── C10: small details, each of which looks like a bug ─────────────────────

@pytest.mark.parametrize("tpl", ["project_detail.html", "project_edit.html"])
def test_the_back_chevron_points_the_right_way_in_rtl(tpl):
    """A vertex at x=9 means an arrow pointing left, and in Hebrew left is
    "forward": these two pages used the "next" glyph for "back"."""
    html = _read(f"frontend/templates/{tpl}")
    back = html[:html.index("</a>")]

    assert '"15 18 9 12 15 6"' not in back, "the 'back' arrow points forward"


def test_the_profile_rows_do_not_grow_emoji_on_save():
    """The server renders `{{ m.phone }}` clean; the JS added "📞 " on save. The row
    changed on save and went back on refresh: it looks like a display bug, on
    exactly the screen whose whole job is to look trustworthy."""
    js = _strip_comments(_read("frontend/static/js/settings.js"))

    assert "'📞 '" not in js and "'💼 '" not in js


def test_the_confirm_dialog_announces_what_it_is_asking():
    """Without this a screen reader announced "cancel, button, dialog", including
    for "permanently delete the account?"."""
    html = _read("frontend/templates/base.html")
    dialog = html[html.index('id="confirmOverlay"') - 200:][:400]

    assert 'aria-labelledby="confirmTitle"' in dialog
    assert 'aria-describedby="confirmMessage"' in dialog


def test_the_copy_button_keeps_its_own_label():
    """After a successful copy the label was restored to 'העתק קוד' while the
    template says 'העתק הזמנה': the button silently renamed itself."""
    js = _read("frontend/static/js/onboarding.js")
    html = _read("frontend/templates/onboarding.html")

    label = re.search(r'id="copyInviteBtn"[^>]*>([^<]+)<', html).group(1).strip()
    assert f"'{label}'" in js, f"the JS restores a label other than {label!r}"


def test_the_signup_tab_does_not_welcome_you_back():
    """The heading is fixed and stays on the signup tab too: "ברוך הבא", masculine
    singular, for someone who was never here."""
    # On the rendered content, not the tag's form: the first version of the test
    # looked for `<h2>ברוך הבא</h2>` while in reality there is a `class` there,
    # i.e. it passed without the fix being applied at all.
    html = re.sub(r"\{#.*?#\}", "", _read("frontend/templates/login.html"), flags=re.S)

    assert "ברוך הבא" not in html, "the signup tab welcomes someone who is coming back"
    assert "ברוכים הבאים" in html


def test_every_delete_chain_handles_a_dropped_connection():
    """Without `.catch` the row stays on screen with no message at all, and the user
    taps ✕ again and again."""
    # Deleting a category (family and project) goes through one helper in
    # core.js (the project edit page only asks, and deletes on save: Matan, 9.10)
    assert "window.sfDeleteCategory(" in _read("frontend/static/js/settings.js")
    assert "window.sfAskCategoryDeletion(" in _read("frontend/static/js/project-edit.js")
    js = _strip_comments(_read("frontend/static/js/core.js"))
    for name in ("window.sfAskCategoryDeletion = function", "window.sfDeleteCategory = function"):
        block = js[js.index(name):]
        block = block[:block.index("\n    };")]
        assert ".catch(" in block, name


def test_local_storage_is_never_touched_unguarded():
    """`core.js` already fell over like this once: in Safari with cookies blocked
    the call **throws**, and in the first line of an IIFE it kills the whole file."""
    offenders = []
    for f in sorted(_JS.glob("*.js")):
        code = _strip_comments(f.read_text(encoding="utf-8"))
        for m in re.finditer(r"localStorage\.(getItem|setItem|removeItem)", code):
            window = code[max(0, m.start() - 260):m.start()]
            if "try" not in window:
                offenders.append(f"{f.name}:{code[:m.start()].count(chr(10)) + 1}")

    assert not offenders, f"unguarded access to localStorage: {offenders}"


def test_duplicating_a_transaction_restores_the_scan_button():
    """The scan button is hidden in edit mode, and duplicating clears `editId`
    without refreshing it: the duplicated row opened with no way to photograph a
    receipt."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    block = js[js.index("duplicateBtn.addEventListener"):]
    block = block[:block.index("\n    });")]

    assert "setType(" in block


def test_swipe_structure_is_built_lazily():
    """On the month page every transaction appears two or three times, so a month of
    150 transactions was ~350 synchronous reparents on load, and for no reason,
    because `touchstart` builds what is touched anyway."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))

    assert "querySelectorAll(ROW_SELECTOR).forEach" not in js, \
        "the structure is still built for every row on page load"


def test_pages_outside_the_app_shell_clear_the_iphone_status_bar():
    """`black-translucent` paints the page under the clock and the dynamic island.
    The main screens make room for it; sign-in/signup/privacy didn't, and "←
    back" on the privacy page sat at the clock's height."""
    css = re.sub(r"/\*.*?\*/", "", _read("frontend/static/css/style.css"), flags=re.S)
    for selector in (".auth-body", ".legal-page"):
        rule = css[css.index(selector + " {"):]
        rule = rule[:rule.index("}")]
        assert "safe-area-inset-top" in rule, selector
        assert "safe-area-inset-bottom" in rule, selector


def test_amount_cards_shrink_instead_of_pushing_the_page_sideways():
    """`1fr` doesn't shrink below its content. With agorot, the three cards on home
    pushed the page sideways at 390 and cut off the savings at 375
    (tests/browser/narrow_screens.py)."""
    css = re.sub(r"/\*.*?\*/", "", _read("frontend/static/css/style.css"), flags=re.S)

    def rule(selector):
        body = css[css.index(selector + " {"):]
        return body[:body.index("}")]

    for grid in (".summary-cards", ".hero-totals", ".kpi-chips"):
        assert "minmax(0, 1fr)" in rule(grid), grid
    assert "cqi" in rule(".summary-card .card-amount")
    assert "cqi" in rule(".kpi-chip .kpi-value")
    # The comparison page: a table with fixed columns instead of a row that
    # overflowed (see test_the_compare_page_is_one_table)
    assert "table-layout: fixed" in rule(".months-table")


def test_the_onboarding_points_to_a_settings_group_that_exists():
    """The welcome screen says "הגדרות ← המשפחה שלי", and the group was called
    "המשפחה". Matan chose to rename the group (30.9), so the pointer is right."""
    onboarding = _read("frontend/templates/onboarding.html")
    titles = re.findall(r'class="group-title">([^<]+)<', _read("frontend/templates/settings.html"))

    for target in re.findall(r"הגדרות ← ([^.<,\n]+)", onboarding):
        assert target.strip() in titles, target


def test_the_compare_page_compares_and_does_not_sum_everything():
    """Matan (30.9): "this is a page for comparing months only, without summing". The
    grand total also didn't say which period it was for."""
    html = re.sub(r"\{#.*?#\}", "", _read("frontend/templates/months.html"), flags=re.S)

    assert 'סה"כ' not in html and "סה״כ" not in html
    assert "archive | sum(" not in html


def test_the_link_next_to_recent_transactions_says_where_it_goes():
    """The latest are from all months; the link opens only the current month."""
    html = _read("frontend/templates/index.html")
    recent = html[html.index("<h2>עסקאות אחרונות</h2>"):]
    link = recent[recent.index('class="see-all-link"'):]
    link = link[link.index(">") + 1:link.index("</a>")]

    assert link == "לכל החודש"


def test_a_successful_save_releases_the_save_button():
    """After success the button stayed "saving…", and on a soft refresh the next
    edit wasn't sent (Matan, 30.9: "sometimes it doesn't let me tap save
    changes"). In the browser: tests/browser/edit_twice.py."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    finish = js[js.index("function finish()"):]
    finish = finish[:finish.index("closeModal();")]
    assert "setSubmitBusy(false)" in finish
    for opener in ("function openAddModal(", "function openEditModal("):
        body = js[js.index(opener):]
        assert "setSubmitBusy(false)" in body[:body.index("formSeq++") + 60], opener


@pytest.mark.parametrize("kind", ["income", "expense", "savings"])
def test_each_summary_leads_to_its_breakdown(kind):
    """Matan (30.9): tapping "income" on home or the month page → its breakdown.
    In the browser: tests/browser/cards_to_breakdown.py."""
    month = _read("frontend/templates/month.html")
    home = _read("frontend/templates/index.html")

    assert f'id="{kind}-breakdown"' in month
    assert f'href="#{kind}-breakdown"' in month
    assert f"url_for('month_view') }}}}#{kind}-breakdown" in home


def test_what_is_left_is_not_a_link():
    """The monthly balance doesn't belong to one category: Matan chose to leave it
    not tappable."""
    month = _read("frontend/templates/month.html")
    label = month.index("מאזן החודש</p>")
    opening = month[month.rindex("month-net", 0, label) - 20:label]
    assert "<a " not in opening and "kpi-link" not in opening


def test_the_card_you_jump_to_stays_visible():
    """`.anim-rise` starts at `opacity: 0` and stays visible only through its
    animation. The flash replaced it, and the card jumped to vanished (Matan: "as
    if it was erased"). In the browser: tests/browser/cards_to_breakdown.py prints
    the opacity."""
    css = re.sub(r"/\*.*?\*/", "", _read("frontend/static/css/style.css"), flags=re.S)
    rule = css[css.index(".chart-card.flash {"):]
    rule = rule[:rule.index("}")]
    assert "opacity: 1" in rule


def test_the_card_you_jump_to_stops_below_the_iphone_status_bar():
    """`scroll-margin-top: 14px` stopped the breakdown under the clock and the
    dynamic island."""
    css = re.sub(r"/\*.*?\*/", "", _read("frontend/static/css/style.css"), flags=re.S)
    rule = css[css.index("#savings-breakdown {"):]
    rule = rule[:rule.index("}")]
    assert "scroll-margin-top" in rule and "safe-area-inset-top" in rule


def test_the_project_field_is_last_in_the_form():
    """Matan (30.9): most transactions aren't in a project, so the field doesn't sit
    after the amount. In the browser: tests/browser/project_field_at_bottom.py."""
    base = _read("frontend/templates/base.html")
    form = base[base.index('id="txForm"'):base.index('id="submitBtn"')]
    assert form.index('id="projectGroup"') > form.index('id="txRecurring"')
    assert form.index('id="projectGroup"') > form.index('id="categoryGrid"')


def test_choosing_a_project_takes_you_to_its_categories():
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    handler = js[js.index("txProject.addEventListener('change'"):]
    handler = handler[:handler.index("});")]
    assert "scrollIntoView" in handler and "refreshCategoryGrid()" in handler


def test_a_project_shows_its_latest_five_and_the_rest_behind_a_button():
    """Matan (30.9): not a huge list on the project page. In the browser:
    tests/browser/project_tx_collapsed.py."""
    html = _read("frontend/templates/project_detail.html")
    assert "{% set first_shown = 5 %}" in html
    assert "loop.index > first_shown %} tx-extra" in html
    assert 'id="showAllProjectTx"' in html and 'aria-controls="projectTxList"' in html
    css = _read("frontend/static/css/style.css")
    assert "#projectTxList:not(.show-all) .tx-extra { display: none; }" in css


def test_the_show_all_button_works_without_the_chart_library():
    """The charts block returns early when there is no Chart.js: the button can't
    sit inside it."""
    js = _strip_comments(_read("frontend/static/js/project-detail.js"))
    charts_guard = js.index("if (!window.sfCharts.ready) return;")
    handler = js.index("#showAllProjectTx")
    charts_block_end = js.index("})();", charts_guard)
    assert handler > charts_block_end


@pytest.mark.parametrize("track_income,income,spent,expected", [
    (True, 1000.50, 300.25, "+₪700.25"),
    (True, 100, 350, "-₪250"),
    (True, 100, 100, "₪0"),
    (False, 0, 50, None),
], ids=["surplus", "deficit", "even", "expense-only-project"])
def test_the_project_shows_income_minus_expenses(track_income, income, spent, expected):
    """Matan (30.9): the project's balance separately, income minus expenses. In the
    browser: tests/browser/project_net.py."""
    from backend.app import app
    html = _read("frontend/templates/project_detail.html")
    start = html.index("{% if project.track_income and project.track_expense %}")
    end = html.index("</div>\n{% endif %}", start) + len("</div>\n{% endif %}")   # the block's closing tag, not the sign's
    project = {"track_income": track_income, "track_expense": True, "income": income, "spent": spent}
    out = app.jinja_env.from_string(html[start:end]).render(project=project)

    if expected is None:
        assert "project-net" not in out
    else:
        value = out[out.index('class="project-net-value">') + 26:out.index("</p>", out.index("project-net-value"))]
        assert value.strip() == expected, value


def test_the_project_value_sits_above_the_squares_with_its_short_name():
    """Matan (30.9): only "the project's cost / value", at the top, and below it
    expenses and income, equal."""
    html = _read("frontend/templates/project_detail.html")
    assert "<p class=\"kpi-label\">עלות / שווי הפרויקט</p>" in html
    assert html.index('class="project-net') < html.index('<div class="hero-totals"')


@pytest.mark.parametrize("target,spent,pct,danger", [
    (5000, 3100, 62, False),
    (1000, 1500, 150, True),
    (None, 3100, None, False),
], ids=["under", "over", "no-target"])
def test_the_target_bar_sits_under_the_squares(target, spent, pct, danger):
    """Matan (1.10, option B): the target budget bar at the top, under the tiles,
    and not a separate "progress against target" card that repeated the same
    numbers. No target: no bar."""
    from backend.app import app
    html = _read("frontend/templates/project_detail.html")
    assert "התקדמות מול היעד</h2>" not in html
    start = html.index("{% if project.budget_target %}\n{% set used_pct")
    end = html.index("{% endif %}", html.index('class="balance-bar-legend"', start)) + len("{% endif %}")
    assert html.index('<div class="hero-totals"') < start < html.index("{% endblock %}", start)
    out = app.jinja_env.from_string(html[start:end]).render(
        project={"budget_target": target, "spent": spent})
    if pct is None:
        assert "hero-bar" not in out
        return
    assert f"{pct}% מהיעד נוצל</span>" in out
    assert f"width: {min(pct, 100)}%;" in out
    assert ("danger" in out) == danger


@pytest.mark.parametrize("chips,columns", [(2, 2), (3, 3), (4, 2), (5, 3), (1, 1)])
def test_the_squares_share_the_row_evenly(chips, columns):
    from backend.app import app
    html = _read("frontend/templates/project_detail.html")
    rule = html[html.index("{% set chip_count"):html.index("{% set columns")]
    rule = html[html.index("{% set columns"):html.index("%}", html.index("{% set columns")) + 2]
    out = app.jinja_env.from_string(rule + "{{ [columns, 1] | max }}").render(chip_count=chips)
    assert out.strip() == str(columns)


def test_the_compare_page_is_one_table():
    """Matan (30.9) chose a table: a row per month, a column per type. With a card
    per month the balance overwrote "savings" at phone width. In the browser:
    tests/browser/months_table.py."""
    html = re.sub(r"\{#.*?#\}", "", _read("frontend/templates/months.html"), flags=re.S)
    # A table per year (inside the loop over the years), with the year above it
    # and not inside it
    loop = html[html.index("groupby('year')"):]
    assert loop.index('class="year-title"') < loop.index('<table class="months-table">')
    assert "year-row" not in html
    # The current year open, previous years only as a heading that opens
    # (Matan, 30.9)
    assert '<details class="year-block chart-card" {% if year_group.grouper >= today_year %}open{% endif %}>' in html
    assert '<summary class="year-title">' in html
    assert "showMoreMonths" not in html
    heads = re.findall(r'<th scope="col"[^>]*>([^<]+)</th>', html)
    assert heads == ["חודש", "הכנסות", "הוצאות", "חיסכון", "מאזן"]
    assert "month-stats" not in html and "month-link" not in html


def test_a_receipt_does_not_make_the_row_taller():
    """Matan (30.9). In the browser: tests/browser/receipt_row_height.py."""
    home = _read("frontend/templates/index.html")
    row = home[home.index('<div class="tx-amount-row">'):]
    row = row[:row.index("</div>")]
    assert 'class="receipt-badge"' in row, "the pin isn't on the same line as the amount"
    css = re.sub(r"/\*.*?\*/", "", _read("frontend/static/css/style.css"), flags=re.S)
    rule = css[css.index(".tx-amount-row .receipt-badge {"):]
    rule = rule[:rule.index("}")]
    assert "margin-block: -4px" in rule


def test_the_compare_chart_reads_right_to_left():
    """Matan (30.9): the chart on the comparison page reversed: right to left."""
    js = _strip_comments(_read("frontend/static/js/months.js"))
    scales = js[js.index("scales: {"):]
    assert re.search(r"x:\s*\{\s*reverse:\s*true", scales)
    assert "position: 'right'" in scales[:scales.index("}\n            }")]


def test_within_each_month_income_is_on_the_right_then_expenses_then_savings():
    """Matan (30.9). `Chart.js` lays out the bars in each month left to right, so
    the list in the code is reversed: savings, expenses, income."""
    js = _read("frontend/static/js/months.js")
    labels = re.findall(r"label:\s*'([^']+)'", js[js.index("datasets: ["):])[:3]
    assert labels == ["חיסכון", "הוצאות", "הכנסות"]
    assert "maxRotation: 0" in js


def test_the_compare_chart_fills_its_card():
    """Matan (30.9): the chart and the text larger, and the chart wide, not tall."""
    css = _read("frontend/static/css/style.css")
    # wide and not tall (Matan, 30.9)
    assert ".line-chart-wrap.compare-chart-wrap { margin-inline: -10px; }" in css
    assert "compare-chart-wrap { height" not in css
    js = _read("frontend/static/js/months.js")
    assert "font: { size: 13 }" in js and "font: { size: 14 }" in js
    assert "SHORT_MONTHS[d.month]" in js, "at the large font size the full names stick together"


def test_the_chart_canvas_fills_its_wrap_in_safari_rtl():
    """Matan (30.9): on iPhone the chart took only about 2/3 of the width. In Safari
    in RTL the canvas (300px by default) gets a computed `margin-left`, and
    Chart.js subtracts it from the width: the chart was stuck at 300. An explicit
    width on the canvas cancels this."""
    css = _read("frontend/static/css/style.css")
    rule = re.search(r"\.line-chart-wrap canvas,\s*\.doughnut-wrap canvas\s*\{([^}]*)\}", css)
    assert rule, "a width rule for the charts' canvas is missing"
    assert "width: 100% !important" in rule.group(1)


def test_the_savings_legend_says_of_what():
    """Matan (30.9): "חסכתם 67%" doesn't say of what; like expenses, "of income"."""
    html = _read("frontend/templates/index.html")
    assert "חסכתם {{ saved_pct }}% מההכנסות</span>" in html


def test_all_transactions_can_be_filtered_by_type():
    """Matan (30.9, idea 5): all / expenses / income / savings above the search:
    only the types present this month, and together with the search (not
    instead of it)."""
    tpl = _read("frontend/templates/month.html")
    assert 'class="date-chip tx-type-chip active" data-type=""' in tpl
    assert "selectattr(0, 'in', present)" in tpl
    assert "tx_types | length > 1" in tpl
    js = _strip_comments(_read("frontend/static/js/month.js"))
    assert "(!type || row.dataset.type === type)" in js
    assert "(!q || desc.toLowerCase().indexOf(q) !== -1)" in js
    assert "count.dataset.total" in js


def test_the_category_row_filters_several_categories_together():
    """Matan (30.9, option A): "all expenses" or several categories together."""
    tpl = _read("frontend/templates/month.html")
    assert 'data-cat-key="{{ tx.get(\'cat_key\', \'\') }}"' in tpl
    assert 'class="date-chip tx-cat-chip active" data-cat=""' in tpl
    js = _strip_comments(_read("frontend/static/js/month.js"))
    assert "(!cats.size || cats.has(row.dataset.catKey))" in js
    assert "pressChip(catChip, !catChip.classList.contains('active'))" in js


def test_the_form_date_chips_ignore_other_chips_on_the_page():
    """The filter buttons wear `.date-chip`. When the form grabbed all of them,
    tapping "expenses" changed the date in the form, and marked all the buttons
    as active."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    assert "querySelectorAll('.date-chip')" not in js
    assert js.count("querySelectorAll('.date-quick-chips .date-chip')") == 2


def test_the_plus_on_a_project_page_starts_in_that_project():
    """Matan (30.9, idea 8): the project is already selected, and the type is one
    it tracks."""
    tpl = _read("frontend/templates/project_detail.html")
    assert '<span id="sfPageProject" data-project-id="{{ project.id }}" hidden></span>' in tpl
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    assert "document.getElementById('sfPageProject')" in js
    assert "setType(type, undefined, undefined, project ? project.id : undefined)" in js
    assert ": project.track_income ? 'income' : 'savings'" in js
    # switching type doesn't reset the project
    assert "setType('income', undefined, undefined, txProject.value)" in js


def test_tapping_a_month_in_the_compare_chart_opens_it():
    """Matan (30.9, idea 10): by the whole column, not just the bar that was hit."""
    js = _strip_comments(_read("frontend/static/js/months.js"))
    assert "getElementsAtEventForMode(evt, 'index', { intersect: false }, true)" in js
    assert "'/month?year=' + d.year + '&month=' + d.month" in js


def test_settings_has_no_projects_box():
    """Matan (1.10): the "projects" box in settings duplicated the projects page: a
    list, a create link, a short edit and "full management". Removed, with its code."""
    settings = _read("frontend/templates/settings.html")
    assert '<span class="group-title">פרויקטים</span>' not in settings
    assert "edit-project-settings-btn" not in settings
    sjs = _read("frontend/static/js/settings.js")
    assert "edit-project-settings-btn" not in sjs and "proj-edit-save" not in sjs


def test_pull_to_refresh_only_in_the_installed_app_and_only_from_the_top():
    """Matan (30.9, idea 14). Regular Safari has it by itself; doubled it would
    refresh twice."""
    js = _strip_comments(_read("frontend/static/js/pwa.js"))
    part = js[js.index("const TRIGGER = SAFE + 58"):]
    assert "if (!isStandalone || !('ontouchstart' in window)) return;" in js
    assert "return busy || window.scrollY > 0" in part
    assert "if (dy < 0 || Math.abs(dx) > Math.abs(dy))" in part
    assert "'.modal-overlay.open, .transaction-item.open, .confirm-overlay.open, '" in part
    assert "document.querySelector(OPEN_WINDOWS)" in part
    assert "document.querySelector('main[data-soft-reload]') && window.softReload" in part


def test_the_category_card_draws_the_average_and_this_month_lighter():
    """Matan (30.9, round 6 item 1): "by category" on the comparison page."""
    js = _strip_comments(_read("frontend/static/js/months.js"))
    assert "c.avg == null ? [] : c.values.map(() => c.avg)" in js
    assert "(isNow && i === months.length - 1) ? BAR_NOW : BAR" in js
    tpl = _read("frontend/templates/months.html")
    assert "'cat_trend': cat_trend" in tpl and 'id="categoryChart"' in tpl


def test_the_search_screen_sits_outside_main_and_rows_open_for_editing():
    """Matan (30.9, round 6 item 2): outside `main` so a soft refresh doesn't close
    it, and below the edit sheet so tapping a result opens it above."""
    # The screen moved to `_search_screen.html` (Matan, 12.10: also on the month
    # page); on both pages it is included in the scripts block, outside `main`
    assert 'id="searchScreen"' in _read("frontend/templates/_search_screen.html")
    for page in ("index.html", "month.html"):
        tpl = _read("frontend/templates/" + page)
        assert tpl.index('{% include "_search_screen.html" %}') > tpl.index("{% block scripts %}"), page
        assert 'id="searchOpen"' in tpl, page
    js = _strip_comments(_read("frontend/static/js/search.js"))
    assert "li.className = 'cat-tx-row'" in js and "d.id = tx.id" in js
    assert "window.addEventListener('sf:refreshed'" in js
    css = _read("frontend/static/css/style.css")
    assert "position: fixed; inset: 0; z-index: 95;" in css


def test_a_finished_project_is_not_offered_but_an_old_transaction_keeps_it():
    """Matan (30.9, round 6 item 3). Without this, editing the amount of a
    transaction in a finished project would move it out of it into household
    expenses: it simply wasn't in the list."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    assert "window.sfFetchList('/api/projects?include_archived=1')" in js
    assert "(p[trackKey] && !p.archived)" in js
    assert "|| (keepOriginal && p.id === selectedProjectId)" in js


def test_a_finished_project_is_reopened_not_returned_to_the_active():
    """Matan (30.9): "back to active" wasn't liked: "reopen"."""
    for f in ("frontend/templates/project_edit.html", "frontend/templates/project_detail.html",
              "frontend/static/js/core.js"):
        assert "לפעילים" not in _read(f), f
    assert "פתיחה מחדש" in _read("frontend/templates/project_detail.html")
    assert "'הפרויקט נפתח מחדש'" in _read("frontend/static/js/core.js")


def test_categories_can_be_dragged_into_order():
    """Matan (30.9, round 6 item 5). Listening on document: moving the row in the
    DOM released `setPointerCapture` and the drag stopped after one step,
    without saving."""
    tpl = _read("frontend/templates/settings.html")
    assert 'class="cat-drag-handle" aria-hidden="true"' in tpl
    assert 'class="cat-move-btn cat-move-up"' in tpl        # the arrows stay
    js = _strip_comments(_read("frontend/static/js/settings.js"))
    part = js[js.index("closest('.cat-drag-handle')"):]
    assert "document.addEventListener('pointermove', move)" in part
    assert "setPointerCapture" not in part
    assert "if (after !== before) persistCategoryOrder(panel);" in part
    css = _read("frontend/static/css/style.css")
    assert "touch-action: none; user-select: none;" in css


def test_a_new_transaction_is_checked_before_it_is_saved():
    """Matan (30.9, round 6 items 6–7): only a new transaction, not an edit. A
    network failure in the check doesn't stop the save."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    assert "if (!editId && precheckPassed !== precheckKey(payload))" in js
    assert ".catch(function () { return {}; })" in js
    assert "confirmText: 'להוסיף בכל זאת'" in js and "cancelText: 'לתקן'" in js
    # the check before the save itself, not after it
    assert js.index("precheckPassed !== precheckKey(payload)") < js.index("submitLabel.textContent = 'שומר…'")


def test_an_edit_can_be_undone_from_the_toast():
    """Matan (30.9, round 6 item 10): in the edit sheet, since 5.10 on the home page
    too. Not after "update from now on": the undo would restore only half the
    change."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    assert "Object.assign({}, spec.body, { if_match: spec.if_match })" in js
    assert "!(followUp && (followUp.series || followUp.error))" in js
    assert "undoSpec(myOriginal, data.transaction, message)" in js
    assert "{ label: 'ביטול', onClick: function () { runUndo(spec); } }" in js


def test_the_date_chips_are_today_and_yesterday():
    """"The day before" was added on 30.9 (round 6 item 11) and removed on 11.10:
    Matan: "it can be removed"."""
    tpl = _read("frontend/templates/base.html")
    chips = tpl[tpl.index('class="date-quick-chips"'):]
    chips = chips[:chips.index("</div>")]
    assert [d for d in ("0", "-1", "-2") if f'data-days="{d}"' in chips] == ["0", "-1"]
    assert ">שלשום<" not in chips


def test_the_month_header_stays_on_top_once_the_balance_scrolls_away():
    """Matan (30.9, round 6 item 12). Only when the balance left upwards, and the
    final amount, not the count-up's intermediate number."""
    js = _strip_comments(_read("frontend/static/js/month.js"))
    assert "bar.hidden = e.isIntersecting || e.boundingClientRect.top > 0;" in js
    assert "window.sfMoney(valueEl.dataset.countup)" in js
    assert "window.addEventListener('sf:refreshed', build);" in js
    css = _read("frontend/static/css/style.css")
    assert "padding: calc(env(safe-area-inset-top, 0px) + 10px) 18px 10px;" in css


def test_a_slow_page_change_shows_a_skeleton_and_a_fast_one_does_not():
    """Matan (30.9, round 6 item 14): only after a quarter of a second, only for
    pages that have a shape, and not stuck when going back from the browser's
    cache."""
    js = _strip_comments(_read("frontend/static/js/skeleton.js"))
    assert "const DELAY = 250;" in js
    for page in ("'/'", "'/month'", "'/months'", "'/projects'", "'/settings'"):
        assert page + ":" in js
    assert "window.addEventListener('pageshow', hide);" in js
    assert "if (e.defaultPrevented" in js
    assert "js/skeleton.js" in _read("frontend/templates/base.html")


def test_every_page_is_a_pixel_taller_than_the_screen():
    """Matan (30.9): in the installed app the bottom menu "rose" on the projects
    page, the only page shorter than the screen. `lvh` (the large viewport) and
    not `dvh`."""
    css = _read("frontend/static/css/style.css")
    body = css[css.index("\nbody {"):]
    body = body[:body.index("}")]
    assert "min-height: calc(100lvh + 1px);" in body
    assert "100dvh" not in body


def test_tapping_a_month_in_the_category_chart_opens_its_expenses():
    """Matan (30.9): a sheet with the category's expenses in that month."""
    js = _strip_comments(_read("frontend/static/js/months.js"))
    assert "chart.options.onClick = function (evt)" in js
    assert "const items = (current.items || [])[i] || [];" in js
    assert "'לא בוצעו עסקאות בקטגוריה בחודש זה'" in js
    assert "link.textContent = 'לעמוד החודש';" in js


def test_the_category_chart_title_label_and_month_link():
    """Matan (1.10): the heading "expenses by category", no black floating label on
    the chart, and "to the month page" opens the page from the top and not cut
    off at the breakdown."""
    tpl = _read("frontend/templates/months.html")
    assert '<h2 class="chart-title">הוצאות לפי קטגוריה</h2>' in tpl
    js = _strip_comments(_read("frontend/static/js/months.js"))
    cat = js[js.index("const chart = new Chart(catCanvas"):js.index("plugins: [valuesOnTop]")]
    assert "tooltip: { enabled: false }" in cat
    assert "link.href = '/month?year=' + m.year + '&month=' + m.month;" in js
    assert "#expense-breakdown';" not in js[js.index("function openMonth"):]
    css = _read("frontend/static/css/style.css")
    assert ".cat-trend-stats > div { display: flex; flex-direction: column; align-items: center; text-align: center; }" in css
    assert "const cell = (label, value) => '<div><span>' + label + '</span><b>' + value + '</b></div>';" in js
    assert "cell('החודש הכי יקר' + (c.max_label ? ' <span class=\"nowrap\">- ' + c.max_label + '</span>' : '')," in js


def test_the_month_strip_recenters_after_a_soft_reload():
    """Matan (1.10): the soft refresh replaces the hero with a strip that sits at
    the start: July and August on screen when the month is October. In the
    browser: tests/browser/month_strip_center.py."""
    js = _strip_comments(_read("frontend/static/js/month.js"))
    assert "centerStrip();" in js
    assert "window.addEventListener('sf:refreshed', centerStrip);" in js
    body = js[js.index("function centerStrip()"):js.index("centerStrip();")]
    assert "document.getElementById('monthStrip')" in body, "the strip is looked up again on every call, not kept from load"


# ─── Review of 1.10 ─────────────────────────────────────────────────────────

def test_closing_the_form_during_the_check_cancels_the_save():
    """The form was closed during "checking…": the transaction was saved anyway,
    with a receipt the close had deleted."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    part = js[js.index("runPrecheck(payload).then(function (ok) {"):]
    part = part[:part.index("requestSubmit();")]
    assert "if (!overlay.classList.contains('open')) return;" in part


def test_pull_to_refresh_leaves_open_windows_alone():
    """Inside search/a sheet, a downward drag is scrolling the list, not
    refreshing the page."""
    js = _strip_comments(_read("frontend/static/js/pwa.js"))
    for sel in (".search-screen:not([hidden])", ".icon-picker:not([hidden])",
                ".color-sheet:not([hidden])", ".confirm-overlay.open"):
        assert sel in js, sel
    assert "document.querySelector(OPEN_WINDOWS)" in js


def test_the_skeleton_rechecks_before_it_shows():
    js = _strip_comments(_read("frontend/static/js/skeleton.js"))
    part = js[js.index("timer = setTimeout(function () {"):]
    assert part.index("if (e.defaultPrevented) return;") < part.index("overlay = build(shape);")


def test_jumping_to_a_department_clears_the_sticky_bar():
    """Matan (2.10): from a card on home to the department on the month page: the
    top of the card was cut off under the sticky header (44px). The margin makes
    room for it. In the browser: tests/browser/home_card_jump.py."""
    css = _read("frontend/static/css/style.css")
    rule = css[css.index("#income-breakdown,\n#expense-breakdown,\n#savings-breakdown {"):]
    rule = rule[:rule.index("}")]
    assert "scroll-margin-top: calc(58px + env(safe-area-inset-top, 0px));" in rule
    sticky = css[css.index(".month-sticky {"):]
    sticky = sticky[:sticky.index("}")]
    assert "padding: calc(env(safe-area-inset-top, 0px) + 10px) 18px 10px;" in sticky, \
        "the sticky header's height changed: update the 58px"


def test_only_the_settings_list_edits_a_series_from_now():
    """Matan (2.10): editing from "recurring transactions" in settings, from the
    current month. Tapping January's transaction in January's list is editing
    January, as before."""
    js = _strip_comments(_read("frontend/static/js/transactions.js"))
    assert "editingSeries = !!(triggerEl && triggerEl.closest && triggerEl.closest('#recurringList'));" in js
    assert "[mySeries ? 'from_now=1' : '', confirmed ? 'confirm=1' : '']" in js
    assert js.count("editingSeries = false;") == 3, "declaration, add and duplicate reset it"
    app_src = _read("backend/app.py")
    gate = app_src[app_src.index('if (request.args.get("from_now")'):][:200]
    assert 'payload["is_recurring"]' in gate
    assert 'str(tx_date) == str(current.get("date"))[:10]' in gate, "changing the start date: an ordinary edit"


def test_the_home_page_has_no_budgets_card():
    """Matan (3.10): "I don't want budgets this month at all: it's redundant on the
    home page". The budgets themselves stayed on the month page."""
    import re
    html = re.sub(r"\{#.*?#\}", "", _read("frontend/templates/index.html"), flags=re.S)
    assert "home-budgets" not in html and "תקציבים החודש" not in html
    assert "get_home_budgets" not in _read("backend/app.py")
    assert 'id="budgetsOpen"' in _read("frontend/templates/month.html"), "setting budgets on the month page stays"


def test_a_month_category_links_to_its_history():
    """Matan (3.10, idea 23): from a category on the month page to its chart on the
    comparison page, already selected. Expenses only: the chart there is of
    expenses. In the browser: checked."""
    html = _read("frontend/templates/month.html")
    exp = html[html.index('id="expense-breakdown"'):]
    exp = exp[:exp.index("</section>")]
    assert "url_for('months', cat=item.category_id) }}#cat-trend" in exp
    assert "{% if item.category_id and not item.is_project %}" in exp
    rest = html.replace(exp, "")
    assert "#cat-trend" not in rest, "income and savings have no such chart"
    assert 'id="cat-trend"' in _read("frontend/templates/months.html")
    js = _strip_comments(_read("frontend/static/js/months.js"))
    assert "new URLSearchParams(location.search).get('cat')" in js
    assert "catTrend.categories.some(x => x.key === wanted) ? wanted : null" in js, "a category with no history: the first one"


def test_the_chosen_category_in_the_compare_chart_stands_out():
    """Matan (3.10): "make the category I pick a bit more highlighted". Only in
    this chart: the other buttons in the app stay as they are."""
    css = _read("frontend/static/css/style.css")
    rule = css[css.index(".cat-trend-chips .tx-cat-chip.active {"):]
    rule = rule[:rule.index("}")]
    assert "background: var(--gradient-gold);" in rule and "color: #FFFFFF;" in rule
    assert "font-weight: 700;" in rule


def test_the_price_change_line_in_settings():
    """Matan (3.10, idea 40): "₪5,000 ← ₪5,500 מאוקטובר"."""
    from backend.app import app
    html = _read("frontend/templates/settings.html")
    start = html.index("{% set change = (price_changes or {}).get(tx.id) %}")
    snippet = html[start:html.index("{% endif %}", start) + len("{% endif %}")]
    months = ["", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט",
              "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
    with app.test_request_context():
        out = app.jinja_env.from_string(snippet).render(
            tx={"id": "t", "amount": 5500}, hebrew_months=months,
            price_changes={"t": {"old": 5000.0, "since": "2026-10-01"}})
        none = app.jinja_env.from_string(snippet).render(
            tx={"id": "x", "amount": 5500}, hebrew_months=months, price_changes={})
    assert "₪5,000 ← ₪5,500 מאוקטובר" in out
    assert "recurring-price-change" not in none


def test_the_projects_page_says_projects_are_outside_the_month_balance():
    """Matan (3.10): "an explanation line that the project's transactions don't
    affect the monthly balance". At the top of the page, visible even when there
    are no projects. In the app's term: "מאזן החודש"."""
    html = _read("frontend/templates/projects.html")
    hero = html[html.index("{% block hero %}"):html.index("{% endblock %}", html.index("{% block hero %}"))]
    assert '<p class="hero-note">העסקאות של הפרויקטים לא נכללות במאזן החודש' in hero


def test_the_compare_page_has_a_range_for_both_charts():
    """Matan (3.10, idea 24): 3 months / half a year (default, as before) / a year,
    for both charts together, and the average and the most expensive month
    recomputed. In the browser: checked with 8 months: an average from finished
    months only, in every range."""
    html = _read("frontend/templates/months.html")
    assert 'data-range="3"' in html and 'data-range="12"' in html
    assert 'class="date-chip range-chip active" data-range="6"' in html, "the default: half a year"
    js = _strip_comments(_read("frontend/static/js/months.js"))
    assert "let RANGE = 6;" in js
    assert "last6 = trendData.slice(-RANGE);" in js and "months = allMonths.slice(-RANGE);" in js
    assert "const c = inRange(" in js, "the statistics by the range, not by the 12 months"
    assert "(m.year < ty || (m.year === ty && m.month < tm))" in js, "average: only from finished months"
    assert "trend[-12:]" in _read("backend/app.py")


def test_swiping_anywhere_on_the_month_changes_month():
    """Matan (11.10): a swipe anywhere on the page pages a month; until then only on
    the balance, and on the rest of the page the phone went back home. Not from
    what has its own swipe (transaction rows, the strip), and not from the edge
    (the system's). In the browser: checked."""
    js = _strip_comments(_read("frontend/static/js/month.js"))
    part = js[js.index("const SLIDE ="):]
    assert "'.transaction-item, .cat-tx-row, .recurring-row, .month-strip," in part
    assert "if (t.clientX < EDGE || t.clientX > window.innerWidth - EDGE) return;" in part
    move = part[part.index("document.addEventListener('touchmove'"):]
    move = move[:move.index("}, { passive: false });")]
    assert move.index("if (axis !== 'x') { sx = null; return; }") < move.index("e.preventDefault();")
    assert "aria-label=\"' + (isOlder ? 'חודש קודם' : 'חודש הבא') + '\"" in part


def test_changing_month_is_a_smooth_swap_not_a_page_load():
    """Matan (11.10): "make the paging smooth". The switch replaces the content
    without a page load (`softNavigate`), with the new content already fetched
    during the swipe."""
    js = _strip_comments(_read("frontend/static/js/month.js"))
    part = js[js.index("const SLIDE ="):]
    assert "window.location.href" not in part
    assert "window.softNavigate(url, text || undefined)" in part
    assert "if (target) prefetch(target.href);" in part
    core = _read("frontend/static/js/core.js")
    nav = core[core.index("window.softNavigate = function"):]
    nav = nav[:nav.index("\n};")]
    assert "history.replaceState(null, '', url)" in nav and "window.location.href = url" in nav



def test_pull_to_refresh_moves_the_page_like_an_iphone():
    """Matan (13.10): "make it look natural like on iPhone: the screen comes down
    like a spring, with a loading indicator". The page itself moves (not just a
    floating circle), with spring resistance; a spinner with 8 spokes that
    appear with the pull; during the refresh the page waits down, then returns
    with a soft bounce."""
    js = _strip_comments(_read("frontend/static/js/pwa.js"))
    part = js[js.index("const TRIGGER = SAFE + 58"):js.index("function sfDockOffset") if "function sfDockOffset" in js else None]
    assert "(1 - 1 / (x * 0.55 / RANGE + 1)) * RANGE" in part
    assert "root.style.setProperty('--ptr-y', dist + 'px');" in part
    assert "for (let k = 0; k < 8; k++)" in part and "place(HOLD, true)" in part
    css = _read("frontend/static/css/style.css")
    assert "animation: ptr-spin .8s steps(8) infinite" in css
    # In CSS and not on the elements: the soft refresh replaces them, and the
    # page would jump up
    assert "body.ptr-active > .page-hero, body.ptr-active > .main-content" in css


def test_add_to_home_screen_stays_until_opened_from_the_icon():
    """Matan (13.10): "something that shows up permanently, until the user does
    it"; ✕ hides it until the next visit; above the bottom menu; no "Safari":
    there are other browsers too."""
    js = _strip_comments(_read("frontend/static/js/pwa.js"))
    part = js[js.index("getElementById('installBar')"):js.index("const START = 8;")]
    assert "fetch('/api/app-installed'" in part
    assert "if (bar.hasAttribute('data-installed')) return;" in part
    assert "sessionStorage.setItem('sf_install_hidden', '1')" in part
    assert "localStorage" not in part                     # not "forever"
    assert "ספארי" not in part and "Safari" not in part
    assert "deferredPrompt.prompt()" in part             # Android: direct install
    base = _read("frontend/templates/base.html")
    assert 'id="installBar"' in base and "app_installed" in base
    assert base.index('id="installBar"') > base.index("</main>")   # the soft refresh doesn't delete it
    import backend.app as app_module
    src = _read("backend/app.py")
    assert '{"app_installed": {user["id"]: clock.today().isoformat()}}' in src
