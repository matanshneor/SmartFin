/* Dark mode: light, dark, or following the phone (Matan, 30.9, round 6, item 4;
 * "following the phone" since 23.11, until then manual only).
 *
 * Loaded in <head> before the page is painted, so it doesn't flash white for a
 * moment. A file and not a line inside the template: the CSP blocks scripts
 * inside the page. The choice is stored on the phone (not for the whole family):
 * whoever likes dark doesn't force it on their partner. */
(function () {
    try {
        // Text size (round 6, item 9): the same moment and the same reason: no
        // jump on load

        const size = localStorage.getItem('sf_text_size');
        if (size === 'large' || size === 'xlarge') {
            document.documentElement.setAttribute('data-text-size', size);
        }
        // Read inside its own guard: `apply` also runs later, from settings and
        // when the phone changes, outside this block
        const stored = function () {
            try { return localStorage.getItem('sf_theme'); } catch (e) { return null; }
        };
        // `auto`: whatever the phone is in now, and again when it changes (the
        // evening switch) while the app is open
        const phone = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
        const apply = function () {
            const theme = stored();
            const dark = theme === 'dark' || (theme === 'auto' && phone && phone.matches);
            if (dark) document.documentElement.setAttribute('data-theme', 'dark');
            else document.documentElement.removeAttribute('data-theme');
        };
        const theme = stored();
        if (theme === 'dark' || theme === 'auto') apply();
        window.sfApplyTheme = apply;                    // settings: right after a choice
        if (phone && phone.addEventListener) phone.addEventListener('change', function () {
            if (stored() === 'auto') apply();
        });
    } catch (e) { /* no storage: stay light and at the regular size */ }
})();
