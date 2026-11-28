"""The edit and delete panels behind a transaction row stay hidden until swiped.

A row under a category is at least 44px tall (the touch-target rule, 22.11),
and its content is 35px. When a finger first touches a row its content is
wrapped in `.swipe-content`, in front of two panels that fill the row. The
wrapper kept the content's own height, so 9px of gold and red showed under
every row that had been touched (Matan, 28.11, on an iPhone).
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CSS = (Path(__file__).resolve().parent.parent / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    return re.search(re.escape(selector) + r"\s*\{(.*?)\}", _CSS, re.S).group(1)


def test_the_row_is_taller_than_its_content_which_is_why_this_matters():
    assert ".cat-tx-row, .week-detail-list li.week-tx, .legend-item { min-height: 44px; }" in _CSS


def test_the_content_is_stretched_over_the_whole_row():
    row = re.sub(r"/\*.*?\*/", "", _rule(".cat-tx-row.swipe-ready"), flags=re.S)
    assert "display: flex" in row and "align-items: stretch" in row
    assert "display: block" not in row, "as a block the content keeps its own height"
    assert "overflow: hidden" in row and "position: relative" in row


def test_the_panels_fill_the_row_and_sit_behind_the_content():
    panel, content = _rule(".swipe-action"), _rule(".swipe-content")
    assert "top: 0" in panel and "bottom: 0" in panel and "z-index: 1" in panel
    assert "z-index: 2" in content
    assert "background: var(--color-input)" in _rule(".cat-tx-row.swipe-ready > .swipe-content"), \
        "an opaque background is what hides them"


def test_nothing_is_rounded_so_no_corner_of_a_panel_shows():
    """Rounded corners over the panels left a thin red and gold arc at each
    corner of a touched row: the blended edge of the clip."""
    row = re.sub(r"/\*.*?\*/", "", _rule(".cat-tx-row.swipe-ready"), flags=re.S)
    content = re.sub(r"/\*.*?\*/", "", _rule(".cat-tx-row.swipe-ready > .swipe-content"), flags=re.S)
    assert "border-radius: 0" in row and "border-radius: 0" in content
