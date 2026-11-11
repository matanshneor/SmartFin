// Registering the Service Worker and offering installation to the home screen.

if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js')
            .catch(function () {});
    });
}

// ── "Add to home screen" (Matan, 13.10) ──
//
// A fixed bar above the bottom menu, until the app is opened once from the icon.
// Then the server remembers (`/api/app-installed` → `data-installed`), and the bar
// doesn't come back even when coming in from the browser. ✕ hides it until the
// next visit (`sessionStorage`). Until then there was a yellow message that closed
// forever with one tap, and text with no guidance.
//
// On iPhone there is no one-tap install: "how?" opens three steps, with no
// browser name: someone coming in from Chrome or Firefox also adds it from the
// share button. From inside WhatsApp, Instagram or Facebook it isn't possible at
// all: there the first step is to open in the browser. On Android "install" opens
// the phone's install prompt; without it, three steps from the menu.

(function () {
    const bar = typeof document.getElementById === 'function' && document.getElementById('installBar');
    if (!bar) return;
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches
        || window.navigator.standalone === true;
    if (isStandalone) {
        // Opened from the icon: once per visit is enough; the server ignores duplicates

        let told = false;
        try { told = sessionStorage.getItem('sf_app_marked') === '1'; } catch (e) { /* no storage */ }
        if (!told && !bar.hasAttribute('data-installed')) {
            fetch('/api/app-installed', { method: 'POST', credentials: 'same-origin',
                                          headers: { 'Accept': 'application/json' } })
                .then(function (r) { if (r.ok) try { sessionStorage.setItem('sf_app_marked', '1'); } catch (e) { /* */ } })
                .catch(function () { /* next time */ });
        }
        return;
    }
    if (bar.hasAttribute('data-installed')) return;
    try { if (sessionStorage.getItem('sf_install_hidden') === '1') return; } catch (e) { /* show it */ }

    const ua = window.navigator.userAgent;
    const isIOS = /iphone|ipad|ipod/i.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    const inApp = /FBAN|FBAV|Instagram|Line\/|LinkedInApp|TikTok|Snapchat|WhatsApp/i.test(ua);
    const go = document.getElementById('installBarGo');
    let deferredPrompt = null;

    function show() {
        bar.hidden = false;
        document.body.classList.add('has-install-bar');
    }
    function hide() {
        bar.hidden = true;
        document.body.classList.remove('has-install-bar');
    }
    document.getElementById('installBarClose').addEventListener('click', function () {
        hide();
        try { sessionStorage.setItem('sf_install_hidden', '1'); } catch (e) { /* comes back on the next page */ }
    });

    window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        deferredPrompt = e;
        document.getElementById('installBarTitle').textContent = 'התקינו את SmartFin';
        document.getElementById('installBarSub').textContent = 'אייקון במסך הבית, כמו אפליקציה';
        go.textContent = 'התקנה';
    });
    window.addEventListener('appinstalled', hide);

    const SHARE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 10H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-2"/><path d="M12 15V3M8 7l4-4 4 4"/></svg>';
    const PLUS  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 8v8M8 12h8"/></svg>';
    const DOTS  = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    const ICON  = '<img src="/static/icons/icon-180.png" alt="">';

    function steps() {
        if (inApp) return [
            [DOTS, 'פתחו את הקישור <b>בדפדפן</b>', 'מהתפריט של האפליקציה שבה אתם נמצאים — "פתיחה בדפדפן"'],
            [isIOS ? SHARE : DOTS, isIOS ? 'שם — <b>שיתוף</b>, ואז <b>הוסף למסך הבית</b>' : 'שם — התפריט, ואז <b>הוסף למסך הבית</b>', ''],
            [ICON, 'ופתחו את SmartFin <b>מהאייקון</b>', ''],
        ];
        if (isIOS) return [
            [SHARE, 'הקישו על כפתור <b>השיתוף</b> של הדפדפן', 'הריבוע עם החץ למעלה'],
            [PLUS, 'בחרו <b>הוסף למסך הבית</b>', 'לא רואים? גללו למטה, או תחת "עוד"'],
            [ICON, 'הקישו <b>"הוסף"</b> — ופתחו את SmartFin מהאייקון', ''],
        ];
        return [
            [DOTS, 'פתחו את <b>התפריט</b> של הדפדפן', 'שלוש הנקודות'],
            [PLUS, 'בחרו <b>הוסף למסך הבית</b> או <b>התקנת אפליקציה</b>', ''],
            [ICON, 'אשרו — ופתחו את SmartFin מהאייקון', ''],
        ];
    }
    function guide() {
        const sheet = document.createElement('div');
        sheet.className = 'color-sheet install-guide';
        sheet.setAttribute('role', 'dialog');
        sheet.setAttribute('aria-modal', 'true');
        sheet.setAttribute('aria-label', 'הוספה למסך הבית');
        let html = '<div class="color-sheet-card"><p class="color-sheet-title">הוספה למסך הבית</p>'
                 + '<p class="color-sheet-hint">שלושה צעדים, פעם אחת — ומאז SmartFin נפתחת מהאייקון</p><ol class="install-steps">';
        steps().forEach(function (s, k) {
            html += '<li><span class="install-step-num">' + (k + 1) + '</span><span class="install-step-text">' + s[1]
                  + (s[2] ? '<span class="install-step-hint">' + s[2] + '</span>' : '')
                  + '</span><span class="install-step-pic">' + s[0] + '</span></li>';
        });
        html += '</ol><button type="button" class="btn-sm btn-ghost color-sheet-close">הבנתי</button></div>';
        sheet.innerHTML = html;      // fixed text from this file only: no user input here
        document.body.appendChild(sheet);
        bar.hidden = true;                      // the bar isn't shown over the guide
        function close() { sheet.remove(); bar.hidden = false; go.focus({ preventScroll: true }); }
        sheet.addEventListener('click', function (e) {
            if (e.target === sheet || e.target.closest('.color-sheet-close')) close();
        });
        document.addEventListener('keydown', function esc(e) {
            if (e.key === 'Escape') { document.removeEventListener('keydown', esc); close(); }
        });
        requestAnimationFrame(function () { sheet.querySelector('.color-sheet-close').focus(); });
    }
    go.addEventListener('click', function () {
        if (deferredPrompt) {
            deferredPrompt.prompt();
            deferredPrompt.userChoice.finally(function () { deferredPrompt = null; });
            return;
        }
        guide();
    });
    show();
})();


// ── Pull down to refresh, only in the installed app (Matan, 30.9, idea 14) ──
//
// Regular Safari has it by itself. In an app opened from the home screen there
// is no address bar and no pull, and no way at all to refresh: whoever was
// waiting for a transaction their partner entered had to close and reopen. Start
// only at the top of the page, only on a vertical drag (a sideways swipe on a row
// is edit/delete), and not while a sheet or editor is open.
//
// Like on iPhone (Matan, 13.10): the page itself comes down with the finger, with
// spring resistance (the more you pull, the less it moves), and in the dark band
// revealed above it, iOS's loading spinner, whose spokes appear one by one with
// the pull. Releasing past the threshold stops the page slightly down and the
// spinner turns until the refresh finishes; then everything returns up with a
// soft bounce.

(function () {
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches
        || window.navigator.standalone === true;
    if (!isStandalone || !('ontouchstart' in window)) return;

    const START = 8;        // up to here it isn't a drag yet
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // The top band of the screen is under the clock and battery: the spinner
    // appears below it

    const probe = document.createElement('div');
    probe.style.cssText = 'position:fixed;top:0;height:env(safe-area-inset-top,0px);visibility:hidden;pointer-events:none';
    document.body.appendChild(probe);
    const SAFE = probe.getBoundingClientRect().height || 0;
    probe.remove();
    const TRIGGER = SAFE + 58;    // pulling past this refreshes on release
    const HOLD = SAFE + 50;       // here the page waits during the refresh
    const RANGE = 320;            // the spring: the closer to this, the less it moves

    const layer = document.createElement('div');
    layer.className = 'ptr-layer';
    layer.setAttribute('aria-hidden', 'true');
    let spokes = '';
    for (let k = 0; k < 8; k++) {
        spokes += '<line x1="12" y1="2.5" x2="12" y2="7" transform="rotate(' + (k * 45) + ' 12 12)"'
                + ' style="opacity:' + (1 - k * 0.11).toFixed(2) + '"/>';
    }
    layer.innerHTML = '<svg class="ptr-spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
                    + ' stroke-width="2.4" stroke-linecap="round">' + spokes + '</svg>';
    document.body.appendChild(layer);
    const spinner = layer.firstChild;
    const lines = spinner.querySelectorAll('line');

    // What moves: the dark header and the content (`body > .page-hero, body >
    // .main-content`, in CSS, by a variable on `body`). Not `style` on the
    // elements themselves: the soft refresh replaces them midway, and the page
    // would jump up before the spinner finished. The bottom menu and the +
    // button stay in place.

    const root = document.body;

    let startX = 0, startY = null, pulling = false, pull = 0, busy = false;

    function place(dist, animate) {
        pull = dist;
        const t = animate ? (calm ? 'transform .15s ease' : 'transform .45s cubic-bezier(.2, 1.1, .3, 1)') : 'none';
        root.classList.add('ptr-active');
        root.style.setProperty('--ptr-t', t);
        root.style.setProperty('--ptr-y', dist + 'px');
        layer.style.transition = animate ? t.replace('transform', 'height') : 'none';
        layer.style.height = dist + 'px';
        // The spinner in the middle of the band's visible part, under the clock

        const visible = Math.max(0, dist - SAFE);
        spinner.style.top = (SAFE + Math.max(0, visible / 2 - 12)) + 'px';
        spinner.style.opacity = Math.min(1, visible / 28);
    }
    function progress(dist) {
        // The spokes appear one by one up to the threshold, like on iPhone

        const n = busy ? 8 : Math.max(0, Math.min(8, Math.round((dist - SAFE) / (TRIGGER - SAFE) * 8)));
        lines.forEach(function (l, k) { l.style.visibility = k < n ? 'visible' : 'hidden'; });
    }
    function springBack() {
        startY = null; pulling = false;
        layer.classList.remove('spinning');
        place(0, true);
        setTimeout(function () {
            if (busy || pulling) return;
            // No `transform` at all at rest; otherwise `fixed` sheets inside the
            // page are trapped in it

            root.classList.remove('ptr-active');
            layer.style.height = '0px';
        }, 480);
    }
    // A sheet is open (search, budgets, icons, colours, confirm). Inside it a
    // downward drag is scrolling its list; before, it was caught as a pull, the
    // list didn't scroll up and the page behind refreshed.

    const OPEN_WINDOWS = '.modal-overlay.open, .transaction-item.open, .confirm-overlay.open, '
                       + '.search-screen:not([hidden]), .icon-picker:not([hidden]), .color-sheet:not([hidden])';
    function blocked(target) {
        return busy || window.scrollY > 0
            || document.querySelector(OPEN_WINDOWS)
            || (target.closest && target.closest('input, textarea, select, canvas, .tx-cat-chips, [role="dialog"]'));
    }

    document.addEventListener('touchstart', function (e) {
        if (e.touches.length !== 1 || blocked(e.target)) { startY = null; return; }
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        pulling = false;
    }, { passive: true });

    document.addEventListener('touchmove', function (e) {
        if (startY === null) return;
        const dx = e.touches[0].clientX - startX;
        const dy = e.touches[0].clientY - startY;
        if (!pulling) {
            // Scrolling up or a sideways move: not ours

            if (dy < 0 || Math.abs(dx) > Math.abs(dy)) {
                if (Math.abs(dx) > START || dy < -START) startY = null;
                return;
            }
            if (dy < START) return;
            pulling = true;
        }
        if (window.scrollY > 0) { springBack(); return; }
        e.preventDefault();
        const x = Math.max(0, dy - START);
        const dist = (1 - 1 / (x * 0.55 / RANGE + 1)) * RANGE;     // spring
        place(dist, false);
        progress(dist);
    }, { passive: false });

    document.addEventListener('touchend', function () {
        if (!pulling) { startY = null; return; }
        if (pull < TRIGGER) { springBack(); return; }
        busy = true;
        pulling = false;
        progress(HOLD);
        layer.classList.add('spinning');
        place(HOLD, true);
        // A page safe to swap: a soft refresh, with no flash; anything else: a
        // full reload

        const soft = document.querySelector('main[data-soft-reload]') && window.softReload;
        if (!soft) { setTimeout(function () { window.location.reload(); }, 250); return; }
        const shown = new Promise(function (r) { setTimeout(r, 500); });   // so they get to see it turned
        Promise.all([Promise.resolve(window.softReload()).catch(function () {}), shown]).then(function () {
            busy = false; springBack();
        });
    });
})();


// ── The bottom menu stays at the bottom, only in the installed app on iPhone
// (Matan, 1.10) ──
//
// A known iOS bug in a home-screen app: `position: fixed` elements "drift" while
// scrolling and get stuck in the middle of the screen until a page change (Matan:
// "just from scrolling", "it stays stuck"). A page change fixes it because it
// recalculates them. So when scrolling stops we recalculate them ourselves, and
// if the visible screen and the screen the browser calculates don't end in the
// same place, move them to the real bottom. Not while typing: the keyboard
// shortens the visible screen on purpose, and the menu shouldn't climb above it.

(function () {
    if (window.navigator.standalone !== true) return;
    const vv = window.visualViewport;

    window.sfDockOffset = function (innerHeight, vvHeight, vvOffsetTop) {
        const gap = Math.round(innerHeight - (vvHeight + vvOffsetTop));
        return Math.abs(gap) > 2 ? -gap : 0;
    };

    function typing() {
        const a = document.activeElement;
        return a && a.matches && a.matches('input, textarea, select, [contenteditable="true"]');
    }

    // Past the top or the bottom of the page (the rubber-band pull) the visible
    // screen is pulled along with the finger: a shift measured then sent the menu
    // off the screen until the next page (Matan's iPhone, 11.11). Measure again
    // once the page has sprung back.
    function overscrolled() {
        const max = document.documentElement.scrollHeight - window.innerHeight;
        return window.scrollY < 0 || window.scrollY > max + 1;
    }

    function settle() {
        if (overscrolled()) { soon(); return; }
        const shift = (vv && !typing())
            ? window.sfDockOffset(window.innerHeight, vv.height, vv.offsetTop) : 0;
        document.querySelectorAll('.bottom-nav, .fab').forEach(function (el) {
            // Recalculate: briefly leave fixed and come back, with no paint in between

            el.style.position = 'absolute';
            void el.offsetHeight;
            el.style.position = '';
            // `translate` and not `transform`: doesn't override the + button's
            // press (`scale`)

            el.style.translate = shift ? '0 ' + shift + 'px' : '';
        });
    }

    let timer = null;
    function soon() { clearTimeout(timer); timer = setTimeout(settle, 120); }
    window.addEventListener('scroll', soon, { passive: true });
    if (vv) { vv.addEventListener('resize', soon); vv.addEventListener('scroll', soon); }
    window.addEventListener('pageshow', soon);
    window.addEventListener('sf:refreshed', soon);
    document.addEventListener('focusout', soon);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) soon(); });
})();



// ── Preloading on touching a link (Matan, 3.10, idea 29) ──
// `pointerdown`: a touch on a phone, a click with a mouse. See `sw.js`: the page
// starts loading right now, and the navigation that arrives when the finger lifts
// gets it.

(function () {
    if (!('serviceWorker' in navigator)) return;
    document.addEventListener('pointerdown', function (e) {
        const sw = navigator.serviceWorker.controller;
        if (!sw) return;
        const a = e.target.closest && e.target.closest('a[href]');
        if (!a || a.target || a.hasAttribute('download')) return;
        let url;
        try { url = new URL(a.href, location.href); } catch (err) { return; }
        if (url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
        // The same page (or just an anchor in it): nothing to load

        if (url.pathname === location.pathname && url.search === location.search) return;
        sw.postMessage({ type: 'prefetch', url: url.pathname + url.search });
    }, { passive: true, capture: true });
})();