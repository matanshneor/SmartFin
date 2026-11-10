/* Phone notifications: on by default (Matan, 1.11).
 *
 * "Notifications should be on for everyone and possible to turn off". The
 * browser doesn't allow subscribing to notifications without the user tapping
 * "allow" in its own prompt; that isn't our decision. So the closest possible:
 *   · Whoever already allowed in the past: subscribed automatically, silently,
 *     on every device they come in from.
 *   · Whoever hasn't been asked yet: a small bar above the bottom menu, once:
 *     "turn on". One tap, and the phone's prompt. ✕: not asked again on this
 *     device ("so it doesn't nag them every week", Matan 1.11). It can always
 *     be turned on in settings → notifications.
 *   · Whoever turned it off in settings: not asked and not subscribed again on
 *     this device.
 * On iPhone this works only from the installed app: outside it there are no
 * notifications at all.
 *
 * `window.sfPush`: the switch in settings works through here too. */
(function () {
    'use strict';
    const key = (window.SF_PAGE_DATA || {}).pushKey;
    const OFF = 'sf_push_off';
    const ASKED = 'sf_push_asked';

    const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
    const iOS = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
                (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
    const supported = !!key && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

    function store(name, value) {
        try { if (value === null) localStorage.removeItem(name); else localStorage.setItem(name, value); }
        catch (e) { /* without memory: at worst we ask again */ }
    }
    function read(name) {
        try { return localStorage.getItem(name); } catch (e) { return null; }
    }
    function keyBytes(b64) {
        const s = (b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
        return Uint8Array.from(atob(s), function (c) { return c.charCodeAt(0); });
    }
    function post(url, body) {
        return fetch(url, { method: 'POST', credentials: 'same-origin',
                            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
            .then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (d) {
                    if (!r.ok) throw new Error(d.error || '');
                    return d;
                });
            });
    }
    function registration() { return navigator.serviceWorker.ready; }

    /* `'on'`: subscribed; `'off'`: not; `'blocked'`: blocked in the phone's
     * settings; `'unsupported'`: no notifications here (on iPhone: not from the
     * installed app). */
    function state() {
        if (!supported) return Promise.resolve('unsupported');
        if (Notification.permission === 'denied') return Promise.resolve('blocked');
        return registration().then(function (reg) { return reg.pushManager.getSubscription(); })
            .then(function (sub) { return sub && read(OFF) !== '1' ? 'on' : 'off'; });
    }

    function subscribe(reg) {
        return reg.pushManager.getSubscription().then(function (sub) {
            return sub || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
        }).then(function (sub) { return post('/api/push/subscribe', sub.toJSON()); });
    }

    /* must run from a tap; otherwise Safari doesn't show the permission prompt */
    function enable() {
        store(OFF, null);
        return Notification.requestPermission().then(function (perm) {
            if (perm !== 'granted') throw new Error('כדי לקבל התראות צריך לאשר אותן בחלון של הטלפון');
            return registration().then(subscribe);
        });
    }

    function disable() {
        store(OFF, '1');
        return registration().then(function (reg) { return reg.pushManager.getSubscription(); })
            .then(function (sub) {
                if (!sub) return post('/api/push/unsubscribe', {});
                const endpoint = sub.endpoint;
                return sub.unsubscribe().then(function () { return post('/api/push/unsubscribe', { endpoint: endpoint }); });
            });
    }

    window.sfPush = { supported: supported, iOS: iOS, standalone: standalone,
                      state: state, enable: enable, disable: disable };

    if (!supported || read(OFF) === '1') return;

    // Already allowed in the past: subscribe silently (and make sure the server
    // knows, once per visit)

    if (Notification.permission === 'granted') {
        let told = false;
        try { told = sessionStorage.getItem('sf_push_told') === '1'; } catch (e) { /* */ }
        if (!told) {
            registration().then(subscribe).then(function () {
                try { sessionStorage.setItem('sf_push_told', '1'); } catch (e) { /* */ }
            }).catch(function () { /* on the next visit */ });
        }
        return;
    }
    if (Notification.permission !== 'default') return;

    // Not asked yet: the bar, unless the "add to home screen" bar is showing now

    const bar = document.getElementById('pushBar');
    if (!bar || document.body.classList.contains('has-install-bar')) return;
    if (read(ASKED) === '1') return;
    function hide() { bar.hidden = true; document.body.classList.remove('has-install-bar'); }
    bar.hidden = false;
    document.body.classList.add('has-install-bar');
    document.getElementById('pushBarClose').addEventListener('click', function () {
        store(ASKED, '1');
        hide();
    });
    document.getElementById('pushBarGo').addEventListener('click', function () {
        enable().then(function () {
            hide();
            if (window.showToast) window.showToast('ההתראות הופעלו');
        }).catch(function (err) {
            store(ASKED, '1');
            hide();
            if (window.showToast && err && err.message) window.showToast(err.message, 'error');
        });
    });
})();
