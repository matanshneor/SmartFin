"""No two categories in the same colour in the expense donut (Matan, 12.10)."""
import re
from pathlib import Path

import pytest

from backend.app import CHART_COLORS, _distinct_colors

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent


def test_the_palette_has_as_many_colors_as_the_server_counts():
    js = (_ROOT / "frontend/static/js/chart-setup.js").read_text(encoding="utf-8")
    palette = js[js.index("var COLORS = ["):js.index("];")]
    colors = re.findall(r"'#[0-9A-Fa-f]{6}'", palette)
    assert len(colors) == CHART_COLORS and len(set(c.lower() for c in colors)) == CHART_COLORS


def test_the_ninth_category_does_not_take_the_first_ones_color():
    items = [{"total": 900 - i, "color_idx": i} for i in range(9)] + [{"total": 50, "color_idx": 0}]
    _distinct_colors(items)
    assert len({c["color_idx"] for c in items}) == len(items)


def test_the_bigger_category_keeps_its_own_color():
    big, small = {"total": 2000, "color_idx": 3}, {"total": 100, "color_idx": 3 + CHART_COLORS}
    _distinct_colors([small, big])
    assert big["color_idx"] == 3 and small["color_idx"] != 3


def test_a_category_alone_keeps_its_color_every_month():
    item = {"total": 10, "color_idx": 5}
    _distinct_colors([item])
    assert item["color_idx"] == 5


def test_more_categories_than_colors_still_get_a_color():
    items = [{"total": 100 - i, "color_idx": 0} for i in range(CHART_COLORS + 2)]
    _distinct_colors(items)
    assert all(0 <= c["color_idx"] < CHART_COLORS for c in items)
    assert len({c["color_idx"] for c in items}) == CHART_COLORS


def test_legends_wrap_at_the_full_palette():
    for tpl in ("month.html", "project_detail.html"):
        text = (_ROOT / "frontend/templates" / tpl).read_text(encoding="utf-8")
        assert "% 8 }}" not in text and "% 12 }}" in text, tpl
