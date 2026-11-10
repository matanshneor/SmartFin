// Gentle animations: counting numbers up, respecting prefers-reduced-motion.

(function () {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* What was shown last time, for every number on the page.
     *
     * The soft refresh replaces the elements themselves, so the value can't be
     * kept on them; this memory lives outside the DOM. The key is derived from
     * the classes and the position within the group, not from an id in the
     * template, so it works without touching every number. */
    const lastShown = {};

    function keyFor(el, seen) {
        const base = el.className || 'countup';
        seen[base] = (seen[base] || 0) + 1;
        return base + '#' + seen[base];
    }

    /* Count-up: from the value shown before to the new one.
     *
     * It used to always run from 0, and that wasted the whole value of the
     * animation: after a ₪100 expense you saw a number climbing from zero,
     * instead of seeing what really happened, that the balance dropped by ₪100.
     * On first load there is no previous value, and there 0 is right: it is the
     * first impression, not an update. */
    function runCountUps() {
        const seen = {};
        document.querySelectorAll('[data-countup]').forEach(function (el) {
            const target = parseFloat(el.dataset.countup);
            if (isNaN(target)) return;
            const prefix = el.dataset.prefix || '';
            // The count itself always in whole shekels, and only the final value
            // as it is, with agorot if it has them. Matan (30.9): "the money is
            // counted in agorot and then shown rounded", and "make the count-up
            // animation without agorot too". The intermediate values are
            // fractional (`1,203.57`), and `sfMoney` shows agorot when present,
            // so they flickered throughout the count.

            const step  = v => prefix + '₪' + window.sfMoney(Math.round(v));
            const final = prefix + '₪' + window.sfMoney(target);

            const key  = keyFor(el, seen);
            const prev = lastShown[key];
            lastShown[key] = { value: target, prefix: prefix };

            // Where to start from. A sign flip (surplus ↔ deficit) starts from 0:
            // the number is shown as an absolute value, and a direct transition
            // between two absolute values would show intermediate amounts with
            // the opposite sign.

            const from = (prev && prev.prefix === prefix) ? prev.value : 0;

            if (reduceMotion || from === target) {
                el.textContent = final;
                return;
            }

            const duration = 1000;
            const start = performance.now();
            function tick(now) {
                const p = Math.min((now - start) / duration, 1);
                const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
                el.textContent = p < 1 ? step(from + (target - from) * eased) : final;
                if (p < 1) requestAnimationFrame(tick);
            }
            requestAnimationFrame(tick);
        });
    }

    /* 2. Cards entering.
     *
     * What is on screen at load enters gradually (stagger), as always. What is
     * below it enters **with the scroll** (Matan, 11.10: "what comes in should
     * start coming in slowly with the scroll"): until then the cards below the
     * screen also "entered" on load, when nobody saw, and scrolling to them they
     * just stood there.
     *
     * The entrance is tied to the scroll position and not to time: fast is fast,
     * slow is slow, stopping stops, and scrolling back reverses. In a browser
     * that supports it (`animation-timeline: view()`, Safari 26 and Chrome) this
     * is CSS only, with no JS during scrolling. In an old browser: a soft
     * entrance the moment the card appears (`IntersectionObserver`). */
    const REVEAL = '.summary-cards, .chart-card, .transaction-item, .settings-card, ' +
                   '.month-item, .alert-card, .kpi-card, .empty-state, .settings-group, ' +
                   '.empty-row, .project-tile';
    const scrollTimeline = !!(window.CSS && CSS.supports && CSS.supports('animation-timeline: view()'));
    let revealObserver = null;

    function belowTheFold(el) { return el.getBoundingClientRect().top > window.innerHeight - 40; }

    function setupReveal(els) {
        if (reduceMotion) return;
        els.forEach(function (el) {
            if (el.classList.contains('reveal-scroll') || el.classList.contains('reveal-fallback')) return;
            if (!belowTheFold(el)) return;
            if (scrollTimeline) { el.classList.add('reveal-scroll'); return; }
            if (!('IntersectionObserver' in window)) return;
            if (!revealObserver) {
                revealObserver = new IntersectionObserver(function (entries) {
                    entries.forEach(function (en) {
                        if (!en.isIntersecting) return;
                        en.target.classList.add('is-in');
                        revealObserver.unobserve(en.target);
                    });
                }, { rootMargin: '0px 0px -60px 0px', threshold: 0.05 });
            }
            el.classList.add('reveal-fallback');
            revealObserver.observe(el);
        });
    }

    function runStagger() {
        if (reduceMotion) return;
        const all = Array.prototype.slice.call(document.querySelectorAll(REVEAL));
        const below = all.filter(belowTheFold);
        // Only what was in the original entrance list and is visible now, as before

        const items = all.filter(function (el) {
            return below.indexOf(el) === -1 && !el.matches('.settings-group, .empty-row, .project-tile');
        });
        setupReveal(below);
        items.forEach(function (el, i) {
            el.style.animationDelay = Math.min(i * 55, 600) + 'ms';
            el.classList.add('anim-rise');
            // At the end of the entrance, release the card. An animation that
            // ended with `forwards` keeps "holding" `transform`, and wins over
            // what a swipe writes to it: the "this week" card didn't move with
            // the finger on the first swipe (Matan, 11.10).

            el.addEventListener('animationend', function done(e) {
                if (e.target !== el || e.animationName !== 'rise-in') return;
                el.removeEventListener('animationend', done);
                el.classList.remove('anim-rise');
                el.style.animationDelay = '';
            });
        });
    }

    // 3. Progress bars stretch from 0 to their final width

    function runBars() {
        if (reduceMotion) return;
        document.querySelectorAll('.balance-bar-fill')
            .forEach(function (bar) {
                const finalWidth = bar.style.width;
                if (!finalWidth) return;
                bar.style.width = '0%';
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () {
                        bar.style.width = finalWidth;
                    });
                });
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        runCountUps();
        runStagger();
        runBars();
    });

    /* After a soft refresh the numbers were replaced with new elements, and the
     * count-up needs to run on them: that is exactly when it is worth the most,
     * because it is the moment the user is waiting to see the new number.
     *
     * Deliberately not `runStagger`: an entrance animation belongs to entering
     * the page, not to an in-place update. Every card entering again on every
     * transaction added is noise. */
    window.addEventListener('sf:refreshed', function () {
        runCountUps();
        runBars();
        // New content (soft refresh, month switch): what is below the screen
        // enters with the scroll

        setupReveal(Array.prototype.slice.call(document.querySelectorAll(REVEAL)));
    });
})();
