"""
The app didn't know there was no network.

Someone in an underground car park typed a transaction, tapped save, and got
"שגיאת רשת — נסה שוב": exactly the same message shown when the server is down and
when there is a bug. They tried again, got the same thing, and there was no hint
that the problem was in their surroundings and not on our side.

And on the home page it was worse: there the window closes optimistically the
moment of the tap, so the failure left them with no window and none of what they
typed. "Try again" meant typing everything again, with no network, exactly when
they least want to.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS   = _ROOT / "frontend/static/js"
_TPL  = _ROOT / "frontend/templates"
_CSS  = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _read(p):
    return p.read_text(encoding="utf-8")


def _strip_comments(js):
    """Without this, a comment explaining why something isn't done is counted as
    doing it."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


# ─── One message, in one place ──────────────────────────────────────────────

def test_no_file_still_hard_codes_the_network_error():
    """37 places showed the same string. A rule written 37 times won't change in 37
    of them."""
    offenders = []
    for f in _JS.glob("*.js"):
        if f.name == "net.js":
            continue          # the single source, and that is the whole point
        for line in _read(f).splitlines():
            if "'שגיאת רשת" in line:
                offenders.append(f"{f.name}: {line.strip()[:60]}")
    assert not offenders, f"a hard-coded string remained in: {offenders}"


def test_the_message_is_used_everywhere_it_used_to_be():
    uses = sum(_read(f).count("window.sfNetError()") for f in _JS.glob("*.js"))
    assert uses >= 35, f"only {uses} places moved to the shared message"


# ─── Every page really loads the file ───────────────────────────────────────

@pytest.mark.parametrize("page,why", [
    ("base.html",           "כל עמודי האפליקציה"),
    ("onboarding.html",     "עמוד עצמאי — לא טוען את core.js"),
    ("login.html",          "עמוד עצמאי, ובלי חיבור אי אפשר להתחבר"),
    ("reset_password.html", "עמוד עצמאי"),
])
def test_every_entry_point_loads_it(page, why):
    """sfNetError is called in each of their files. A page that doesn't load net.js
    will crash on undefined exactly at the moment of failure."""
    assert "js/net.js" in _read(_TPL / page), f"{page} — {why}"


def test_it_loads_before_anything_that_calls_it():
    base = _read(_TPL / "base.html")
    assert base.index("js/net.js") < base.index("js/transactions.js")


# ─── What actually happens ──────────────────────────────────────────────────

_HARNESS = r"""
const fs = require('fs');
const g = globalThis;

const body = [];
const handlers = {};
let online = process.argv[2] === 'online';

// ב-node ‎navigator‎ הוא מאפיין לקריאה בלבד: השמה רגילה נבלעת בשקט,
// והבדיקה הייתה רצה כל הזמן מול ‎undefined‎ ונראית ירוקה בטעות
Object.defineProperty(g, 'navigator', {
    configurable: true,
    get: () => ({ get onLine() { return online; } }),
});
g.document = {
    body: { appendChild: (el) => body.push(el) },
    createElement: () => ({
        className: '', textContent: '', hidden: false,
        attrs: {}, setAttribute(k, v) { this.attrs[k] = v; },
    }),
};
g.window = g;
g.addEventListener = (type, fn) => { handlers[type] = fn; };
const toasts = [];
g.showToast = (msg) => toasts.push(msg);

(0, eval)(fs.readFileSync(process.argv[3], 'utf8'));

const out = () => ({
    banners: body.length,
    visible: body.length ? !body[0].hidden : false,
    text:    body.length ? body[0].textContent : null,
    role:    body.length ? body[0].attrs.role : null,
    message: g.sfNetError(),
    toasts:  toasts.slice(),
});

const steps = { start: out() };
online = false; if (handlers.offline) handlers.offline();
steps.wentOffline = out();
online = true;  if (handlers.online) handlers.online();
steps.cameBack = out();

console.log(JSON.stringify(steps));
"""


def _run(start_state):
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    harness = _ROOT / "tests" / "_net_harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    try:
        out = subprocess.run([node, str(harness), start_state, str(_JS / "net.js")],
                             capture_output=True, text=True, timeout=30)
    finally:
        harness.unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr[:800]
    return json.loads(out.stdout)


def test_nothing_is_shown_while_the_connection_is_fine():
    """Control: a bar that shows up for no reason trains people to ignore it."""
    steps = _run("online")

    assert steps["start"]["banners"] == 0
    assert steps["start"]["message"] == "שגיאת רשת — נסו שוב"


def test_the_banner_appears_when_the_connection_drops():
    steps = _run("online")

    assert steps["wentOffline"]["visible"] is True
    assert "אין חיבור לאינטרנט" in steps["wentOffline"]["text"]
    assert steps["wentOffline"]["role"] == "status"


def test_the_banner_goes_away_and_says_so_when_it_comes_back():
    """A bar that stays on screen after the connection returned is worse than a bar
    that never appeared."""
    steps = _run("online")

    assert steps["cameBack"]["visible"] is False
    assert steps["cameBack"]["toasts"] == ["החיבור חזר"]


def test_someone_who_opens_the_app_already_offline_is_told_immediately():
    """There is no offline event for whoever was already disconnected when they
    opened: only a state."""
    steps = _run("offline")

    assert steps["start"]["visible"] is True


def test_the_error_message_changes_with_the_connection():
    steps = _run("online")

    assert steps["wentOffline"]["message"] == "אין חיבור לאינטרנט — נסו שוב כשהחיבור יחזור"
    assert steps["cameBack"]["message"]    == "שגיאת רשת — נסו שוב"


def test_only_a_definite_offline_counts():
    """navigator.onLine === true only says there is an active network interface: it
    returns true even facing a router with no internet. Inferring "all is well"
    from it means showing a false bar to half the users."""
    code = _strip_comments(_read(_JS / "net.js"))

    assert "navigator.onLine === false" in code
    assert "navigator.onLine === true" not in code


# ─── What was typed doesn't vanish ──────────────────────────────────────────

def test_a_failed_save_on_the_dashboard_brings_the_form_back():
    """The heart of the second part. On the home page the window closed before the
    server answered, so a network failure left a message and an empty form."""
    js = _read(_JS / "transactions.js")
    catch = js[js.rindex("const url    = editId"):]
    catch = catch[catch.index(".catch(function () {"):][:900]

    assert "placeholderRow.remove();" in catch
    # reopenAfterFailure and not openModal directly: it also restores the receipt
    # that the optimistic close cleared. And that it does open the window:
    assert "reopenAfterFailure();" in catch, "the window isn't restored: what was typed is lost"
    helper = js[js.index("function reopenAfterFailure()"):][:200]
    assert "openModal();" in helper
    assert "formError.textContent = window.sfNetError();" in catch


def test_closing_the_modal_does_not_clear_the_fields():
    """This is what makes it possible to restore the window with its content. If
    closeModal starts resetting, the fix above will silently break."""
    js = _read(_JS / "transactions.js")
    body = js[js.index("function closeModal()"):js.index("function resetForm()")]

    assert "txForm.reset()" not in body
    assert "resetForm()" not in body


# ─── The bar itself ─────────────────────────────────────────────────────────

def test_the_banner_is_readable():
    m = re.search(r"\.net-banner\s*\{[^}]*background:\s*(#[0-9A-Fa-f]{6})[^}]*"
                  r"color:\s*(#[0-9A-Fa-f]{6})", _CSS)
    assert m, "the bar's colours weren't found"

    def lum(h):
        def f(c):
            c /= 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (f(int(h[i:i + 2], 16)) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    a, b = lum(m.group(1)), lum(m.group(2))
    ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
    assert ratio >= 4.5, f"contrast {ratio:.2f}:1, below the standard"


def test_the_banner_clears_the_notch():
    block = _CSS[_CSS.index(".net-banner"):][:600]
    assert "env(safe-area-inset-top" in block, "the bar will be cut off under the iPhone notch"


def test_a_hidden_banner_is_actually_hidden():
    assert ".net-banner[hidden] { display: none; }" in _CSS
