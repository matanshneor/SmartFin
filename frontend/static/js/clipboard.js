/* The message copied when sharing an invite code.
 *
 * Until now six characters were copied and nothing else. Whoever got "K4F2QX"
 * on WhatsApp got six characters with no name, no link and no hint what to do
 * with them, while the landing page sells this as "invite your household with
 * a six-character code".
 *
 * The code stays on its own line with no punctuation attached, so it can be
 * selected with a long press without dragging a period along. */
window.sfInviteMessage = function (code, familyName) {
    const who = familyName ? ('\u200f"' + familyName + '"') : '\u200fהמשפחה שלנו';
    return 'הצטרפו אליי ל-SmartFin — התקציב המשותף של ' + who + '.\n\n'
         + 'להצטרפות בלחיצה:\n' + window.location.origin + '/join/' + encodeURIComponent(code) + '\n\n'
         + 'או קוד ההזמנה:\n' + code;
};

/* Copying to the clipboard, with a fallback.
 *
 * `navigator.clipboard` fails more than it seems: a denied permission, an
 * insecure context, an in-app browser (the link opened from WhatsApp), or the
 * object simply doesn't exist. The previous code called `.then()` with no
 * `.catch`, so a failure was an unhandled rejection: the button didn't change,
 * no message appeared, nothing happened.
 *
 * And this is the action that makes a family a family: without passing on the
 * code, nobody joins.
 *
 * The fallback selects the text on screen, so the user is left with one action
 * (copy) instead of wondering why nothing happens. */
window.copyToClipboard = function (text, el) {
    function selectInstead() {
        try {
            const range = document.createRange();
            range.selectNodeContents(el);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            if (window.showToast) window.showToast('הקוד מסומן — העתיקו אותו ידנית');
        } catch (e) {
            if (window.showToast) window.showToast('לא הצלחנו להעתיק. הקוד: ' + text, 'error');
        }
        return false;
    }

    if (!navigator.clipboard || !navigator.clipboard.writeText) {
        return Promise.resolve(selectInstead());
    }
    return navigator.clipboard.writeText(text)
        .then(function () { return true; })
        .catch(function () { return selectInstead(); });
};
