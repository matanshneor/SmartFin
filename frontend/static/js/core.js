// Core helpers shared by every page: HTML escaping, toast and confirm dialog.
// Moved out of base.html so the browser can cache it between pages, and to
// allow a strict CSP that blocks inline code.
// Reading a "data island": a <script type="application/json"> block the server
// renders. Read on demand and not once upfront, because each page adds its own
// island after core.js and an early read wouldn't see it.

window.sfData = function (id) {
    const el = document.getElementById(id);
    if (!el) return {};
    try {
        return JSON.parse(el.textContent) || {};
    } catch (e) {
        console.error('sfData: JSON פגום ב-' + id, e);
        return {};
    }
};

// The global data from base.html is available right away: its island appears
// before this file.

window.SF_PAGE_DATA = window.sfData('sf-page-data');

/* A money amount for display: the same rule as `format_money` on the server
 * (backend/money.py): agorot only when present. `12.5 → "12.50"`, `180 → "180"`.
 * No ₪ and no sign. `withCents` forces two digits, for an animation that ends
 * on an amount with agorot, so the digits don't jump from "12" to "12.50" on
 * the last frame. */
window.sfMoney = function (value, withCents) {
    const amount = Math.round((Number(value) || 0) * 100) / 100;
    const cents = withCents || amount !== Math.round(amount);
    return amount.toLocaleString('en-US', {
        minimumFractionDigits: cents ? 2 : 0,
        maximumFractionDigits: cents ? 2 : 0,
    });
};

/* A message to show after a full page load ("העסקה נוספה").
 *
 * `sessionStorage` throws in a browser that blocks storage (strict private
 * mode, Lockdown). This call sat unguarded inside the `.then` of a save that
 * had already succeeded, and the exception fell into the network `.catch`: the
 * user saw "network error", the button was released, and they tapped again and
 * created a copy. Without storage we give up the message, not the reload. */
/* "Number + noun": the same rule as `count_of` in backend/wording.py:
 * `sfCount(1, 'עסקה אחת', 'עסקאות')` → "עסקה אחת", `sfCount(3, …)` → "3 עסקאות". */
window.sfCount = function (n, one, many) {
    n = parseInt(n, 10) || 0;
    return n === 1 ? one : n + ' ' + many;
};

/* A save button is enabled only when something changed (Matan, 13.10: "everywhere
 * something can be saved"). The label doesn't change; only disabled or
 * enabled. "Changed" = different from the saved value, each field's
 * `defaultValue` / `defaultChecked` in the form, so "cancel" and going back to
 * the original value disable it too. `commit()` after a successful save: the
 * new values are now the saved ones. */
window.sfSaveWhenChanged = function (scope, button) {
    if (!scope || !button) return null;
    function changed() {
        return Array.prototype.some.call(scope.querySelectorAll('input:not([type="hidden"]), select, textarea'), function (el) {
            if (el.type === 'checkbox' || el.type === 'radio') return el.checked !== el.defaultChecked;
            if (el.tagName === 'SELECT') {
                const def = Array.prototype.find.call(el.options, function (o) { return o.defaultSelected; });
                return el.value !== (def ? def.value : (el.options[0] || {}).value);
            }
            return el.value.trim() !== el.defaultValue.trim();
        });
    }
    function sync() { button.disabled = !changed(); }
    function commit() {
        scope.querySelectorAll('input:not([type="hidden"]), textarea').forEach(function (el) {
            if (el.type === 'checkbox' || el.type === 'radio') el.defaultChecked = el.checked;
            else el.defaultValue = el.value;
        });
        sync();
    }
    scope.addEventListener('input', sync);
    scope.addEventListener('change', sync);
    sync();
    return { sync: sync, commit: commit, changed: changed };
};

window.sfToastAfterReload = function (message) {
    try { sessionStorage.setItem('sf_toast', message); } catch (e) { /* without the message, not without the reload */ }
};

// HTML escaping: every value the user entered (category/project name, icon,
// description) must go through this before being injected into innerHTML;
// otherwise a name with a malicious tag created by one family member runs for
// everyone (XSS). Available globally to every page.

window.escapeHtml = function (s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
};

// ── Sensory feedback for a transaction that went in: a soft sound + a tap ──
// The sound is synthesised in real time (no audio file, no network request):
// two short rising tones. Vibration is tried two ways because no single one
// works everywhere: navigator.vibrate on Android, and a hidden switch toggle
// that triggers iOS haptics. There is no way to know whether the device is
// muted, so we don't try: iOS will just swallow the sound.

/* ── Physical motion, shared by the sheet, the rows and the "this week" card
 * (Matan, 7.10, Apple-style) ── */

// A spring in Apple's terms: `response` is how fast it arrives (seconds),
// `damping` 1 is no bounce, below 1 overshoots a little. Starts from the current
// position and velocity, so there is no seam between the finger and the motion.
// Returns a function that stops it midway.

window.sfSpring = function (from, to, v0, response, damping, paint, done, until) {
    const k = Math.pow(2 * Math.PI / response, 2), c = 4 * Math.PI * damping / response;
    let x = from, v = v0, last = performance.now(), raf;
    raf = requestAnimationFrame(function frame(now) {
        const dt = Math.min((now - last) / 1000, 0.064);
        last = now;
        const n = Math.max(1, Math.ceil(dt / 0.004));
        for (let i = 0; i < n; i++) { v += (-k * (x - to) - c * v) * dt / n; x += v * dt / n; }
        if ((until && until(x)) || (Math.abs(x - to) < 0.5 && Math.abs(v) < 8)) {
            paint(until ? x : to); done(); return;
        }
        paint(x);
        raf = requestAnimationFrame(frame);
    });
    return function () { cancelAnimationFrame(raf); };
};

// Where a motion at this velocity would end up if it kept decaying. `0.998`:
// like scrolling; `0.99`: shorter, for small distances like a row.

window.sfProject = function (v, rate) {
    rate = rate || 0.998;
    return (v / 1000) * rate / (1 - rate);
};

// Past the boundary: every extra pixel moves less (like the edge of a list on iPhone)

window.sfRubber = function (over, dim) { return (over * dim * 0.55) / (dim + 0.55 * Math.abs(over)); };

// Velocity (px/s) from the last `100ms` of the drag, not the whole of it. At
// least one frame: the browser coalesces fast moves, and two samples in the same
// millisecond gave zero velocity, so a strong fling did nothing.

window.sfVelocity = function (samples, now) {
    const end = samples[samples.length - 1];
    let first = end;
    for (let i = samples.length - 1; i >= 0 && now - samples[i].t <= 100; i--) first = samples[i];
    return (end.p - first.p) / Math.max(end.t - first.t, 16) * 1000;
};

/* An area that opens and closes by sliding its height rather than jumping
 * (Matan, 7.10, motion round, item 5). `apply(open)` does the switch itself (a
 * class): on open right away and then grow from 0, on close shrink and only
 * switch at the end. A tap midway continues from the height currently on screen.
 * `animate` and not CSS, so reduced motion is checked here: the general rule
 * doesn't apply to it. */
window.sfReveal = function (body, open, apply) {
    const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (calm || !body || !body.animate) { apply(open); return; }
    const from = body.sfAnim ? body.offsetHeight : null;     // in the middle of a previous motion
    if (body.sfAnim) { body.sfAnim.cancel(); body.sfAnim = null; }
    if (open) apply(true);
    const cs = getComputedStyle(body);
    const full = { height: body.offsetHeight + 'px', opacity: 1,
                   paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom,
                   marginTop: cs.marginTop, marginBottom: cs.marginBottom,
                   borderTopWidth: cs.borderTopWidth, borderBottomWidth: cs.borderBottomWidth };
    const none = { height: '0px', opacity: 0, paddingTop: '0px', paddingBottom: '0px',
                   marginTop: '0px', marginBottom: '0px', borderTopWidth: '0px', borderBottomWidth: '0px' };
    const start = Object.assign({}, open ? none : full);
    if (from !== null) { start.height = from + 'px'; start.opacity = open ? 0.5 : 1; }
    body.style.overflow = 'hidden';
    const anim = body.animate([start, open ? full : none],
        { duration: open ? 220 : 180, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' });
    body.sfAnim = anim;
    anim.onfinish = function () {
        body.sfAnim = null;
        body.style.overflow = '';
        if (!open) apply(false);
    };
};

// Feedback on save: a short vibration only (Matan, 11.10). Until then there was
// also a two-tone sound, with an off switch in settings, and a switch was needed
// only because of the sound: in an app used at the supermarket checkout, a sound
// is what makes people uninstall it. A vibration is silent, so it needs no setting.

window.appFeedback = function () {
    // Vibration: Android only. iPhone has no navigator.vibrate, and toggling a
    // switch from code doesn't trigger the Taptic engine (tested): there the tap
    // comes from the touch itself on the save button, which is the <label> of a
    // hidden switch.

    try {
        if (navigator.vibrate) navigator.vibrate([12, 40, 22]);
    } catch (err) { /* no vibration: carry on */ }
};

// ── Global feedback system: toast + confirm dialog ──

(function () {
    const toast = document.getElementById('appToast');
    let toastTimer = null;
    let toastHide = null;

    // Whether the last action was on the keyboard, to move focus to "undo" only
    // for someone navigating by keyboard, and not jump for someone who touched
    // the screen

    let lastInputWasKeyboard = false;
    document.addEventListener('keydown', function () { lastInputWasKeyboard = true; }, true);
    document.addEventListener('pointerdown', function () { lastInputWasKeyboard = false; }, true);

    // "Undo" doesn't vanish under the finger or the focus: 6 seconds isn't
    // enough for someone who reached it by keyboard or is hesitating with the
    // mouse over it

    function holdToast() { clearTimeout(toastTimer); }
    function releaseToast() {
        clearTimeout(toastTimer);
        if (toastHide) toastTimer = setTimeout(toastHide, 2500);
    }
    toast.addEventListener('mouseenter', holdToast);
    toast.addEventListener('mouseleave', releaseToast);
    toast.addEventListener('focusin', holdToast);
    toast.addEventListener('focusout', releaseToast);

    // action = { label, onClick }: an optional action button (e.g. "undo" for a
    // delete). When a button is shown, the toast stays 6 seconds to give time to
    // react.

    window.showToast = function (msg, type, action) {
        toast.innerHTML = '';
        const span = document.createElement('span');
        span.className = 'toast-msg';
        span.textContent = msg;
        toast.appendChild(span);
        toast.classList.toggle('error', type === 'error');
        clearTimeout(toastTimer);
        // A long message ("העסקה נוספה. עברתם את התקציב של…") needs time to read

        let duration = msg.length > 40 ? 4500 : 2600;
        if (action) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'toast-action';
            btn.textContent = action.label;
            btn.addEventListener('click', function () {
                clearTimeout(toastTimer);
                toast.classList.remove('show');
                action.onClick();
            });
            toast.appendChild(btn);
            duration = 6000;
        }
        toast.classList.add('show');
        toastHide = function () { toast.classList.remove('show'); toastHide = null; };
        toastTimer = setTimeout(toastHide, duration);
        // Someone who deleted by keyboard lost focus along with the row; "undo"
        // is its natural place now, otherwise they would have to Tab through the
        // whole page in 6 seconds

        const actionBtn = toast.querySelector('.toast-action');
        if (actionBtn && lastInputWasKeyboard) actionBtn.focus({ preventScroll: true });
    };

    // A message saved before a page reload is shown now.
    // Access to storage can throw: private browsing, blocked storage, or an
    // environment without it at all. This line runs when the file loads, so an
    // exception here took down **all** of core.js: no toast, no confirm dialog,
    // no soft refresh. A useful side effect: that is how it was found, when the
    // tests ran on node 22.

    let pending = null;
    try {
        pending = sessionStorage.getItem('sf_toast');
    } catch (e) { /* no storage: the deferred message just won't be shown */ }
    // A message from the server (`_notice.html`), e.g. "your family changed"

    const noticeEl = document.getElementById('sfNotice');
    if (!pending && noticeEl) {
        try { pending = JSON.parse(noticeEl.textContent); } catch (e) {}
    }
    if (pending) {
        try { sessionStorage.removeItem('sf_toast'); } catch (e) {}
        setTimeout(function () { window.showToast(pending); }, 350);
    }

    const overlay = document.getElementById('confirmOverlay');
    const titleEl = document.getElementById('confirmTitle');
    const msgEl   = document.getElementById('confirmMessage');
    const yesBtn  = document.getElementById('confirmYes');
    const noBtn   = document.getElementById('confirmNo');
    const choiceEl = document.getElementById('confirmChoice');
    let resolver  = null;
    let withChoice = false;
    let confirmLastFocused = null;

    function closeConfirm(result) {
        // With `choices`, confirming returns what was chosen and not `true`

        if (result === true && withChoice) result = choiceEl.value;
        overlay.classList.remove('open');
        if (resolver) { resolver(result); resolver = null; }
        if (confirmLastFocused) { confirmLastFocused.focus(); confirmLastFocused = null; }
    }

    /* Three outcomes, not two:
     *   true  : the confirm button was chosen
     *   false : the cancel button was chosen
     *   null  : a retreat: Escape, ✕, or a tap outside the dialog
     *
     * The distinction is needed when there are two questions in a row. When
     * deleting a project, the second question is "what to do with the
     * transactions", and escaping from it was read as "keep them", so the
     * project was deleted anyway. Whoever panicked and tapped outside the
     * dialog meant to back out, and a project has no undo-delete.
     *
     * `null` was chosen on purpose because it is falsy: every existing caller
     * checks `if (!ok)` and keeps working unchanged. Whoever needs the
     * distinction checks `=== null`.
     *
     * `opts.choices` ([{value, label}]) adds a list to choose from under the
     * message, and then confirming returns the chosen `value` instead of `true`. */
    window.appConfirm = function (opts) {
        withChoice = !!(opts.choices && opts.choices.length);
        choiceEl.hidden = !withChoice;
        choiceEl.innerHTML = '';
        (opts.choices || []).forEach(function (c) {
            const o = document.createElement('option');
            o.value = c.value;
            o.textContent = c.label;
            choiceEl.appendChild(o);
        });
        titleEl.textContent  = opts.title || 'לאשר את הפעולה?';
        msgEl.textContent    = opts.message || '';
        yesBtn.textContent   = opts.confirmText || 'מחיקה';
        noBtn.textContent    = opts.cancelText || 'ביטול';
        // Default: a destructive action (red), right for 99% of existing uses
        // (deletion). opts.danger === false shows a neutral button for ordinary
        // actions.

        yesBtn.classList.toggle('confirm-yes-neutral', opts.danger === false);
        confirmLastFocused = document.activeElement;
        overlay.classList.add('open');
        // On the next frame: the dialog is hidden with visibility when closed,
        // and an element inside a hidden parent can't be focused

        requestAnimationFrame(function () {
            noBtn.focus(); // a safe default: not the destructive button
        });
        return new Promise(function (resolve) { resolver = resolve; });
    };

    /* Deleting a category: shared by settings and the project page.
     *
     * No transaction without a category: first ask the server how many
     * transactions it has and where they can be moved, then ask the user in the
     * same dialog. The server enforces the same rule itself (409 without a
     * target), so the question here is a convenience, not the protection.
     *
     * `sfAskCategoryDeletion` only asks: a `Promise` of `{target, count}`
     * (`target` is `null` when there are no transactions), or `null` when
     * cancelled/blocked. The project edit page deletes only on save (Matan,
     * 9.10), so it asks now and deletes later. `adjust` (optional) fixes the
     * target list according to changes not yet saved. */
    let deletingCategory = false;
    function catJson(r) {
        return r.json().catch(function () { return {}; })
            .then(function (d) { return { ok: r.ok, d: d }; });
    }
    window.sfAskCategoryDeletion = function (baseUrl, name, adjust) {
        // A double tap on ✕ opened two questions in a row, and the answer to
        // the first was swallowed by the second

        if (deletingCategory) return Promise.resolve(null);
        deletingCategory = true;
        let usage = null;
        return fetch(baseUrl + '/usage').then(catJson).then(function (res) {
            if (!res.ok) {
                window.showToast(res.d.error || 'המחיקה נכשלה', 'error');
                return false;
            }
            usage = adjust ? adjust(res.d) : res.d;
            if (usage.blocked) {
                window.showToast(usage.blocked, 'error');
                return false;
            }
            const count = usage.count || 0;
            const opts = { title: 'למחוק את "' + name + '"?', confirmText: 'מחיקת הקטגוריה' };
            if (count) {
                opts.message = (count === 1 ? 'יש בקטגוריה הזו עסקה אחת' : 'יש בקטגוריה הזו ' + count + ' עסקאות')
                    + '. לאיזו קטגוריה להעביר ' + (count === 1 ? 'אותה' : 'אותן') + '?';
                opts.choices = usage.alternatives.map(function (c) {
                    return { value: c.id, label: c.icon + ' ' + c.name };
                });
                opts.confirmText = 'העברה ומחיקה';
            } else {
                opts.message = 'אין בה עסקאות.';
            }
            return window.appConfirm(opts);
        }).then(function (answer) {
            if (!answer) return null;
            const target = usage.count ? answer : null;
            return {
                target: target, count: usage.count || 0,
                dest: target && usage.alternatives.find(function (c) { return c.id === target; }),
            };
        }).catch(function () {
            window.showToast(window.sfNetError(), 'error');
            return null;
        }).then(function (choice) {
            deletingCategory = false;
            return choice;
        });
    };

    /* Asks and deletes right away (settings). A `Promise` of `true` when the
 * category was deleted. */
    window.sfDeleteCategory = function (baseUrl, name) {
        return window.sfAskCategoryDeletion(baseUrl, name).then(function (choice) {
            if (!choice) return false;
            const url = baseUrl + (choice.target ? '?move_to=' + encodeURIComponent(choice.target) : '');
            return fetch(url, { method: 'DELETE' }).then(catJson).then(function (res) {
                if (!res.ok) {
                    window.showToast(res.d.error || 'המחיקה נכשלה', 'error');
                    return false;
                }
                window.showToast(choice.dest
                    ? 'הקטגוריה נמחקה — ' + (choice.count === 1 ? 'העסקה הועברה' : choice.count + ' עסקאות הועברו')
                      + ' ל"' + choice.dest.name + '"'
                    : 'הקטגוריה נמחקה');
                return true;
            });
        }).catch(function () {
            window.showToast(window.sfNetError(), 'error');
            return false;
        });
    };

    yesBtn.addEventListener('click', function () { closeConfirm(true); });
    noBtn.addEventListener('click', function () { closeConfirm(false); });
    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closeConfirm(null);   // a retreat, not a "no"
    });

    // Escape closes, Tab/Shift+Tab stay inside the dialog

    overlay.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { closeConfirm(null); return; }   // retreat
        if (e.key !== 'Tab') return;
        const focusables = withChoice ? [choiceEl, yesBtn, noBtn] : [yesBtn, noBtn];
        const idx = focusables.indexOf(document.activeElement);
        e.preventDefault();
        const next = e.shiftKey
            ? focusables[(idx <= 0 ? focusables.length : idx) - 1]
            : focusables[(idx + 1) % focusables.length];
        next.focus();
    });
})();



/* ─── Soft refresh ─────────────────────────────────────────────────────────
 *
 * Every action in the app used to end with location.reload(). Adding a
 * transaction took about two seconds from tap to a stable screen: a deliberate
 * 380ms pause so the sound could finish, a round trip to the server, re-parsing
 * 99KB of CSS and 77KB of JS, and then a one-second count-up animation on the
 * very number you wanted to see. Along the way the temporary row the code had
 * managed to show was wiped, and so was the scroll position.
 *
 * Fetch the same URL and replace only what changed. Jinja stays the single
 * source of truth: no display logic is duplicated here, and that is a source of
 * bugs avoided. Falling back to a full reload means that in the worst case the
 * behaviour is identical to today.
 *
 * The animations don't run again, on purpose: counting up from 0 after an
 * update makes the number "jump backwards" before your eyes, and the fresh HTML
 * already contains the final value anyway.
 */
/* The page areas the soft refresh replaces.
 *
 * The hero is a **sibling** of `main` in base.html, not a child, so a refresh
 * that replaced only `main` left "left in the account this month" stuck after
 * every add and edit. That is the number the whole app exists for.
 *
 * And for a new family it was especially bad: the hero shows "tap + to add your
 * first transaction and see your balance here", and that sentence stayed on
 * screen above the transaction just added. */
const SF_RELOAD_REGIONS = ['header.page-hero', 'main.main-content'];

/* `pendingToast`: a message the caller intends to show after the refresh. If
 * the soft refresh isn't possible and we fall back to a full reload, it is
 * saved so it is shown after the load and not lost, and then the promise
 * returns `'reloaded'`, so the caller doesn't show it twice. */
/* Replaces the page areas with content from HTML already fetched. Returns
 * `'full'` when it can't be replaced safely (a missing area, or charts without
 * the library), and the caller falls back to a full load. */
function sfApplyHtml(html, regions) {
    const doc = new DOMParser().parseFromString(html, 'text/html');

    // A month that was empty doesn't load the chart library (70KB with nothing
    // to draw). After the first transaction the new HTML already brings charts,
    // and without the library they stayed empty squares. Once a month: a full
    // reload.

    if (typeof window.Chart === 'undefined' && doc.querySelector('main canvas')) return 'full';

    // A deploy since this page loaded: the new HTML may use classes the old CSS
    // and scripts don't know. Each asset URL carries its content hash (`?v=`),
    // so another stylesheet or core script means another version: reload fully
    // instead of pouring new markup into old styles (design pass, 11.11). Only
    // the assets every page shares: switching tabs brings other page scripts.

    const assets = function (d) {
        return Array.prototype.map.call(
            d.querySelectorAll('link[rel="stylesheet"][href*="/css/"], script[src*="/js/core.js"]'),
            function (el) { return el.getAttribute('href') || el.getAttribute('src'); }).join('|');
    };
    if (assets(doc) !== assets(document)) return 'full';

    // All areas are collected before touching any of them: a partial swap (a
    // new hero over an old body) is worse than a full reload.

    const pairs = regions.map(function (sel) {
        return [document.querySelector(sel), doc.querySelector(sel)];
    });
    if (pairs.some(p => !p[0] || !p[1])) return 'full';

    // Elements that hold their own state (`data-sf-keep`, the install banner)
    // move as they are into the new content, instead of being swapped for an
    // empty copy from the server

    const keep = [];
    pairs.forEach(function (pair) {
        if (pair[0].querySelectorAll) {
            pair[0].querySelectorAll('[data-sf-keep][id]').forEach(function (el) { keep.push(el); });
        }
    });

    pairs.forEach(function (pair) { pair[0].replaceWith(pair[1]); });

    keep.forEach(function (el) {
        const fresh = document.getElementById(el.id);
        if (fresh && fresh !== el) fresh.replaceWith(el);
    });

    // The page's data blocks (`sf-view-data` etc.) sit in `{% block scripts %}`,
    // outside the swapped areas. Without this the month page's charts and legend
    // were redrawn from the old numbers, under cards that had already updated.

    doc.querySelectorAll('script[type="application/json"][id]').forEach(function (fresh) {
        const current = document.getElementById(fresh.id);
        if (current && current.tagName === 'SCRIPT') current.textContent = fresh.textContent;
    });
    if (doc.title) document.title = doc.title;
    // Notify whoever needs to reconnect themselves (animations, charts)

    window.dispatchEvent(new CustomEvent('sf:refreshed'));
    return 'ok';
}

function sfFetchPage(url) {
    return fetch(url, {
        headers: { 'X-Requested-With': 'sf-soft-reload' },
        credentials: 'same-origin',
    }).then(function (r) {
        // 401 is already handled in auth-guard; anything else falls back to a
        // full load

        if (!r.ok) throw new Error('soft load got ' + r.status);
        // Redirected (e.g. to the lock screen, Matan 13.10): don't paste it
        // inside the page

        if (r.redirected) throw new Error('soft load redirected');
        return r.text();
    });
}

window.softReload = function (selector, pendingToast) {
    const regions = selector ? [selector] : SF_RELOAD_REGIONS;
    function fullReload() {
        if (pendingToast) window.sfToastAfterReload(pendingToast);
        window.location.reload();
        return 'reloaded';
    }
    return sfFetchPage(window.location.href)
        .then(function (html) {
            if (sfApplyHtml(html, regions) === 'full') return fullReload();
        })
        .catch(fullReload);
};

/* Moving to another URL without a full load: moving between months (Matan,
 * 11.10). The same mechanism as the soft refresh, with a different URL. `html`:
 * content already fetched ahead of time (during the swipe), so we don't wait
 * for the server a second time. The URL is replaced (`replaceState`) and not
 * added to history: paging between months isn't a "new page", and "back"
 * leaves the month rather than stepping one by one through every month paged. */
window.softNavigate = function (url, html) {
    function fullLoad() {
        window.location.href = url;
        return 'navigated';
    }
    return Promise.resolve(html || sfFetchPage(url))
        .then(function (text) {
            if (sfApplyHtml(text, SF_RELOAD_REGIONS) === 'full') return fullLoad();
            try { history.replaceState(null, '', url); } catch (e) { /* not critical */ }
            return 'swapped';
        })
        .catch(fullLoad);
};
window.sfFetchPage = sfFetchPage;


// ── "Project finished" / "reopen" (Matan, 30.9, round 6, item 3) ──
// The button at the bottom of the project page and in the bar of a finished
// project. Finishing asks first; reopening doesn't, since it hides nothing.

document.addEventListener('click', function (e) {
    const btn = e.target.closest && e.target.closest('[data-archive-project]');
    if (!btn || btn.disabled) return;
    const archived = btn.dataset.archived === 'true';
    const ask = archived ? window.appConfirm({
        title: 'לסמן את "' + btn.dataset.name + '" כפרויקט שהסתיים?',
        message: 'הוא לא יופיע יותר בטופס ההוספה וברשימת הפרויקטים, אבל כל העסקאות '
               + 'והסכומים נשמרים, ואפשר להחזיר אותו בכל רגע.',
        confirmText: 'סיום הפרויקט',
        danger: false,
    }) : Promise.resolve(true);
    ask.then(function (ok) {
        if (!ok) return;
        btn.disabled = true;
        fetch('/api/projects/' + btn.dataset.archiveProject + '/archive', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ archived: archived }),
        })
            .then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (d) {
                    if (!r.ok) throw new Error(d.error || 'HTTP ' + r.status);
                });
            })
            .then(function () {
                try {
                    window.sfToastAfterReload(archived ? 'הפרויקט סומן כפרויקט שהסתיים'
                                                       : 'הפרויקט נפתח מחדש');
                } catch (err) { /* without the toast, not without the refresh */ }
                window.location.reload();
            })
            .catch(function (err) {
                btn.disabled = false;
                window.showToast(err && err.message && !/^HTTP/.test(err.message)
                                 ? err.message : window.sfNetError(), 'error');
            });
    });
});


// ── Switching tabs doesn't go into history (Matan, 11.10) ──
// "When I scroll the whole page, it moves me between pages". On iPhone a swipe
// from the edge is the system's "back", and a website can't turn it off; every
// tap on the tab bar added a page to history, so a scroll that started near the
// edge went back to the previous page.
// The tabs replace each other (`location.replace`) like in an app: there is
// nowhere to go back to. Opening a project from the list does go into history:
// "back" from it returns to the list.
// On `window` and not `document`: runs after anyone who already handled the
// tap (smooth month switching in `month.js` stops it itself).

(function () {
    const TABS = ['/', '/month', '/months', '/projects', '/settings'];
    window.addEventListener('click', function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const a = e.target.closest && e.target.closest('a[href]');
        if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
        let url;
        try { url = new URL(a.href, location.href); } catch (err) { return; }
        if (url.origin !== location.origin || TABS.indexOf(url.pathname) === -1) return;
        // A link to an anchor on the same page: the browser scrolls; no reload

        if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
        e.preventDefault();
        location.replace(url.href);
    });
})();


// ── "This week" on the home page: tapping a day shows its transactions
// (Matan, 30.9) ──
// Delegated, since the card is replaced on a soft refresh

document.addEventListener('click', function (e) {
    const btn = e.target.closest && e.target.closest('.week-day');
    if (!btn || btn.disabled) return;
    const card = btn.closest('.week-card');
    card.querySelectorAll('.week-day').forEach(function (b) {
        const on = b === btn;
        b.classList.toggle('is-selected', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    card.querySelectorAll('.week-detail').forEach(function (d) {
        d.hidden = d.dataset.day !== btn.dataset.day;
    });
});


// ── "This week": paging to earlier weeks, arrows and swipe (Matan, 2.10) ──
// The server returns the whole card from the same template the home page
// renders; it is swapped in place.
// Delegated: a soft refresh returns the card to the current week, and that's fine.

(function () {
    let loading = false;

    const calmMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // The next week is fetched as soon as the swipe starts (Matan, 11.10: "in
    // real time"), so on release it is usually already here. One request per week.

    let pending = null;
    function fetchWeek(offset) {
        if (!pending || pending.offset !== offset) {
            const req = fetch('/api/week?offset=' + offset, { credentials: 'same-origin' })
                .then(function (r) {
                    if (!r.ok) throw new Error('week ' + r.status);
                    return r.text();
                });
            req.catch(function () { if (pending && pending.req === req) pending = null; });
            pending = { offset: offset, req: req };
        }
        return pending.req;
    }

    // `dir`: which side the card exits to on a swipe (`-1` left, `1` right);
    // none for the arrows.
    // `fromX`/`v`: where the card stands and how fast the finger left: the exit
    // continues the motion seamlessly.

    function goToWeek(card, offset, focusSel, dir, fromX, v) {
        if (loading || offset < 0) return;
        loading = true;
        card.classList.add('is-loading');
        if (dir && calmMotion) dir = 0;
        let out = Promise.resolve();
        if (dir && card.animate) {
            const w = card.offsetWidth;
            const speed = Math.max(Math.abs(v || 0), 1400);                 // px/s
            const ms = Math.max(110, Math.min(220, (w * 0.7 - Math.abs(fromX || 0)) / speed * 1000));
            card.style.transform = '';
            out = card.animate([{ transform: 'translateX(' + (fromX || 0) + 'px)', opacity: 1 },
                                { transform: 'translateX(' + (dir * w * 0.7) + 'px)', opacity: 0 }],
                               { duration: ms, easing: 'cubic-bezier(0.3, 0, 0.8, 0.6)', fill: 'forwards' })
                .finished.catch(function () {});
        }
        Promise.all([fetchWeek(offset), out])
            .then(function (res) { return res[0]; })
            .then(function (html) {
                const tpl = document.createElement('template');
                tpl.innerHTML = html.trim();
                const fresh = tpl.content.querySelector('.week-card');
                if (!fresh) throw new Error('no card');
                card.replaceWith(fresh);
                pending = null;
                if (dir && fresh.animate) {
                    // The new card enters from the other side and stops on a soft spring

                    const w = fresh.offsetWidth;
                    fresh.animate([{ transform: 'translateX(' + (-dir * w * 0.35) + 'px)', opacity: 0 },
                                   { transform: 'translateX(0)', opacity: 1 }],
                                  { duration: 280, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' });
                }
                // Focus stays on the arrow that was pressed: keyboard and screen
                // reader don't "fall" to the top of the page

                const again = focusSel && fresh.querySelector(focusSel);
                if (again && !again.disabled) again.focus({ preventScroll: true });
            })
            .catch(function () {
                card.classList.remove('is-loading');
                if (card.getAnimations) card.getAnimations().forEach(function (a) { a.cancel(); });
                settle(card, 0);
                window.showToast(window.sfNetError(), 'error');
            })
            .then(function () { loading = false; });
    }

    document.addEventListener('click', function (e) {
        const btn = e.target.closest && e.target.closest('.week-nav-btn');
        if (!btn || btn.disabled) return;
        const card = btn.closest('.week-card');
        const older = Number(btn.dataset.weekGo) > Number(card.dataset.offset);
        goToWeek(card, Number(btn.dataset.weekGo),
                 older ? '.week-nav-btn[aria-label="שבוע קודם"]' : '.week-nav-btn[aria-label="שבוע הבא"]');
    });

    // "Add an expense" on an empty week: the same sheet as the + button (Matan, 18.10)

    document.addEventListener('click', function (e) {
        if (!(e.target.closest && e.target.closest('.week-empty-add'))) return;
        const fab = document.getElementById('fabBtn');
        if (fab) fab.click();
    });

    // Swipe: in Hebrew time flows right to left (Sunday on the right), so the
    // previous week "is" to the right: dragging left brings it, and dragging
    // right goes forward again.
    //
    // Direction lock (Matan, 2.10: "so that if I scroll there, it doesn't scroll
    // the screen"): after 8px decide once whether this is a sideways move or a
    // scroll. Sideways: the screen is locked until the finger lifts, and the card
    // moves with it. Scroll: touch nothing.

    let sx = null, sy = 0, axis = null, drag = null, x = 0, stopSpring = null;
    let samples = [];                  // position and time, for the release velocity

    function canGo(card, older) {
        const b = card.querySelector('.week-nav-btn[aria-label="' + (older ? 'שבוע קודם' : 'שבוע הבא') + '"]');
        return !!(b && !b.disabled);
    }
    function placeCard(card, v) { x = v; card.style.transform = v ? 'translateX(' + v + 'px)' : ''; }
    // Back into place on a spring that continues the finger's velocity, not a
    // fixed 0.2-second transition

    function settle(card, v) {
        if (stopSpring) { stopSpring(); stopSpring = null; }
        card.style.transition = '';
        if (calmMotion || !x) { placeCard(card, 0); return; }
        stopSpring = window.sfSpring(x, 0, v || 0, 0.32, 0.86, function (p) { placeCard(card, p); },
                                     function () { stopSpring = null; placeCard(card, 0); });
    }

    document.addEventListener('touchstart', function (e) {
        const card = e.target.closest && e.target.closest('.week-card');
        if (!card || loading || e.touches.length !== 1) { sx = null; return; }
        if (stopSpring) { stopSpring(); stopSpring = null; }   // caught in the middle of returning
        sx = e.touches[0].clientX - x; sy = e.touches[0].clientY;
        axis = null; drag = card;
        samples = [{ t: e.timeStamp, p: e.touches[0].clientX }];
    }, { passive: true });

    document.addEventListener('touchmove', function (e) {
        if (sx === null || !drag) return;
        const dx = e.touches[0].clientX - sx, dy = e.touches[0].clientY - sy;
        if (!axis) {
            if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
            axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
            if (axis === 'x') {
                sx += dx > 0 ? 8 : -8;            // without the 8-pixel jump of the decision
                // The week before last is already being fetched

                const older = dx < 0;
                if (canGo(drag, older)) fetchWeek(Number(drag.dataset.offset) + (older ? 1 : -1));
            }
        }
        if (axis !== 'x') return;
        e.preventDefault();                       // the screen doesn't move while swiping sideways
        // The card moves with the finger one to one (Matan, 11.10; until then at
        // half speed). Towards a direction it can't go (no next week): resistance
        // that grows gradually.

        const raw = e.touches[0].clientX - sx;
        drag.style.transition = '';
        placeCard(drag, canGo(drag, raw < 0) ? raw : window.sfRubber(raw, drag.offsetWidth));
        samples.push({ t: e.timeStamp, p: e.touches[0].clientX });
        if (samples.length > 6) samples.shift();
    }, { passive: false });

    document.addEventListener('touchend', function (e) {
        if (sx === null || !drag) return;
        const card = drag;
        const dx = x;
        const wasX = axis === 'x';
        sx = null; drag = null; axis = null;
        if (!wasX) return;
        // By **where the motion is heading** and not just how far it moved
        // (Matan, 7.10, Apple-style): a short fast flick moves a week, and a long
        // drag that comes back doesn't

        const v = window.sfVelocity(samples, e.timeStamp);
        const aim = dx + window.sfProject(v, 0.99);
        const older = aim < 0;
        // A drag to one side and a flick back to the other: changing your mind,
        // not a request for the opposite week

        if (Math.abs(aim) < 50 || (aim < 0) !== (dx < 0) || !canGo(card, older)) { settle(card, v); return; }
        const offset = Number(card.dataset.offset);
        x = 0;
        goToWeek(card, older ? offset + 1 : offset - 1, null, older ? -1 : 1, dx, v);
    }, { passive: true });

    document.addEventListener('touchcancel', function () {
        if (drag) settle(drag);
        sx = null; drag = null; axis = null;
    }, { passive: true });
})();


// ── "Got it" on a home-page alert (Matan, 12.10) ──
// ✕ hides it until the end of the month, on this phone. Stored by a key that
// includes the month and the text: a new month brings everything back; an
// overage that grew has different text, so it comes back. Delegated, since the
// alerts are swapped after every soft refresh.

(function () {
    const KEY = 'sf_dismissed_alerts';
    function loadDismissed() { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { return []; } }
    function saveDismissed(list) { try { localStorage.setItem(KEY, JSON.stringify(list.slice(-40))); } catch (e) { /* no storage */ } }
    function applyDismissed() {
        const gone = loadDismissed();
        document.querySelectorAll('.alert-wrap[data-alert-key]').forEach(function (w) {
            w.hidden = gone.indexOf(w.dataset.alertKey) !== -1;
        });
        document.querySelectorAll('.alerts-section').forEach(function (sec) {
            sec.hidden = !sec.querySelector('.alert-wrap:not([hidden])');
        });
    }
    document.addEventListener('click', function (e) {
        const btn = e.target.closest && e.target.closest('.alert-dismiss');
        if (!btn) return;
        const w = btn.closest('.alert-wrap');
        const list = loadDismissed();
        if (list.indexOf(w.dataset.alertKey) === -1) list.push(w.dataset.alertKey);
        saveDismissed(list);
        applyDismissed();
    });
    applyDismissed();
    window.addEventListener('sf:refreshed', applyDismissed);
})();


// ── One announcement per visit on home (design pass, 11.11) ──
// "A family member joined", "<month> closed" and "your <year>" could all sit
// on home at once (on 1 January, all three, over the alerts). Only the first
// still showing appears; the others wait for a later visit, without being
// marked as seen. Order: "joined" first, since it is shown only once; then the
// month recap (one day); then the year (fifteen days). Budget alerts aren't
// announcements: they always show.

(function () {
    const ORDER = ['#memberJoined', '.recap-wrap', '.year-card-wrap'];
    function oneAnnouncement() {
        let shown = false;
        ORDER.forEach(function (sel) {
            document.querySelectorAll(sel).forEach(function (el) {
                if (el.hidden && !el.dataset.sfQueued) return;     // dismissed, or never shown
                if (!shown) { shown = true; if (el.dataset.sfQueued) { el.hidden = false; delete el.dataset.sfQueued; } return; }
                el.hidden = true;
                el.dataset.sfQueued = '1';
            });
        });
    }
    document.addEventListener('DOMContentLoaded', oneAnnouncement);
    window.addEventListener('sf:refreshed', oneAnnouncement);
    // Closing the one showing lets the next one in, on this visit
    document.addEventListener('click', function (e) {
        if (e.target.closest && e.target.closest('.recap-close, .member-joined-close, .year-card-wrap .alert-dismiss')) {
            setTimeout(oneAnnouncement, 0);
        }
    });
})();


// ── "Project finished?": "not now" hides it for a month (Matan, 12.10) ──

(function () {
    const KEY = 'sf_quiet_later';
    const MONTH = 30 * 24 * 3600 * 1000;
    function loadQuiet() { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { return {}; } }
    function applyQuiet() {
        const later = loadQuiet();
        document.querySelectorAll('[data-project-quiet]').forEach(function (b) {
            const at = later[b.dataset.projectQuiet];
            b.hidden = !!(at && Date.now() - at < MONTH);
        });
    }
    document.addEventListener('click', function (e) {
        const btn = e.target.closest && e.target.closest('[data-quiet-later]');
        if (!btn) return;
        const banner = btn.closest('[data-project-quiet]');
        const later = loadQuiet();
        later[banner.dataset.projectQuiet] = Date.now();
        try { localStorage.setItem(KEY, JSON.stringify(later)); } catch (err) { /* no storage */ }
        applyQuiet();
    });
    applyQuiet();
    window.addEventListener('sf:refreshed', applyQuiet);
})();


// ── "Pin to home" on the project page (Matan, 12.10) ──

document.addEventListener('click', function (e) {
    const btn = e.target.closest && e.target.closest('[data-pin-project]');
    if (!btn || btn.disabled) return;
    const pin = btn.dataset.pinned !== 'true';
    btn.disabled = true;
    fetch('/api/projects/' + btn.dataset.pinProject + '/pin', {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ pinned: pin }),
    })
        .then(function (r) { if (!r.ok) throw new Error(); })
        .then(function () {
            btn.dataset.pinned = pin ? 'true' : 'false';
            btn.setAttribute('aria-pressed', pin ? 'true' : 'false');   // the icon button in the header
            window.showToast(pin ? 'הפרויקט יופיע בבית, מתחת למאזן' : 'הפרויקט הוסר מהבית');
        })
        .catch(function () { window.showToast(window.sfNetError(), 'error'); })
        .then(function () { btn.disabled = false; });
});


// ── App opens (Matan, 10.11.2026) ──
//
// Records that the app was opened: when a page loads, and when the user comes
// back to it from another app. Every page that loads this file is behind
// sign-in, so there is always a user. Fire and forget: nothing waits for it, and
// a failure only goes to the console; the database itself skips a repeat within
// 5 minutes, so moving between pages doesn't add rows.
(function () {
    function logOpen() {
        fetch('/api/app-open', {
            method: 'POST', credentials: 'same-origin', keepalive: true,
            headers: { 'Accept': 'application/json' },
        })
            .then(function (r) { if (!r.ok) console.warn('app-open: HTTP ' + r.status); })
            .catch(function (e) { console.warn('app-open:', e); });
    }
    logOpen();
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') logOpen();
    });
})();


/* ─── Focus stays inside an open dialog (design pass, 11.11) ──────────────
 *
 * Only the transaction sheet and the confirm dialog kept Tab inside; the
 * search screen, "all transactions", the budgets sheet, the icon and colour
 * pickers, the receipt viewer, the yearly summary, "what's new" and the invite
 * all said `aria-modal` and let Tab walk out to the page behind them. One trap
 * for all of them: the topmost visible `[aria-modal="true"]`. The two that
 * manage their own Tab cancel the event first, so this one stays out of their way.
 */
(function () {
    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), '
                    + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    function shown(el) {
        if (el.hidden || el.closest('[hidden]')) return false;
        const cs = getComputedStyle(el);
        return cs.display !== 'none' && cs.visibility !== 'hidden' && el.getClientRects().length > 0;
    }

    function topDialog() {
        const open = Array.prototype.filter.call(
            document.querySelectorAll('[aria-modal="true"]'), shown);
        if (!open.length) return null;
        // The one holding the focus, else the last one opened (later in the page)
        return open.find(function (d) { return d.contains(document.activeElement); })
            || open[open.length - 1];
    }

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Tab' || e.defaultPrevented) return;
        const dialog = topDialog();
        if (!dialog) return;
        const items = Array.prototype.filter.call(dialog.querySelectorAll(FOCUSABLE), shown);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        const inside = dialog.contains(document.activeElement);
        if (!inside || (e.shiftKey && document.activeElement === first)) {
            e.preventDefault();
            (e.shiftKey ? last : first).focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    });
})();
