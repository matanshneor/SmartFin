/* An eye for showing the password (Matan, 12.10).
 *
 * Every password field gets an eye button on the side: a tap toggles between
 * dots and text. On a phone it is easy to mistype a letter and impossible to
 * see where. `autocomplete` doesn't change: the iPhone's password manager keeps
 * working. Each field separately: showing one doesn't show the other. */
(function () {
    'use strict';
    const SVG = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
              + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"';
    const EYE = '<svg ' + SVG + '><path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/></svg>';
    const EYE_OFF = '<svg ' + SVG + '><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c4.5 0 8.3 2.94 9.94 6.65a1 1 0 0 1 0 .7 10.7 10.7 0 0 1-1.44 2.49"/>'
                  + '<path d="M14.08 14.16a3 3 0 0 1-4.24-4.24"/><path d="M17.48 17.5A10.75 10.75 0 0 1 2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 4.45-5.14"/><path d="m2 2 20 20"/></svg>';

    document.querySelectorAll('input[type="password"]').forEach(function (input) {
        if (input.closest('.pw-wrap')) return;
        const wrap = document.createElement('span');
        wrap.className = 'pw-wrap';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pw-eye';
        btn.innerHTML = EYE;
        btn.setAttribute('aria-label', 'הצגת הסיסמה');
        btn.setAttribute('aria-pressed', 'false');
        wrap.appendChild(btn);
        btn.addEventListener('click', function () {
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            btn.innerHTML = show ? EYE_OFF : EYE;
            btn.setAttribute('aria-label', show ? 'הסתרת הסיסמה' : 'הצגת הסיסמה');
            btn.setAttribute('aria-pressed', show ? 'true' : 'false');
        });
    });
})();
