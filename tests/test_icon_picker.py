"""Picking an icon from a grid and a suggestion by name (Matan, 30.9, round 6, item 8)."""
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "frontend/static/js/icon-picker.js").read_text(encoding="utf-8")


def _suggest(names):
    """Runs the real `suggest` in node, with a minimal document."""
    harness = """
    const listeners = {};
    global.window = {};
    global.document = { addEventListener(t, f) { listeners[t] = f; }, createElement() { return {}; } };
    eval(require('fs').readFileSync(process.argv[1], 'utf8'));
    console.log(JSON.stringify(JSON.parse(process.argv[2]).map(n => window.sfIconSuggest(n))));
    """
    out = subprocess.run(["node", "-e", harness, str(_ROOT / "frontend/static/js/icon-picker.js"),
                          json.dumps(names)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_name_suggests_an_icon():
    assert _suggest(["דלק", "שכר דירה", "חשבון חשמל", "טיול ליפן", "מתנות לחגים", "קופת חולים"]) == \
        ["⛽", "🏠", "💡", "✈️", "🎁", "🏥"]


def test_an_unknown_name_suggests_nothing():
    assert _suggest(["שונות", "", "  "]) == [None, None, None]


def test_every_icon_fits_the_two_character_fields():
    """`maxlength="2"` in UTF-16: `🛡️` (three) was silently cut to `🛡` + half a character."""
    import re
    icons = re.findall(r"'([^'a-zA-Z\\\\ ]{1,4})'", _JS)
    icons = [i for i in icons if any(ord(c) > 0x2000 for c in i)]
    assert len(icons) > 100
    too_long = [i for i in icons if len(i.encode("utf-16-le")) // 2 > 2]
    assert not too_long, too_long


@pytest.mark.parametrize("page", ["base", "onboarding"])
def test_loaded_wherever_there_is_an_icon_field(page):
    html = (_ROOT / f"frontend/templates/{page}.html").read_text(encoding="utf-8")
    assert "js/icon-picker.js" in html


def test_a_hand_pick_is_never_replaced_by_a_suggestion():
    assert "target.dataset.autoIcon = '0'" in _JS
    assert "if (!icon || icon.dataset.autoIcon === '0') return;" in _JS
