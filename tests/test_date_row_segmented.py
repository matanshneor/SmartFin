"""The date row when adding (Matan, 1.11): "not clear enough"; now one bar like
"של מי ההוצאה": today | yesterday | other, and the date itself under "today"
and "yesterday"."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
BASE = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
JS = (ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def test_each_quick_chip_has_a_place_for_its_date():
    assert BASE.count('<span class="date-sub" aria-hidden="true"></span>') == 2


def test_the_chips_show_their_actual_date():
    assert "chip.querySelector('.date-sub')" in JS


def test_adding_looks_like_the_owner_toggle_and_editing_is_untouched():
    assert "#modalOverlay .modal-sheet:not(.is-edit) .detail-row .date-row {" in CSS
    assert ".date-sub { display: none; }" in CSS   # when editing, only the full date


def test_owner_and_date_boxes_are_the_same_width():
    """Matan, 1.11: the labels at a fixed width, so the boxes after them are the
    same width."""
    assert "#modalOverlay .detail-row > .form-label { flex: 0 0 100px;" in CSS
