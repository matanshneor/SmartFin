/* A loading skeleton (Matan, 30.9, round 6, item 14).
 *
 * Moving between pages is a full load. On a slow network (train, lift) the tap
 * did nothing visible until the next page arrived, and it wasn't clear whether
 * it registered. Now, if after a quarter of a second we haven't left yet, a
 * skeleton in the shape of the destination page appears. On a fast network it
 * doesn't appear at all, so it doesn't flash for nothing.
 *
 * It doesn't speed anything up. It says "got it, on the way". */
(function () {
    const DELAY = 250;
    let timer = null, overlay = null;

    // The shape per page: [how many tiles in the top row, then card heights]

    const SHAPES = {
        '/':         { chips: 3, blocks: [110, 64, 64, 64] },
        '/month':    { chips: 3, blocks: [230, 230, 120] },
        '/months':   { chips: 0, blocks: [260, 200, 180] },
        '/projects': { chips: 0, blocks: [72, 72, 72] },
        '/settings': { chips: 0, blocks: [60, 60, 60, 60, 60, 60] },
    };

    function build(shape) {
        const el = document.createElement('div');
        el.className = 'skeleton-screen';
        el.setAttribute('aria-hidden', 'true');
        const hero = document.createElement('div');
        hero.className = 'skeleton-hero';
        hero.innerHTML = '<span class="sk sk-line" style="width:38%"></span>'
                       + '<span class="sk sk-line sk-big" style="width:52%"></span>';
        el.appendChild(hero);
        const body = document.createElement('div');
        body.className = 'skeleton-body';
        if (shape.chips) {
            const row = document.createElement('div');
            row.className = 'skeleton-chips';
            for (let i = 0; i < shape.chips; i++) row.appendChild(Object.assign(document.createElement('span'), { className: 'sk sk-card' }));
            body.appendChild(row);
        }
        shape.blocks.forEach(function (h) {
            const b = document.createElement('span');
            b.className = 'sk sk-card';
            b.style.height = h + 'px';
            body.appendChild(b);
        });
        el.appendChild(body);
        return el;
    }

    function hide() {
        clearTimeout(timer);
        timer = null;
        if (overlay) { overlay.remove(); overlay = null; }
    }

    document.addEventListener('click', function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const a = e.target.closest && e.target.closest('a[href]');
        if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
        const url = new URL(a.href, location.href);
        if (url.origin !== location.origin) return;
        if (url.pathname === location.pathname && url.search === location.search) return;   // only an anchor on the same page
        const shape = SHAPES[url.pathname] || (url.pathname.indexOf('/projects/') === 0 ? SHAPES['/month'] : null);
        if (!shape) return;
        hide();
        timer = setTimeout(function () {
            // Other code cancelled the navigation after we got the tap: nothing to
            // wait for, and a skeleton shown here would never go away

            if (e.defaultPrevented) return;
            overlay = build(shape);
            document.body.appendChild(overlay);
            // The tapped tab in the bottom menu is already marked: "got it, on
            // the way there"

            if (a.closest('.bottom-nav')) {
                a.closest('.bottom-nav').querySelectorAll('.nav-item').forEach(function (n) {
                    n.classList.toggle('active', n === a);
                });
            }
        }, DELAY);
    });
    // Going back from the browser's cache: the page comes back as it was, with
    // the skeleton on top

    window.addEventListener('pageshow', hide);
    window.addEventListener('pagehide', function () { clearTimeout(timer); });
})();
