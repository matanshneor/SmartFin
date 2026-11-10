"""
The count-up animation always ran from zero.

After adding a ₪100 expense the user saw "left in the account" climb from zero to
the new amount: a whole second of animation that tells them nothing. What is
interesting is **what changed**: that the balance dropped by ₪100. Matan asked for
this after seeing the soft refresh working.

On first load there is no previous value, and there zero is right: it is the
first impression, not an update.
"""
import json
import shutil
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS   = _ROOT / "frontend/static/js"

_HARNESS = r"""
const fs = require('fs'), g = globalThis;
const reduce = process.argv[3] === 'reduce';
let handlers = {};
const frames = [];

function makeEl(cls, value, prefix) {
    return { className: cls, dataset: { countup: String(value),
             ...(prefix ? { prefix } : {}) },
             set textContent(v) { frames.push(v); }, get textContent() { return ''; },
             style: {}, classList: { add() {}, toggle() {} } };
}

let current = [];
g.document = {
    querySelectorAll: (s) => s === '[data-countup]' ? current : [],
    addEventListener: (t, fn) => { handlers[t] = fn; },
};
g.window = g;
// ‎sfMoney‎ מ-core.js עצמו (נחלץ בזמן ריצה), לא עותק
eval(require('fs').readFileSync(process.env.SF_CORE_JS, 'utf8')
     .match(/window\.sfMoney = function[\s\S]*?\n\};/)[0]);
g.addEventListener = (t, fn) => { handlers[t] = fn; };
g.matchMedia = () => ({ matches: reduce });
// שעון אחד לשניהם. ‎performance.now‎ ו-‎requestAnimationFrame‎ חייבים
// לדבר באותה יחידה — אחרת ‎now - start‎ שלילי, ההתקדמות לא מגיעה ל-1,
// והלולאה רצה לנצח.
let clock = 0;
g.performance = { now: () => clock };
// מתקדמים ברבע שנייה כל פריים: מספיק כדי לראות התקדמות אמיתית,
// ומגיע ל-1000ms (סוף האנימציה) אחרי ארבעה.
// ‎pending‎ סופר פריימים שטרם רצו, כדי שאפשר יהיה לחכות **לסיום**
// האנימציה במקום לנחש כמה זמן היא לוקחת.
let pending = 0;
g.requestAnimationFrame = (fn) => {
    pending++;
    clock += 250;
    setTimeout(() => { pending--; fn(clock); }, 0);
};

(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));

const steps = JSON.parse(process.argv[4]);   // [[value, prefix], ...]
(function next(i) {
    if (i >= steps.length) {
        console.log(JSON.stringify({ frames }));
        return;
    }
    current = [makeEl('hero-amount', steps[i][0], steps[i][1] || '')];
    frames.push('--');
    if (i === 0) handlers.DOMContentLoaded();
    else handlers['sf:refreshed']();

    // המתנה ל**ניקוז** ולא 60ms קבועים. הגרסה הקודמת הניחה שהאנימציה
    // מספיקה להסתיים בזמן הזה; תחת עומס (הסוויטה המלאה, או CI) היא לא,
    // והשלב הבא התחיל כשהקודם עדיין רץ — כלומר בדיקה שנופלת אחת לכמה
    // הרצות על תזמון ולא על התנהגות. עם CI כשער פריסה זה חוסם פריסות
    // אקראית.
    (function drain() {
        if (pending > 0) { setTimeout(drain, 0); return; }
        next(i + 1);
    })();
})(0);
"""


def _run(steps, reduce=False):
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    h = _ROOT / "tests" / "_countup_harness.js"
    h.write_text(_HARNESS, encoding="utf-8")
    try:
        out = subprocess.run([node, str(h), str(_JS / "motion.js"),
                              "reduce" if reduce else "normal", json.dumps(steps)],
                             capture_output=True, text=True, timeout=30,
            env={**os.environ, "SF_CORE_JS": str(_JS / "core.js")},)
    finally:
        h.unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr[:800]
    frames = json.loads(out.stdout.strip().splitlines()[-1])["frames"]
    # Split into groups by the '--' marker
    groups, cur = [], None
    for f in frames:
        if f == "--":
            if cur is not None:
                groups.append(cur)
            cur = []
        else:
            cur.append(f)
    if cur is not None:
        groups.append(cur)
    return groups


def _num(text):
    # `float` and not `int`: an amount with agorot is shown with agorot even
    # mid-animation
    return float(text.replace("₪", "").replace(",", "").replace("-", "") or 0)


# ─── The behaviour Matan asked for ──────────────────────────────────────────

def test_the_second_render_never_goes_near_zero():
    """The heart. ₪5,000 then ₪4,900: the whole animation moves between the two
    values, and doesn't plunge to zero and climb back.

    Checked on every frame and not only the first: the first sampled one is
    already a quarter of a second into the acceleration, not the starting point
    itself."""
    first, second = _run([[5000, ""], [4900, ""]])

    values = [_num(f) for f in second]
    assert min(values) >= 4900, f"went down to {min(values)}, i.e. it started from zero"
    assert max(values) <= 5000
    assert values[-1] == 4900


def test_the_first_render_still_climbs_from_zero():
    """Control: this is the first impression, not an update, and there zero is right."""
    first, = _run([[5000, ""]])

    values = [_num(f) for f in first]
    assert values[0] < 5000 * 0.8, f"started from {values[0]}: doesn't look like a climb from zero"
    assert values[-1] == 5000


def test_a_number_that_did_not_change_does_not_animate():
    """An animation from a value to itself is a second of nothing. Happens on every
    refresh that didn't touch this number, e.g. adding a project transaction,
    which doesn't go into the balance."""
    first, second = _run([[5000, ""], [5000, ""]])

    assert second == ["₪5,000"], f"{len(second)} frames ran instead of one"


def test_flipping_between_surplus_and_deficit_starts_from_zero():
    """The number is shown as an absolute value with a prefix. A direct transition
    between two absolute values would show intermediate amounts with the opposite
    sign: "-₪250" on the way from a ₪300 surplus to a ₪200 deficit."""
    first, second = _run([[300, ""], [200, "-"]])

    values = [_num(f) for f in second]
    assert max(values) <= 200, f"reached {max(values)}: it continued from the previous value"
    assert second[-1] == "-₪200"
    assert all(f.startswith("-") for f in second), "the sign isn't consistent throughout the animation"


def test_reduced_motion_still_skips_everything():
    """Control: this preference beats everything, including the new behaviour."""
    first, second = _run([[5000, ""], [4900, ""]], reduce=True)

    assert first == ["₪5,000"]
    assert second == ["₪4,900"]


# ─── The count doesn't change form midway (Matan, 30.9) ─────────────────────
#
# "The money is counted in agorot and then shown rounded": a target in whole
# shekels, but the animation's intermediate values are fractional (1,203.57), and
# `sfMoney` shows agorot when present, so they appeared throughout the count and
# vanished at the end.

def test_a_whole_shekel_target_counts_in_whole_shekels():
    frames = [f for g in _run([[1481, ""]]) for f in g]

    assert len(frames) > 3, "no animation to check"
    assert not [f for f in frames if "." in f], [f for f in frames if "." in f][:5]
    assert frames[-1] == "₪1,481"


def test_a_target_with_agorot_counts_in_whole_shekels_and_lands_exactly():
    """"Make the count-up animation without agorot too": even when the final amount
    includes agorot, the count is in whole shekels, and the agorot appear only in
    the final value."""
    frames = [f for g in _run([[1481.37, ""]]) for f in g]

    assert len(frames) > 3, "no animation to check"
    assert frames[-1] == "₪1,481.37"
    assert not [f for f in frames[:-1] if "." in f], [f for f in frames[:-1] if "." in f][:5]
