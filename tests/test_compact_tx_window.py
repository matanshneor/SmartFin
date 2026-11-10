"""חלון ההוספה והעריכה במסך אחד (מתן, 5.10).

הקטגוריות בגודל של היום; מה שהשתנה הוא כל השאר. ובדף הבית העריכה נפתחת
באותו חלון כמו בעמוד החודש — העריכה בתוך השורה הוסרה.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TPL = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
_JS = (_ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
_FORM = _TPL[_TPL.index('<form class="tx-form" id="txForm"'):_TPL.index("</form>")]


def test_the_home_page_edits_in_the_same_window():
    assert "openInlineEditor" not in _JS and "buildInlineEditor" not in _JS
    index = (_ROOT / "frontend/templates/index.html").read_text(encoding="utf-8")
    assert "tx-editor" not in index
    click = _JS[_JS.index("// ── Opening an edit by tapping an existing transaction"):][:900]
    assert "openEditModal(buildTxFromRow(row), row);" in click


def test_the_description_comes_right_after_the_categories():
    order = [_FORM.index('id="categoryGrid"'), _FORM.index('id="txDescription"'),
             _FORM.index('id="ownerGroup"'), _FORM.index('id="txDate"'), _FORM.index('id="txRecurring"')]
    assert order == sorted(order)


def test_editing_has_no_receipt_button():
    assert "receiptAttach" not in _TPL and "receiptAttach" not in _JS
    app = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    assert '"/api/transactions/<tx_id>/receipt", methods=["POST"]' not in app


def test_the_date_is_one_row_and_editing_shows_only_the_date():
    row = _FORM[_FORM.index('class="date-row"'):]
    assert row.index('class="date-quick-chips"') < row.index('class="date-pick"') < row.index('id="txDate"')
    assert ".modal-sheet.is-edit .date-quick-chips { display: none; }" in _CSS
    assert _JS.count("setEditLook(true)") == 1 and _JS.count("setEditLook(false)") == 2   # הוספה, שכפול
    assert "weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'" in _JS


def test_who_entered_it_sits_by_the_title():
    header = _TPL[_TPL.index('<div class="modal-title-row">'):]
    assert header.index('id="modalTitle"') < header.index('id="txEnteredMeta"') < header.index("</div>")


def test_frequency_and_end_date_stack_at_16px():
    """עד 7.10 הם ישבו באותה שורה ב-0.88rem — והאייפון עשה זום בנגיעה בהם.
    מתן (7.10) בחר: 16px, אחד מתחת לשני."""
    assert "#modalOverlay .recurring-fields.visible { display: grid; grid-template-columns: 1fr;" in _CSS
    assert "#modalOverlay .recurring-fields .form-input { font-size: max(1rem, 16px);" in _CSS


def test_the_categories_are_taller_and_show_the_whole_name():
    """מתן (11.10): "תגדיל טיפה את הקטגוריות, אני רוצה שיראו את כל השמות" —
    במקום "באותו גודל של היום" (5.10)."""
    assert "#modalOverlay .cat-btn { min-height: 76px;" in _CSS
    assert "#modalOverlay .cat-btn > span:last-child { overflow-wrap: anywhere; }" in _CSS
