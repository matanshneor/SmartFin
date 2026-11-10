"""
Joining a family that hadn't finished setup split it in two.

The wizard showed the invite code in step 2, before the categories were created,
and whoever got it and joined right away landed on the wizard themselves, because
"needs setup" was measured by the absence of categories. With no message at all,
and on the opening screen "משפחה חדשה או הצטרפות?".

That looks exactly like a failure, and the obvious way out is the button offered:
"פותחים משפחה חדשה". Two people in separate families, each with their own code,
and neither understands why the other doesn't see the transactions.

Two fixes: the code isn't shown before there is something to invite to, and
whoever already landed there gets an explanation instead of an opening screen.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_HTML = (_ROOT / "frontend/templates/onboarding.html").read_text(encoding="utf-8")
_JS   = (_ROOT / "frontend/static/js/onboarding.js").read_text(encoding="utf-8")

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "family_needs_onboarding", lambda fid: True)
    monkeypatch.setattr(app_module.db, "get_family",
                        lambda fid: {"id": _FAM, "name": "שניאור", "invite_code": "K4F2QX",
                                     "manager_id": "someone-else"})
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME; sess["family_id"] = _FAM; sess["user_name"] = "מתן"
        yield c


# ─── Prevention: no invite before there is something to invite to ──────────

def test_the_invite_code_is_not_a_step_in_the_middle_any_more():
    """The code was shown in step 2 of 4: that is, before the family even exists as
    far as the app is concerned."""
    assert 'id="stepInvite"' in _HTML
    assert 'id="step2"' in _HTML and 'id="step4"' not in _HTML


def _success_handler(marker):
    """The body of the .then(function (…) { … }) after marker, by brace balancing.

    A textual sign for the end of the block ("}).catch") breaks on every
    formatting change, and the test then swallows the rest of the file and reports
    green on nothing. Counting doesn't break."""
    body = _JS[_JS.index(marker):]
    head = re.search(r"\.then\(function \([A-Za-z_$][\w$]*\) \{", body)
    assert head, f"no success handler found after {marker}"

    start = head.end()
    depth = 1
    for i in range(start, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                return body[start:i]
    raise AssertionError("הסוגריים לא נסגרו")


def test_the_invite_screen_comes_after_the_wizard_finishes():
    """It is shown only on a successful response of /api/onboarding/complete."""
    block = _success_handler("onboarding/complete")

    assert "stepInvite.style.display = 'block'" in block
    assert "window.location.href = '/'" not in block, "still skips straight to the app"


def test_a_joiner_goes_straight_into_the_app():
    """Whoever joins a set-up family doesn't need an invite screen: they invited
    no one, and the family already exists. They go in."""
    block = _success_handler("family/join")

    assert "window.location.href = '/'" in block
    assert "stepInvite" not in block, "the joiner gets the invite screen instead of going in"


def test_the_wizard_is_three_steps_now():
    """The invite left the sequence, so the dots did too."""
    assert _HTML.count('class="step-dot') == 3
    assert "dotStep4" not in _HTML and "dotStep4" not in _JS


# ─── The rescue: whoever already landed there ───────────────────────────────

def test_a_joiner_waiting_on_an_unfinished_family_gets_an_explanation(client, monkeypatch):
    """The heart. Until now they saw the opening screen, and tapped "משפחה חדשה"."""
    monkeypatch.setattr(app_module.db, "get_family_members",
                        lambda fid: [{"id": _ME, "name": "מתן"},
                                     {"id": "other", "name": "אור"}])

    html = client.get("/onboarding").get_data(as_text=True)

    assert "הצטרפתם בהצלחה" in html
    assert "אור" in html
    assert 'id="step0"' not in html, "the opening screen is still shown"


def test_the_manager_keeps_the_wizard_even_after_someone_joins(client, monkeypatch):
    """The danger in the fix itself: whoever opened the family is still in the middle
    of the wizard when a member joins. If they also get the waiting screen, both
    wait for each other forever, and nobody can finish the setup."""
    monkeypatch.setattr(app_module.db, "get_family",
                        lambda fid: {"id": _FAM, "name": "שניאור",
                                     "invite_code": "K4F2QX", "manager_id": _ME})
    monkeypatch.setattr(app_module.db, "get_family_members",
                        lambda fid: [{"id": _ME, "name": "מתן"},
                                     {"id": "other", "name": "אור"}])

    html = client.get("/onboarding").get_data(as_text=True)

    assert 'id="step0"' in html, "whoever opens the family is locked out of their own wizard"
    assert "הצטרפתם בהצלחה" not in html


def test_the_first_member_still_gets_the_wizard(client, monkeypatch):
    """Control, and the important one here: whoever opened the family must see the
    wizard."""
    monkeypatch.setattr(app_module.db, "get_family_members",
                        lambda fid: [{"id": _ME, "name": "מתן"}])

    html = client.get("/onboarding").get_data(as_text=True)

    assert 'id="step0"' in html
    assert "הצטרפתם בהצלחה" not in html


def test_an_unreadable_member_list_falls_back_to_the_wizard(client, monkeypatch):
    """If it's impossible to know who else is in the family, the wizard is the safe
    default: blocking whoever really needs to set up is worse than showing a
    needless screen."""
    def _boom(fid):
        raise app_module.db.DataUnavailable("members")
    monkeypatch.setattr(app_module.db, "get_family_members", _boom)

    assert 'id="step0"' in client.get("/onboarding").get_data(as_text=True)


# ─── The file doesn't crash on the new screen ───────────────────────────────

_HARNESS = r"""
const fs = require('fs');
const present = new Set(JSON.parse(process.argv[3]));
const bound = [];

function el(id) {
    return {
        id,
        style: {},
        classList: { toggle() {}, add() {}, remove() {} },
        addEventListener: (type) => bound.push(id + ':' + type),
        appendChild() {}, querySelectorAll: () => [], focus() {},
        value: '', textContent: '', checked: false, innerHTML: '',
    };
}

const g = globalThis;
g.document = {
    getElementById:   (id) => (present.has(id) ? el(id) : null),
    querySelectorAll: () => [],
    querySelector:    () => null,
    addEventListener() {},
    createElement:    () => el('new'),
};
g.window = g;
g.navigator = { clipboard: { writeText: () => Promise.resolve() } };
g.location = { href: '/onboarding' };
g.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
g.sfNetError = () => '';

(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify({ bound }));
"""


def _run(ids):
    """Runs the real onboarding.js against a DOM containing only the given ids."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    harness = _ROOT / "tests" / "_onboarding_harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    try:
        out = subprocess.run(
            [node, str(harness),
             str(_ROOT / "frontend/static/js/onboarding.js"), json.dumps(ids)],
            capture_output=True, text=True, timeout=30,
        )
    finally:
        harness.unlink(missing_ok=True)
    return out


_WIZARD_IDS = re.findall(r'id="([A-Za-z][\w-]*)"', _HTML)


def test_the_script_survives_a_page_that_has_no_wizard_on_it():
    """The heart of this item from the JS side. The waiting screen is served instead
    of the whole wizard, and has none of the elements. Without the early exit the
    file crashes on the first one missing, exactly like core.js crashed on
    sessionStorage."""
    out = _run(["stepWaiting"])

    assert out.returncode == 0, f"the file crashed on the waiting screen:\n{out.stderr[:800]}"
    assert json.loads(out.stdout)["bound"] == [], "listeners were bound to elements that don't exist"


def test_the_wizard_itself_still_wires_everything_up():
    """Control: the early exit doesn't switch off the real wizard."""
    out = _run(_WIZARD_IDS)

    assert out.returncode == 0, f"the file crashed on the wizard:\n{out.stderr[:800]}"
    bound = json.loads(out.stdout)["bound"]
    for expected in ("chooseCreateBtn:click", "toStep2Btn:click",
                     "finishOnboardingBtn:click", "finishBtn:click"):
        assert expected in bound, f"{expected} wasn't bound (bound: {bound})"
