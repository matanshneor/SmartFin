/* The contact address, assembled in the browser.
 *
 * The legal pages are open to the whole internet and marked for indexing, and
 * that is exactly what bots crawl: they look for `mailto:` and strings with
 * `@`. An address sitting there as plain text gets harvested within days.
 *
 * Assembling it here keeps what matters: the address is real text in the DOM,
 * so a screen reader reads it, it can be selected and copied, and the link
 * works. A crawler that doesn't run JavaScript (most don't) sees nothing.
 *
 * `<noscript>` has a human-readable phrasing, so contact is possible without
 * JavaScript too. This isn't perfect protection, and it isn't meant to be: the
 * goal is to block automated harvesting, not a person looking.
 */
(function () {
    document.querySelectorAll('[data-contact]').forEach(function (el) {
        const user   = el.dataset.user;
        const domain = el.dataset.domain;
        if (!user || !domain) return;

        const address = user + '@' + domain;
        const link = document.createElement('a');
        link.href = 'mailto:' + address;
        link.textContent = address;
        el.textContent = '';
        el.appendChild(link);
    });
})();
