"""הגדרות: תיבה אחת פתוחה בכל רגע, והתנתקות מתחת לכל התיבות (מתן, 1.10)."""
import json
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8")
_HTML = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")


def _accordion(clicks):
    """מריץ ב-node את קוד האקורדיון האמיתי על ארבע תיבות מדומות.
    מחזיר, אחרי כל לחיצה, אילו תיבות פתוחות ואם הייתה גלילה."""
    code = _JS[_JS.index("// ── Accordion"):_JS.index("// ── Category type tabs")]
    harness = """
    const scrolled = [];
    function box(i) {
        const classes = new Set();
        const g = {
            i, classList: {
                toggle(c) { classes.has(c) ? classes.delete(c) : classes.add(c); return classes.has(c); },
                remove(c) { classes.delete(c); },
                contains(c) { return classes.has(c); },
            },
            compareDocumentPosition(o) { return o.i > i ? 4 : 2; },
            scrollIntoView() { scrolled.push(i); },
        };
        const h = { listeners: {}, attrs: {},
            addEventListener(t, f) { this.listeners[t] = f; },
            setAttribute(k, v) { this.attrs[k] = String(v); },
            closest() { return g; } };
        return { g, h };
    }
    const boxes = [0, 1, 2, 3].map(box);
    global.Node = { DOCUMENT_POSITION_FOLLOWING: 4 };
    global.document = { querySelectorAll() { return boxes.map(b => b.h); } };
    eval(process.argv[1]);
    const out = [];
    for (const i of JSON.parse(process.argv[2])) {
        scrolled.length = 0;
        boxes[i].h.listeners.click();
        out.push({
            open: boxes.filter(b => b.g.classList.contains('open')).map(b => b.g.i),
            expanded: boxes.filter(b => b.h.attrs['aria-expanded'] === 'true').map(b => b.g.i),
            scrolled: scrolled.slice(),
        });
    }
    console.log(JSON.stringify(out));
    """
    res = subprocess.run(["node", "-e", harness, code, json.dumps(clicks)],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def test_opening_a_box_closes_the_one_that_was_open():
    steps = _accordion([0, 2, 1])
    assert [s["open"] for s in steps] == [[0], [2], [1]]
    assert [s["expanded"] for s in steps] == [[0], [2], [1]]


def test_tapping_the_open_box_closes_it():
    steps = _accordion([1, 1])
    assert steps[1]["open"] == [] and steps[1]["expanded"] == []


def test_scrolls_only_when_an_open_box_above_was_closed():
    """סגירה מעל מקפיצה את העמוד; סגירה מתחת — לא, ואין סיבה לגלול."""
    steps = _accordion([0, 3, 1])
    assert steps[0]["scrolled"] == []          # לא נסגר כלום
    assert steps[1]["scrolled"] == [3]         # 0 נסגרה מעל 3
    assert steps[2]["scrolled"] == []          # 3 נסגרה מתחת ל-1


def test_logout_sits_under_all_the_boxes_not_inside_account():
    form = _HTML.index('class="logout-form settings-logout"')
    last_box_end = _HTML.rindex("</section>", 0, _HTML.index('class="settings-contact"'))
    assert last_box_end < form < _HTML.index('class="settings-contact"')
    assert len(re.findall(r"url_for\('logout'\)", _HTML)) == 1
