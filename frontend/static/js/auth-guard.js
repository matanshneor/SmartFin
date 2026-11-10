/* An expired session: detected once, not in forty places.
 *
 * When the session ended, every call to the server used to come back as the
 * sign-in page with status 200, because fetch follows redirects silently. Each
 * of the app's 40 calls did r.json() on HTML, failed, and fell into a catch
 * saying "network error, try again". The user would retry and get exactly the
 * same, forever, with no hint that the fix is to sign in again. The server now
 * returns a real 401, and it is detected here.
 *
 * Wraps fetch itself rather than adding a helper that has to be called: that
 * way a call written tomorrow is covered without anyone remembering. A separate
 * file with no dependencies, because it is also loaded on the standalone pages
 * (onboarding) that don't load core.js.
 */
(function () {
    if (!window.fetch) return;

    const nativeFetch = window.fetch.bind(window);
    let handling = false;

    function isOurs(input) {
        // Only requests to our server: a 401 from an external service shouldn't
        // sign anyone out

        const url = (typeof input === 'string') ? input : (input && input.url) || '';
        return url.startsWith('/') || url.startsWith(window.location.origin);
    }

    window.fetch = function (input, init) {
        return nativeFetch(input, init).then(function (response) {
            if (response.status === 401 && isOurs(input) && !handling) {
                handling = true;
                if (window.showToast) {
                    window.showToast('ההתחברות הסתיימה — מעבירים להתחברות', 'error');
                }
                // A short pause so the message can be read; without it the
                // screen just switches

                setTimeout(function () { window.location.href = '/login'; }, 1400);
            }
            return response;
        });
    };
})();
