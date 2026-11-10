/* Offline mode: say it before, not after.
 *
 * Until now the app had no notion of there being no network. Someone in an
 * underground car park typed a transaction, tapped save, and got "network
 * error, try again": the same message shown when the server is down and when
 * there is a bug. They tried again, got the same, and there was no hint that
 * the problem was on their side and not ours, and that the fix is to leave the
 * car park.
 *
 * Two layers, both small:
 *   · A top bar that appears when there is no connection, so the surprise
 *     arrives before the typing.
 *   · An error message that can tell "no network" from "something went wrong".
 *
 * Deliberately not here: a queue that syncs transactions when the network
 * returns. That is a whole sync system (duplicates, conflicts, and
 * transactions recorded hours after the user forgot about them). In a money
 * app it is worse than the problem it solves.
 *
 * No dependencies, because it is also loaded on the standalone pages (sign-in,
 * password reset, onboarding) that don't load core.js.
 */
(function () {
    var GENERIC = 'שגיאת רשת — נסו שוב';
    var OFFLINE = 'אין חיבור לאינטרנט — נסו שוב כשהחיבור יחזור';

    /* Rely only on the reliable direction.
     *
     * `navigator.onLine === true` only says there is an active network
     * interface: it returns true even when connected to a router with no
     * internet, so nothing can be concluded from it about everything being
     * fine. `false`, by contrast, is reliable: the browser knows for sure there
     * is no connection. So it adds information when negative, and takes none
     * away when positive. */
    function definitelyOffline() {
        return navigator.onLine === false;
    }

    /* The message every network-failure spot shows. A function and not a string
     * because the answer depends on the state at the moment of failure, not at
     * the moment the file loaded. */
    window.sfNetError = function () {
        return definitelyOffline() ? OFFLINE : GENERIC;
    };

    /* A list from the server (categories, family members, projects), or an
     * error. Never something in between.
     *
     * `fetch().then(r => r.json())` doesn't check the status: a 500 or 429
     * response is `{error: …}`, and it was cached as if it were the list. From
     * that moment every tap on + fell on `.filter` of an object and showed
     * "network error", even after the server recovered, because the cache was
     * already "full". A rejection here isn't stored in any cache, so the next
     * tap tries again. */
    window.sfFetchList = function (url) {
        return fetch(url).then(function (r) {
            return r.json().catch(function () { return null; }).then(function (data) {
                if (!r.ok || !Array.isArray(data)) {
                    throw new Error((data && data.error) || ('HTTP ' + r.status));
                }
                return data;
            });
        });
    };

    var banner = null;

    function showBanner() {
        if (!banner) {
            banner = document.createElement('div');
            banner.className = 'net-banner';
            // status and not alert: this is a status message, not something to
            // interrupt what the screen reader is currently reading for

            banner.setAttribute('role', 'status');
            banner.textContent =
                'אין חיבור לאינטרנט. מה שהקלדתם נשאר על המסך — נסו שוב כשהחיבור יחזור.';
            document.body.appendChild(banner);
        }
        banner.hidden = false;
    }

    function hideBanner() {
        if (!banner || banner.hidden) return;
        banner.hidden = true;
        if (window.showToast) window.showToast('החיבור חזר');
    }

    window.addEventListener('offline', showBanner);
    window.addEventListener('online', hideBanner);

    // Someone who opened the app when there was already no connection gets no
    // event, only a state

    if (definitelyOffline()) showBanner();
})();
