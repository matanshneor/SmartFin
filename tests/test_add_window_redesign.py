"""The add sheet and the summary card in the new design (Matan, 11.10).

From a complete design mockup Matan chose only two things: one summary card on
home, and the add sheet: a large amount in the centre in the department's
colour, tall categories with the full name, the details in one block and a
dark, wide button. Receipt scanning moved to the header row.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TPL = (_ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
_JS = (_ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
_FORM = _TPL[_TPL.index('<form class="tx-form" id="txForm"'):_TPL.index("</form>")]


def _rule(selector):
    css = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
    # The last one that starts a line is the one that counts (and not
    # `:root[…] #modalOverlay …`, which contains it)
    start = [m.start() for m in re.finditer(r"(?m)^" + re.escape(selector) + r" \{", css)][-1]
    body = css[start:]
    return body[:body.index("}")]


def test_the_receipt_scan_sits_in_the_title_row():
    row = _TPL[_TPL.index('<div class="modal-title-row">'):]
    row = row[:row.index('<div class="type-toggle')]
    assert 'id="modalTitle"' in row and 'id="scanBtn"' in row
    assert _TPL.count('id="scanBtn"') == 1
    assert "min-height: 44px" in _rule("#modalOverlay .scan-btn")


def test_the_amount_is_big_centered_and_takes_the_type_color():
    assert 'class="form-group amount-group"' in _FORM
    assert "font-size: 2.25rem" in _rule("#modalOverlay .amount-input")
    assert "text-align: left" in _rule("#modalOverlay .amount-input")      # next to the ₪, the field centred
    for mode, color in (("expense", "--color-expense"), ("income", "--color-income"), ("savings", "--color-savings")):
        assert f"var({color})" in _rule(f"#modalOverlay .modal-sheet.{mode}-mode .amount-group")


def test_the_amount_field_is_as_wide_as_what_is_in_it():
    """After every change of the value or of the placeholder text; otherwise the ₪
    drifts away from the number."""
    body = _JS[_JS.index("function fitAmount()"):]
    assert "txAmount.style.width = (text.length + 0.3) + 'ch';" in body[:500]
    for line in ("txAmount.value       = plainAmount(tx.amount);", "txAmount.value = data.amount;",
                 "txAmount.placeholder = 'מפענח נתונים…';", "txForm.reset();"):
        assert _JS[_JS.index(line):][:len(line) + 40].count("fitAmount();") == 1, line
    listener = _JS[_JS.index("txAmount.addEventListener('input'"):][:200]
    assert "fitAmount();" in listener


def test_the_details_are_one_block():
    block = _FORM[_FORM.index('<div class="tx-details">'):_FORM.index("</div>{# ‎.tx-details‎ #}")]
    for field in ('id="txDescription"', 'id="ownerGroup"', 'id="txDate"', 'id="txRecurring"', 'id="recurringFields"'):
        assert field in block, field
    assert 'id="projectGroup"' not in block and 'id="categoryGrid"' not in block
    # Description, whose, date, recurring transaction, and installments (Matan, 12.10)
    assert block.count('class="form-group detail-row') == 5
    assert 'id="txInstallments"' in block
    assert "border-top" in _rule("#modalOverlay .tx-details .detail-row + .detail-row")


def test_recurring_is_a_switch():
    assert "transform: translateX(-20px)" in _rule(
        "#modalOverlay .recurring-group .checkbox-input:checked + .checkbox-custom::after")
    assert "var(--toggle-off)" in _rule("#modalOverlay .recurring-group .checkbox-custom")


def test_the_save_button_is_dark_for_every_type():
    """No red/green by department. `#modalOverlay` beats `.modal-sheet.expense-mode`."""
    assert "var(--hero-bg)" in _rule("#modalOverlay .submit-btn")
    assert "var(--gradient-gold)" in _rule(':root[data-theme="dark"] #modalOverlay .submit-btn')


def test_the_summary_is_one_card_with_dividers():
    cards = _rule(".summary-cards")
    assert "gap" not in cards and "box-shadow: var(--shadow-card)" in cards and "overflow: hidden" in cards
    cell = _rule(".summary-card")
    assert "box-shadow" not in cell and "border-radius" not in cell
    assert "border-inline-start" in _rule(".summary-card + .summary-card")
    motion = (_ROOT / "frontend/static/js/motion.js").read_text(encoding="utf-8")
    assert "'.summary-cards, .chart-card," in motion      # the card rises as one unit, not three cells
