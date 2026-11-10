"""Prefetch on touch (Matan, 3.10: idea 29).

Touching a link sends the service worker a message; it starts fetching the page,
and the navigation arriving when the finger lifts gets that response. In memory
only, 5 seconds, once. In the browser: requests counted on the server
(touch+tap = one request).
"""
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent

_HARNESS = r"""
const listeners = {};
let fetches = [], now = 1000;
global.Date = class extends Date { static now() { return now; } };
global.self = { location: { origin: 'https://app' }, addEventListener: (t, f) => { listeners[t] = f; },
                skipWaiting() {}, clients: { claim() {} } };
global.caches = { open: () => Promise.resolve({ addAll: () => Promise.resolve() }), keys: () => Promise.resolve([]) };
const scenario = JSON.parse(process.argv[2]);
global.fetch = (url, opts) => {
    const u = typeof url === 'string' ? url : url.url;
    fetches.push(u);
    const r = scenario.response;
    return Promise.resolve({ ok: r.ok, redirected: r.redirected, url: u, from: 'net',
                             headers: { get: () => r.type } });
};
global.Response = class { constructor(b, o) { this.status = o.status; } };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
(async () => {
    for (const step of scenario.steps) {
        if (step.message) listeners.message({ data: { type: 'prefetch', url: step.message } });
        if (step.wait) now += step.wait;
        if (step.write) listeners.fetch({ request: { method: step.write, mode: 'cors', url: 'https://app/api/x' },
                                          respondWith: () => {} });
        if (step.navigate) {
            let served;
            listeners.fetch({ request: { method: 'GET', mode: 'navigate', url: 'https://app' + step.navigate },
                              respondWith: p => { served = p; } });
            await served;
        }
    }
    console.log(JSON.stringify(fetches));
})();
"""


def _run(steps, response=None):
    response = response or {"ok": True, "redirected": False, "type": "text/html; charset=utf-8"}
    out = subprocess.run(["node", "-e", _HARNESS, str(_ROOT / "frontend/static/sw.js"),
                          json.dumps({"steps": steps, "response": response})],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_touch_then_navigate_is_one_request():
    assert _run([{"message": "/month"}, {"wait": 300}, {"navigate": "/month"}]) == ["https://app/month"]


def test_without_a_touch_the_navigation_goes_to_the_network():
    assert _run([{"navigate": "/month"}]) == ["https://app/month"]


def test_an_old_prefetch_is_thrown_away():
    """More than 5 seconds: don't serve. A page with money isn't served from a while
    ago."""
    assert _run([{"message": "/month"}, {"wait": 6000}, {"navigate": "/month"}]) == \
        ["https://app/month", "https://app/month"]


def test_a_prefetch_serves_one_navigation_only():
    assert _run([{"message": "/month"}, {"navigate": "/month"}, {"navigate": "/month"}]) == \
        ["https://app/month", "https://app/month"]


def test_a_redirect_is_not_reused():
    """An expired session redirects to sign-in. Safari refuses a redirected response
    in a navigation: go to the network."""
    got = _run([{"message": "/month"}, {"navigate": "/month"}],
               {"ok": True, "redirected": True, "type": "text/html"})
    assert got == ["https://app/month", "https://app/month"]


def test_api_and_other_sites_are_never_prefetched():
    assert _run([{"message": "/api/week?offset=1"}, {"message": "https://evil.example/x"}]) == []


def test_the_page_side_sends_the_message_on_touch():
    js = (_ROOT / "frontend/static/js/pwa.js").read_text(encoding="utf-8")
    part = js[js.index("document.addEventListener('pointerdown'"):]
    assert "sw.postMessage({ type: 'prefetch', url: url.pathname + url.search });" in part
    assert "url.pathname.startsWith('/api/')" in part


def test_a_save_throws_away_pages_loaded_before_it():
    """Touching "ביטול" (the page loaded), "המשך עריכה", "שמירה", and the navigation
    after the save got the page from before it, with the old categories (Matan,
    9.10)."""
    for method in ("POST", "PUT", "DELETE"):
        assert _run([{"message": "/projects/p1"}, {"write": method}, {"navigate": "/projects/p1"}]) == \
            ["https://app/projects/p1", "https://app/projects/p1"], method
