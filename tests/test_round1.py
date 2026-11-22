"""Round 1 from the ideas list (Matan, 12.10): 12 small polishes.

Greeting by the hour · commas in the amount · no ".00" · no duplicate on a
recurring transaction · "אין שינויים" · "חלק מעסקה קבועה" · password eye ·
sign-out confirmation · "שליחה" for the invite · sort by amount · "הבא: 1.11" ·
no single-column "לפי בן משפחה" chart.
"""
import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from backend import supabase_config as db
from backend.wording import greeting, next_charge_label

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "frontend/static/js"
_T = _ROOT / "frontend/templates"
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _read(p):
    return p.read_text(encoding="utf-8")


# ─── Greeting by the hour ───────────────────────────────────────────────

@pytest.mark.parametrize("hour,text", [(5, "בוקר טוב"), (11, "בוקר טוב"), (12, "צהריים טובים"),
                                       (16, "צהריים טובים"), (17, "ערב טוב"), (21, "ערב טוב"),
                                       (22, "לילה טוב"), (0, "לילה טוב"), (4, "לילה טוב")])
def test_the_greeting_follows_the_hour(hour, text):
    assert greeting(hour) == text


def test_the_home_page_greets_by_the_israeli_clock():
    # the home hero dropped the greeting line on 22.11 (Matan: a shorter hero, so
    # the week chart fits the first screen); the function stays for later use
    assert '{{ greeting() }}' not in _read(_T / "index.html")
    app_src = _read(_ROOT / "backend/app.py")
    assert 'app.jinja_env.globals["greeting"] = lambda: greeting(clock.now().hour)' in app_src


# ─── Commas in the amount, no ".00" ────────────────────────────────────

def _format(value, caret):
    """Runs the real formatAmount from transactions.js in node."""
    src = _read(_JS / "transactions.js")
    fn = src[src.index("    function amountValue() {"):src.index("    function fitAmount() {")]
    script = ("const txAmount={value:%s,selectionStart:%d,setSelectionRange(a){this.sel=a}};\n%s\n"
              "formatAmount(true);console.log(JSON.stringify([txAmount.value, txAmount.sel ?? null, amountValue()]));"
              % (json.dumps(value), caret, fn))
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.parametrize("typed,caret,shown,value", [
    ("48000", 5, "48,000", 48000),
    ("4800.5", 6, "4,800.5", 4800.5),
    ("1234567.891", 11, "1,234,567.89", 1234567.89),   # up to two digits after the point
    ("0012", 4, "12", 12),
    ("12.3.4", 6, "12.34", 12.34),                      # one point
    ("0.5", 3, "0.5", 0.5),
])
def test_the_amount_gets_commas_while_typing(typed, caret, shown, value):
    got, _, num = _format(typed, caret)
    assert got == shown and num == value


def test_the_caret_stays_after_the_same_digit():
    # the caret after the 8 in "4800" → "4,800": after the 8, that is, after "4,8"
    got, sel, _ = _format("4800", 2)
    assert got == "4,800" and sel == 3


def test_the_field_is_text_with_a_number_keyboard_and_no_cents_placeholder():
    base = _read(_T / "base.html")
    field = base[base.index('id="txAmount"'):]
    field = field[:field.index(">")]
    assert 'inputmode="decimal"' in field and 'placeholder="0"' in field
    assert 'type="text" id="txAmount"' in base
    tx = _read(_JS / "transactions.js")
    assert "const AMOUNT_PLACEHOLDER_DEFAULT = '0';" in tx
    assert "const amount = amountValue();" in tx                # sending without commas
    assert "formatAmount(false);        // a value written by code" in tx  # editing and scanning


# ─── Editing ────────────────────────────────────────────────────────────

def test_recurring_transactions_have_no_duplicate_button():
    tx = _read(_JS / "transactions.js")
    assert "duplicateBtn.hidden = !!(tx.isRecurring || tx.recurringParentId || editingSeries);" in tx


def test_save_is_grey_until_something_changes():
    tx = _read(_JS / "transactions.js")
    assert "editSnapshot = formSnapshot();" in tx
    # since 13.10: the same caption, just disabled/enabled ("don't write no changes")
    assert "if (editId) { submitLabel.textContent = 'שמירת השינויים'; return; }" in tx
    assert "'אין שינויים'" not in tx
    assert "if (isUnchanged()) return;" in tx
    assert "label.submit-btn.is-unchanged" in _CSS


def test_an_occurrence_links_to_its_recurring_transaction():
    base = _read(_T / "base.html")
    assert 'id="txSeriesLink"' in base and "חלק מעסקה קבועה" in base
    tx = _read(_JS / "transactions.js")
    assert "const seriesId = tx.recurringParentId ||" in tx
    assert "seriesLink.href = '/settings?series=' + encodeURIComponent(seriesId);" in tx
    settings = _read(_JS / "settings.js")
    assert "new URLSearchParams(location.search).get('series')" in settings and "row.click();" in settings


def test_an_installment_says_which_payment_it_is():
    # "תשלום 3 מתוך 12" in the edit sheet, from the numbers every row carries
    base = _read(_T / "base.html")
    assert 'id="txSeriesText"' in base
    tx = _read(_JS / "transactions.js")
    assert "'תשלום ' + instNo + ' מתוך ' + instTotal" in tx
    assert "installmentNo:       row.dataset.installmentNo || null," in tx
    for name in ("index.html", "month.html", "project_detail.html", "settings.html"):
        page = _read(_T / name)
        assert page.count('data-recurring-parent-id=') == page.count('data-installment-no="{{ tx.installment_no'), name
    search = _read(_JS / "search.js")
    assert "d.installmentNo = tx.installment_no" in search


def test_save_does_not_cover_the_last_row_on_iphone():
    # Safari counts the sticky `bottom` from inside the sheet's padding: the
    # space under Save is a spacer inside the form, not padding on the sheet
    assert "#modalOverlay .tx-form::after { content: \"\"; display: block; height: calc(10px + env(safe-area-inset-bottom, 0px)); }" in _CSS
    assert "#modalOverlay .modal-sheet { padding-bottom: 8px; }" in _CSS


# ─── Password and sign-out ──────────────────────────────────────────────

def test_every_password_page_loads_the_eye():
    for name in ("login.html", "reset_password.html", "settings.html"):
        assert "js/password-eye.js" in _read(_T / name), name
    eye = _read(_JS / "password-eye.js")
    assert "input.type = show ? 'text' : 'password';" in eye
    assert "autocomplete" not in eye.split("*/", 1)[1]           # don't touch the password manager


def test_logging_out_asks_first():
    settings = _read(_JS / "settings.js")
    block = settings[settings.index("// ── Confirmation before signing out"):]
    assert "window.appConfirm(" in block and "form.submit();" in block
    assert 'method="POST" action="{{ url_for(\'logout\') }}"' in _read(_T / "settings.html")


# ─── Invite, sort, next charge, one family member ──────────────────────

def test_the_invite_is_shared_where_possible():
    settings = _read(_JS / "settings.js")
    block = settings[settings.index("const SHARE_LABEL"):settings.index("// ── Join another family")]
    assert "navigator.share({ text: msg })" in block and "err.name === 'AbortError'" in block
    assert "window.copyToClipboard(msg, inviteCode)" in block


def test_all_transactions_can_be_sorted_by_amount():
    month = _read(_T / "month.html")
    assert 'data-sort="amount"' in month and 'data-sort="date"' in month
    js = _read(_JS / "month.js")
    assert "Number(b.dataset.amount) - Number(a.dataset.amount)" in js
    assert "sortAllTx('date');" in js                            # not saved


_TODAY = dt.date(2026, 10, 12)


def _tpl(date, freq, end=None):
    return {"date": date, "recurring_frequency": freq, "recurring_end_date": end}


@pytest.mark.parametrize("tpl,expected", [
    (_tpl("2026-09-01", "monthly_1"), dt.date(2026, 11, 1)),
    (_tpl("2026-09-15", "monthly_15"), dt.date(2026, 10, 15)),
    (_tpl("2026-08-12", "monthly_same"), dt.date(2026, 10, 12)),         # today
    (_tpl("2026-01-31", "monthly_same"), dt.date(2026, 10, 31)),
    (_tpl("2026-10-05", "weekly"), dt.date(2026, 10, 12)),
    (_tpl("2026-10-01", "biweekly"), dt.date(2026, 10, 15)),
    (_tpl("2026-11-03", "monthly_same"), dt.date(2026, 11, 3)),          # hasn't started yet
    (_tpl("2026-01-01", "monthly_1", end="2026-10-01"), None),          # ended
])
def test_the_next_charge(tpl, expected):
    assert db.next_recurring_date(tpl, _TODAY) == expected


def test_the_next_charge_label():
    assert next_charge_label(_TODAY, _TODAY) == "הבא: היום"
    assert next_charge_label(dt.date(2026, 10, 13), _TODAY) == "הבא: מחר"
    assert next_charge_label(dt.date(2026, 11, 1), _TODAY) == "הבא: 1.11"
    assert next_charge_label(dt.date(2027, 1, 1), _TODAY) == "הבא: 1.1.27"
    assert next_charge_label(None, _TODAY) == ""


def test_one_member_gets_no_chart():
    import inspect
    from backend import app as app_module
    src = inspect.getsource(app_module.month_view)
    assert "if len(mb) >= 2:" in src


def test_the_sheet_fits_above_the_iphone_keyboard():
    # iOS doesn't shrink the page for the keyboard: while a field in the sheet has
    # focus, the overlay takes the visible screen's size, so the sheet scrolls to
    # its last rows (installments, Save) instead of ending behind the keyboard
    tx = _read(_JS / "transactions.js")
    assert "const covered = window.innerHeight - vv.height > 60;" in tx
    assert "overlay.style.setProperty('--sheet-room', Math.round(vv.height) + 'px');" in tx
    assert "vv.addEventListener('resize', fit);" in tx
    assert "max-height: calc(var(--sheet-room, 100dvh) * 0.96)" in _CSS


def test_nothing_peeks_out_under_save():
    # Matan's screenshot (21.11): "תשלומים" showed in the strip under Save. While
    # content goes on below, the strip is covered; with the keyboard open Save
    # sits right on top of it
    tx = _read(_JS / "transactions.js")
    assert "submitBtn.classList.toggle('is-stuck', below > 2);" in tx
    assert "overlay.classList.add('kb-fit');" in tx
    assert "#modalOverlay label.submit-btn.is-stuck::after {" in _CSS
    assert "#modalOverlay.kb-fit label.submit-btn { bottom: 8px; }" in _CSS


def test_save_ring_does_not_cut_the_field_above():
    # the 12px ring cut the project field's bottom edge with only 8px between them
    assert "#modalOverlay label.submit-btn { margin-top: 18px; }" in _CSS


def test_week_card_names_an_installment():
    # "תשלום 1 מתוך 2" instead of "קבועה" in the week card (Matan, 22.11)
    from backend.supabase_config import week_from_rows
    import datetime
    today = datetime.date(2026, 11, 22)
    rows = [{"amount": 150, "date": "2026-11-22", "description": "", "is_recurring": True,
             "recurring_parent_id": None, "installments": 2, "installment_no": 1,
             "categories": {"name": "מסעדות", "icon": "🥂"}}]
    week = week_from_rows(rows, False, today)
    day = [d for d in week["days"] if d["today"]][0]
    assert day["recurring"][0]["installment"] == "תשלום 1 מתוך 2"
    assert week["recurring_label"] == "בתשלומים"
    card = _read(_T / "_week_card.html")
    assert "{{ t.installment or 'קבועה' }}" in card


def test_week_card_rows_open_the_edit_sheet():
    # tapping a transaction in "this week" opens the edit sheet (Matan, 22.11)
    from backend.supabase_config import week_from_rows
    import datetime
    rows = [{"id": "t1", "amount": 95, "date": "2026-11-22", "description": "ערב",
             "category_id": "c1", "is_recurring": False, "recurring_parent_id": None,
             "user_id": None, "categories": {"name": "מסעדות", "icon": "🥂"}}]
    week = week_from_rows(rows, False, datetime.date(2026, 11, 22))
    item = [d for d in week["days"] if d["today"]][0]["transactions"][0]
    assert item["id"] == "t1" and item["date"] == "2026-11-22" and item["category_id"] == "c1"
    card = _read(_T / "_week_card.html")
    assert 'class="week-tx' in card and 'data-id="{{ t.id }}"' in card
    tx = _read(_JS / "transactions.js")
    assert tx.count("'.transaction-item, .cat-tx-row, .recurring-row, .week-tx'") == 2


def test_editing_an_installment_shows_installments_not_recurring():
    # Matan, 22.11: an installment opened for editing said "recurring". Now the
    # installments switch is on and locked with "תשלום X מתוך Y", the recurring
    # switch is hidden, and saving keeps the series' recurring fields
    tx = _read(_JS / "transactions.js")
    assert "if (editId && editingInstallment) {" in tx
    assert "instHint.textContent = 'תשלום ' + editingInstallment.no + ' מתוך ' + editingInstallment.total;" in tx
    assert "payload.is_recurring        = !!editingOriginal.isRecurring;" in tx
    # `hidden` really hides inside the sheet
    assert "#modalOverlay [hidden] { display: none !important; }" in _CSS


def test_deleting_an_installment_speaks_of_payments():
    # Matan, 22.11: the delete question called an installment "a recurring
    # transaction". It now names the payment and counts from the payment numbers
    tx = _read(_JS / "transactions.js")
    assert "title:       'זו עסקה בתשלומים'," in tx
    assert "const left = inst.total - inst.no + 1;" in tx
    assert "confirmText: 'רק את התשלום הזה'," in tx


def test_series_delete_is_one_question_with_three_answers():
    # Matan, 22.11: one question instead of two. The destructive button says what
    # it deletes ("מחיקת 10 תשלומים (3 עד 12)"), returns 'extra', and maps to
    # mode=later; "only this one" maps to mode=one
    tx = _read(_JS / "transactions.js")
    assert "extraText:   'מחיקת ' + left + ' תשלומים (' + inst.no + ' עד ' + inst.total + ')'," in tx
    assert "if (answer === 'extra') return sendSeriesDelete(txData, row, 'later', later, onFail);" in tx
    core = _read(_JS / "core.js")
    assert "closeConfirm('extra')" in core
    assert 'id="confirmExtra"' in _read(_T / "base.html")


def test_audit_fixes_of_22_11():
    # year recap: the bar's height is a share of the row above its label
    assert ".yr-bar { display: grid; grid-template-rows: minmax(0, 1fr) auto;" in _CSS
    # project budget "12000", not "12000.0"
    assert "{{ b | int if b == b | int else b }}" in _read(_T / "project_edit.html")
    # an installment purchase is named in the recurring list
    assert 'class="installment-tag">' in _read(_T / "settings.html")
    # the same row of checkboxes when creating a project as when editing one
    assert '<div class="track-row">' in _read(_T / "project_new.html")
    # a switch that is off is hollow
    assert ".pref-switch input:not(:checked) + .pref-slider { background: transparent;" in _CSS


def test_installment_left_counts_what_is_still_to_pay():
    # "נשארו ₪3,600" (Matan, 22.11): the first payment carries the remainder
    from backend.supabase_config import installment_left
    assert installment_left(4800, 12, "2026-09-04", 3) == 3600
    assert installment_left(4800, 12, "2026-09-04", 12) == 0
    assert installment_left(4800, 12, "2026-09-04", 0) == 4800
    assert installment_left(100, 3, "2026-09-04", 1) == 66.66      # 33.34 + 33.33 + 33.33
    assert installment_left(None, 12, "2026-09-04", 3) is None


def test_installments_have_their_own_list_and_colours_a_swatch():
    st = _read(_T / "settings.html")
    # two parts since 22.11: "קבועות" by type, then "בתשלומים" under a line
    assert "{% set installments = recurring | selectattr('installments') | list %}" in st
    assert '<h3 class="recurring-part-title{% if fixed %} has-line{% endif %}">בתשלומים</h3>' in st
    assert '<h3 class="recurring-part-title">קבועות</h3>' in st
    assert "נשארו ₪{{ inst.left | money }}" in st
    assert 'class="color-swatch owner-{{ idx }}"' in st
    tx = _read(_JS / "transactions.js")
    assert "' · נשארו ₪' + window.sfMoney(editingInstallment.left)" in tx


def test_bimonthly_and_yearly_frequencies():
    # Matan, 22.11: arnona/water/electricity every two months, insurance yearly
    from datetime import date
    from backend.supabase_config import _recurring_occurrences as occ
    from backend.app import _FREQUENCIES
    assert {"bimonthly", "yearly"} <= set(_FREQUENCIES)
    two = occ({"date": "2026-01-31", "recurring_frequency": "bimonthly"}, date(2026, 12, 31))
    assert [d.isoformat() for d in two] == ["2026-03-31", "2026-05-31", "2026-07-31", "2026-09-30", "2026-11-30"]
    year = occ({"date": "2024-02-29", "recurring_frequency": "yearly"}, date(2028, 3, 1))
    assert [d.isoformat() for d in year] == ["2025-02-28", "2026-02-28", "2027-02-28", "2028-02-29"]
    # monthly is unchanged
    month = occ({"date": "2026-10-23", "recurring_frequency": "monthly_same"}, date(2027, 1, 31))
    assert [d.isoformat() for d in month] == ["2026-11-23", "2026-12-23", "2027-01-23"]
    base = _read(_T / "base.html")
    assert '<option value="bimonthly">' in base and '<option value="yearly">' in base


def test_second_audit_round_of_22_11():
    assert ".cat-tx-row, .week-detail-list li.week-tx, .legend-item { min-height: 44px; }" in _CSS
    assert "--text-2xs:            0.75rem;" in _CSS
    core = _read(_JS / "core.js")
    assert "window.sfThousands = function (input) {" in core
    for name in ("project_edit.html", "project_new.html"):
        assert "data-thousands" in _read(_T / name), name
    assert 'data-add-type="expense"' in _read(_T / "months.html")


def test_switch_hints_and_my_colour():
    base = _read(_T / "base.html")
    assert 'עסקה קבועה<small class="switch-hint">חוזרת כל חודש</small>' in base
    assert 'תשלומים<small class="switch-hint">פריסה לחודשים</small>' in base
    st = _read(_T / "settings.html")
    assert "הצבע שלך" not in st and 'id="memberColorTitle">הצבע שלי</p>' in st


def test_third_round_of_22_11_shortcuts_one_budget_place_swipe_hint():
    month = _read(_JS / "month.js")
    assert "nav.className = 'month-sticky-nav';" in month and "chip.className = 'month-jump';" in month
    assert 'id="month-projects"' in _read(_T / "month.html")
    assert "#allTx, #month-projects { scroll-margin-top: calc(104px + env(safe-area-inset-top, 0px)); }" in _CSS
    tx = _read(_JS / "transactions.js")
    assert "const KEY = 'sf_swipe_hint';" in tx
    assert "content.style.transform = 'translateX(' + OPEN_X + 'px)';" in tx


def test_the_home_hero_is_shorter():
    # Matan, 22.11: no greeting line and tighter spacing, so the week chart fits
    # the first screen above the bottom bar
    assert 'class="hero-top hero-top-home"' in _read(_T / "index.html")
    assert ".hero-balance .hero-amount { font-size: 2.25rem; }" in _CSS


def test_the_recurring_list_has_no_hint_line():
    # Matan, 22.11: the row's chevron already says it opens; the line went
    assert "recurring-hint" not in _read(_T / "settings.html") and "recurring-hint" not in _CSS
