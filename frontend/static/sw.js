const CACHE = 'smartfin-v25';

// No precaching.
//
// Asset URLs now carry a content fingerprint (`?v=…`) set on the server, and a
// fixed list here can't know it: it would download unfingerprinted URLs no page
// requests, i.e. a double download of every file and a cache that is never
// touched.
//
// And it isn't needed anymore anyway: a fingerprinted URL is served with a
// one-year lifetime, so the browser keeps it by itself from the first visit.
// Whatever arrives here is cached in real time below.

self.addEventListener('install', function () {
    self.skipWaiting();
});

self.addEventListener('activate', function (e) {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
        )
    );
    self.clients.claim();
});


/* The "no connection" page. Loads the stylesheet from the cache (it is already
 * there) so it looks like the app and not a browser error. The retry button
 * exists because without it the only way forward is a manual refresh, and an app
 * installed on a phone has no refresh button on screen. */
function offlinePage() {
    return new Response(
        '<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="utf-8">' +
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' +
        '<title>אין חיבור</title>' +
        // Dark mode here too (review of 1.10). The response built here has no
        // CSP, so an inline line is allowed: js/theme.js may not be cached when
        // there is no network

        '<script>try{if(localStorage.getItem("sf_theme")==="dark")document.documentElement.setAttribute("data-theme","dark")}catch(e){}</script>' +
        '<link rel="stylesheet" href="/static/css/style.css"></head>' +
        '<body class="auth-body"><div class="auth-container">' +
        '<div class="empty-state">' +
        '<div class="empty-illus" aria-hidden="true"><svg class="ui-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M12 20h.01"/><path d="M8.5 16.43a5 5 0 0 1 7 0"/><path d="M2 8.82a15 15 0 0 1 4.18-2.65"/>' +
        '<path d="M19 12.86a10 10 0 0 0-1.86-1.42"/><path d="M22 8.82a15 15 0 0 0-11.29-3.76"/>' +
        '<path d="M5 12.86a10 10 0 0 1 5.17-2.69"/><path d="m2 2 20 20"/></svg></div>' +
        '<p class="empty-text">אין חיבור לאינטרנט</p>' +
        '<p class="empty-sub">המספרים שלכם מחכים ברגע שהחיבור יחזור. ' +
        'לא מוצגים כאן נתונים ישנים, כדי שלא תסתמכו על מספר שכבר לא נכון.</p>' +
        '<button class="submit-btn" onclick="location.reload()">נסו שוב</button>' +
        '</div></div></body></html>',
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
}

/* ── Preloading on touch (Matan, 3.10, idea 29) ──
 *
 * The browser requests a page only when the finger leaves the screen. When it
 * **touches** a link, the page sends a message here, and we start fetching it
 * right then. If a navigation to the same URL arrives within a few seconds, it
 * gets this response instead of a new request.
 *
 * In memory only, for a few seconds, and one response per navigation: this
 * isn't a cache. What is written below ("no page behind sign-in is served from
 * the cache") stays true: what is served here is a response born a moment
 * before the tap, not a page from days ago. */
const PREFETCH_TTL = 5000;
const prefetched = new Map();     // URL → { at, response: Promise<Response|null> }

self.addEventListener('message', function (e) {
    const data = e.data || {};
    if (data.type !== 'prefetch' || typeof data.url !== 'string') return;
    let url;
    try { url = new URL(data.url, self.location.origin); } catch (err) { return; }
    if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
    const key = url.pathname + url.search;
    const now = Date.now();
    for (const [k, v] of prefetched) if (now - v.at > PREFETCH_TTL) prefetched.delete(k);
    if (prefetched.has(key)) return;
    prefetched.set(key, {
        at: now,
        // Only a valid HTML response, with no redirect: Safari refuses to serve a
        // redirected response (e.g. to sign-in, when the session expired) to a
        // navigation. Then the navigation goes to the network.

        response: fetch(url.href, { credentials: 'same-origin' })
            .then(res => (res.ok && !res.redirected &&
                          (res.headers.get('Content-Type') || '').includes('text/html')) ? res : null)
            .catch(() => null),
    });
});

function takePrefetched(request) {
    if (request.mode !== 'navigate') return null;
    const url = new URL(request.url);
    const key = url.pathname + url.search;
    const hit = prefetched.get(key);
    if (!hit) return null;
    prefetched.delete(key);                       // one response per navigation
    if (Date.now() - hit.at > PREFETCH_TTL) return null;
    return hit.response;
}

self.addEventListener('fetch', function (e) {
    // Only intercept same-origin GET requests
    if (e.request.method !== 'GET') {
        // A save/delete/add makes every preloaded page stale: tapping "cancel"
        // then "save" within 5 seconds returned the project page to its state
        // before the save (Matan, 9.10). The write itself goes to the network as
        // usual.

        prefetched.clear();
        return;
    }
    if (!e.request.url.startsWith(self.location.origin)) return;

    const url = new URL(e.request.url);

    // API calls: always straight to the network, no cache and no fallback.
    // Transaction/category/project data changes all the time: silently serving
    // a stale response from the cache during a network failure would be worse
    // than showing a real, clear network error.

    if (url.pathname.startsWith('/api/')) {
        return;
    }

    // Static assets: stale-while-revalidate: serves from the cache immediately
    // (fast), but always fetches a fresh version in the background and updates
    // the cache, so a change to CSS/JS appears automatically on the next refresh
    // without having to bump a manual CACHE version every time.
    // Only valid responses are cached: transient 404/500s aren't stored.

    if (url.pathname.startsWith('/static/')) {
        // A fingerprinted URL (`?v=…`) is immutable by definition: a change to
        // the file produces a different fingerprint, i.e. a different URL. So if
        // it is in the cache, that is the answer, with no network check. A URL
        // without a fingerprint (e.g. a file loaded by hand) keeps the previous
        // behaviour: from the cache immediately, and a background refresh.

        const immutable = url.searchParams.has('v');
        e.respondWith(
            caches.open(CACHE).then(cache =>
                cache.match(e.request).then(cached => {
                    if (cached && immutable) return cached;
                    const network = fetch(e.request).then(res => {
                        if (res.ok) cache.put(e.request, res.clone());
                        return res;
                    // No network and no exact copy: any cached version of the
                    // same file beats nothing. The "no connection" screen
                    // requests `style.css` without `?v=` (it doesn't know the
                    // fingerprint), and the cache holds only fingerprinted URLs,
                    // so the screen meant to look like the app was shown as text.

                    }).catch(() => cached || cache.match(e.request, { ignoreSearch: true }));
                    return cached || network;
                })
            )
        );
        return;
    }

    // ── What is cached, and what never is ──
    //
    // Every page behind sign-in shows money or personal details, so none of them
    // is served from the cache. It looks like giving up on offline, and it is the
    // opposite: a user on a poor cellular connection got a full, styled screen
    // with "₪1,270 left in the account" from the previous visit, maybe from days
    // ago. The count-up animation even ran on them, so they looked fresh. Nothing
    // marked it as stale, and they could make a money decision based on it. A
    // clear message that there is no connection beats a false number.
    //
    // The root and the sign-in form are excluded for another reason: their
    // content is derived from the cookies (landing page for a guest, dashboard
    // for a signed-in user, the saved email in the form).
    //
    // Only the legal pages are cached: they are the same for everyone and don't
    // change.

    const CACHEABLE_PAGES = ['/privacy', '/terms'];

    if (!CACHEABLE_PAGES.includes(url.pathname)) {
        const early = takePrefetched(e.request);
        e.respondWith(
            (early || Promise.resolve(null))
                .then(res => res || fetch(e.request))
                .catch(() => offlinePage())
        );
        return;
    }

    // Static pages: network first, and the cache when there is no network

    e.respondWith(
        fetch(e.request)
            .then(res => {
                if (res.ok) {
                    const clone = res.clone();
                    caches.open(CACHE).then(c => c.put(e.request, clone));
                }
                return res;
            })
            .catch(() => caches.match(e.request).then(cached => cached || offlinePage()))
    );
});


/* ── Phone notifications (Matan, 31.10) ──
 * The server sends `{title, body, url, tag}` encrypted (`backend/push.py`).
 * `tag`: a new notification of the same kind replaces the previous one instead
 * of piling up. */
self.addEventListener('push', function (e) {
    let data = {};
    try { data = e.data ? e.data.json() : {}; } catch (err) { data = { body: e.data && e.data.text() }; }
    e.waitUntil(self.registration.showNotification(data.title || 'SmartFin', {
        body: data.body || '',
        tag: data.tag || undefined,
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        lang: 'he', dir: 'rtl',
        data: { url: data.url || '/' },
    }));
});

/* A tap: if the app is already open, go to it, to the notification's page;
 * otherwise a new window. Only URLs of the site itself. */
self.addEventListener('notificationclick', function (e) {
    e.notification.close();
    let target = '/';
    try {
        const u = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin);
        if (u.origin === self.location.origin) target = u.pathname + u.search;
    } catch (err) { /* default: home */ }
    e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
        for (const c of list) {
            if ('focus' in c) return c.navigate(target).then(function (w) { return (w || c).focus(); });
        }
        return self.clients.openWindow(target);
    }));
});
