/* Dark mode, manual only (Matan, 30.9, round 6, item 4).
 *
 * Loaded in <head> before the page is painted, so it doesn't flash white for a
 * moment. A file and not a line inside the template: the CSP blocks scripts
 * inside the page. The choice is stored on the phone (not for the whole family):
 * whoever likes dark doesn't force it on their partner. */
(function () {
    try {
        if (localStorage.getItem('sf_theme') === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
        }
        // Text size (round 6, item 9): the same moment and the same reason: no
        // jump on load

        const size = localStorage.getItem('sf_text_size');
        if (size === 'large' || size === 'xlarge') {
            document.documentElement.setAttribute('data-text-size', size);
        }
    } catch (e) { /* no storage: stay light and at the regular size */ }
})();
