/* "How to add a family member": one sheet, on the first visit to home after
 * the onboarding wizard (Matan, 5.10). The server renders it only with
 * `?welcome=1` and only when there is nobody else in the family; here it opens,
 * sends the invite, and removes the parameter from the URL, so a refresh
 * doesn't open it again. */
(function () {
    const sheet = document.getElementById('welcomeInvite');
    if (!sheet) return;

    if (window.history.replaceState) {
        const url = new URL(window.location.href);
        url.searchParams.delete('welcome');
        window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    }

    const sendBtn = document.getElementById('welcomeSendBtn');
    const close = function () { sheet.hidden = true; };
    document.getElementById('welcomeCloseBtn').addEventListener('click', close);
    sheet.addEventListener('click', function (e) { if (e.target === sheet) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });

    sendBtn.addEventListener('click', function () {
        const msg = window.sfInviteMessage(sheet.dataset.code, sheet.dataset.family);
        // On a phone, the system share sheet (WhatsApp directly). On desktop, or
        // if sharing failed, copy, as in settings.

        if (navigator.share) {
            navigator.share({ text: msg }).then(close).catch(function (err) {
                if (err && err.name === 'AbortError') return;     // the share sheet was closed
                copy(msg);
            });
            return;
        }
        copy(msg);
    });

    function copy(msg) {
        window.copyToClipboard(msg, document.getElementById('welcomeCode')).then(function (copied) {
            if (!copied) return;              // a message was already shown, and the code is selected
            sendBtn.textContent = '✓ ההזמנה הועתקה — הדביקו אותה בוואטסאפ';
        });
    }

})();
