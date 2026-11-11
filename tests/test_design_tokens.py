"""The scales added in stage 2 of the design pass (11.11.2026).

Layers, corners, type and motion became tokens on the light `:root`, replacing
only values that were already exactly equal, so nothing on screen moved (proved
by resolving every token back to its value, and by a pixel-identical run of
`tests/visual/`). What is still written by hand is the list for consolidation.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CSS = (Path(__file__).resolve().parent.parent / "frontend/static/css/style.css").read_text(encoding="utf-8")

_SCALES = {
    "z-dock": "50", "z-sheet": "100", "z-dialog": "150", "z-toast": "200", "z-install": "950",
    "radius-xs": "8px", "radius-lg": "20px", "radius-sheet": "26px", "radius-pill": "999px",
    "text-xs": "0.75rem", "text-sm": "0.8125rem", "text-md": "0.875rem", "text-base": "1rem",
    "text-lg": "1.25rem", "text-xl": "1.5rem",
    "dur-fast": "120ms", "dur-quick": "0.15s", "dur-base": "0.2s", "dur-medium": "0.25s", "dur-slow": "0.3s",
}


@pytest.mark.parametrize("name,value", _SCALES.items())
def test_each_scale_step_is_defined_once(name, value):
    defs = re.findall(rf"--{name}:\s*([^;]+);", _CSS)
    assert defs == [value], f"--{name}: {defs}"


def test_the_scales_are_used():
    # Easings sit mostly on lines other tests quote, so fewer of them moved
    for prefix, least in (("--z-", 5), ("--radius-", 5), ("--text-", 5), ("--dur-", 5), ("--ease-", 3)):
        assert _CSS.count("var(" + prefix) >= least, prefix


def test_type_steps_stay_in_rem():
    """The text-size setting scales rem; a px step would stay small"""
    for m in re.finditer(r"--text-[\w-]+:\s*([^;]+);", _CSS):
        assert m.group(1).endswith("rem"), m.group(0)
