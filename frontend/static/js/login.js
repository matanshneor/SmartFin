// Safety net: if a password-reset link landed here (fallback to SITE_URL),
// forward it to the reset page with the token.

if (window.location.hash.includes('type=recovery')) {
    window.location.replace('/reset-password' + window.location.hash);
}


(function () {
    const tabLogin    = document.getElementById('tabLogin');
    const tabSignup   = document.getElementById('tabSignup');
    const loginPanel  = document.getElementById('loginPanel');
    const signupPanel = document.getElementById('signupPanel');

    function showTab(tab) {
        const isSignup = tab === 'signup';
        tabLogin.classList.toggle('active', !isSignup);
        tabSignup.classList.toggle('active', isSignup);
        tabLogin.setAttribute('aria-pressed', isSignup ? 'false' : 'true');
        tabSignup.setAttribute('aria-pressed', isSignup ? 'true' : 'false');
        loginPanel.style.display  = isSignup ? 'none' : 'block';
        signupPanel.style.display = isSignup ? 'block' : 'none';
        // The "forgot password" screen isn't closed here, so tapping a tab after
        // it showed a reset form **above** the signup form, with two navigation
        // controls disagreeing about where the user is, exactly on a path people
        // reach already frustrated.

        const forgot = document.getElementById('forgotPanel');
        if (forgot) forgot.style.display = 'none';
    }

    tabLogin.addEventListener('click', () => showTab('login'));
    tabSignup.addEventListener('click', () => showTab('signup'));

    // ── Forgot password ──

    const forgotPanel = document.getElementById('forgotPanel');
    const forgotError = document.getElementById('forgotError');
    const forgotSuccess = document.getElementById('forgotSuccess');

    document.getElementById('forgotLink').addEventListener('click', function (e) {
        e.preventDefault();
        loginPanel.style.display = 'none';
        forgotPanel.style.display = 'block';
    });

    document.getElementById('backToLoginLink').addEventListener('click', function (e) {
        e.preventDefault();
        forgotPanel.style.display = 'none';
        loginPanel.style.display = 'block';
        forgotError.textContent = '';
        forgotSuccess.textContent = '';
    });

    document.getElementById('forgotBtn').addEventListener('click', function () {
        const email = document.getElementById('forgotEmail').value.trim();
        forgotError.textContent = '';
        forgotSuccess.textContent = '';

        if (!email) {
            forgotError.textContent = 'נא להזין מייל';
            return;
        }

        const btn = this;
        btn.disabled = true;
        btn.textContent = 'שולח…';

        fetch('/api/auth/forgot', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ email: email }),
        })
        .then(function (r) {
            return r.json().catch(function () { return {}; })
                .then(function (d) { return { ok: r.ok, data: d || {} }; });
        })
        .then(function (res) {
            btn.disabled = false;
            btn.textContent = 'שליחת קישור איפוס';
            // "Sent" also appeared on 422 (invalid email), on 429 and on
            // Supabase's limit of 2 emails an hour, and the server already knew
            // how to tell the truth. Whoever entered a wrong email waited for an
            // email that would never come. When the server says it failed, that
            // is what is shown.
            //
            // Success stays vague on purpose ("if the email is registered"), even
            // when it isn't registered; otherwise this form is a way to check
            // which emails exist in the system.

            if (!res.ok) {
                forgotError.textContent = res.data.error || 'השליחה נכשלה — נסו שוב בעוד רגע';
                return;
            }
            forgotSuccess.textContent = 'אם המייל רשום במערכת — קישור איפוס נשלח אליו עכשיו. כדאי לבדוק גם בספאם.';
        })
        .catch(function () {
            forgotError.textContent = window.sfNetError();
            btn.disabled = false;
            btn.textContent = 'שליחת קישור איפוס';
        });
    });
})();


/* Locking against double submission on the sign-in and signup forms.
 *
 * These were the only forms in the app without a lock: everywhere else the
 * button is locked (settings.js, onboarding.js, reset-password.js). In the app
 * installed on a phone there isn't even a browser spinner, so after a tap the
 * screen just doesn't respond while the server works, and the user taps again.
 * The sign-in log shows it happens in practice: almost every sign-in is
 * recorded twice, a second apart. The second tap also burns into the quota of
 * 10 a minute, and whoever crossed it landed on an error page with no way out.
 */
(function () {
    /* Signup: every problem under its own field, while typing (Matan, 13.10).
     *
     * Until now the server returned one error at a time, at the top, and both
     * password fields were emptied: someone who got three fields wrong submitted
     * four times and typed the password four times. The rules are identical to
     * the server's (`_EMAIL_RE`, `_PHONE_RE` in app.py), which stays the final
     * check. A field starts being checked only after it was left, so it doesn't
     * shout "invalid" in the middle of first typing; from then on, on every
     * keystroke, so the fix shows immediately. */
    const EMAIL_RE = /^[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;
    const PHONE_RE = /^0(5\d|7\d|[2-4689])\d{7}$/;

    function phoneDigits(raw) {
        let d = (raw || '').replace(/\D/g, '');
        if (d.indexOf('972') === 0) d = '0' + d.slice(3);
        return d;
    }

    const RULES = {
        first_name: function (v) { return v.trim() ? '' : 'נא למלא שם פרטי'; },
        last_name:  function (v) { return v.trim() ? '' : 'נא למלא שם משפחה'; },
        signup_email: function (v) {
            v = v.trim();
            if (!v) return 'נא למלא מייל';
            if (v.indexOf('@') < 0) return 'חסר @ במייל';
            if (!/@[^@]+\.[^@.]+$/.test(v)) return 'המייל חסר סיומת — למשל ‎@gmail.com‎';
            return EMAIL_RE.test(v) ? '' : 'כתובת המייל אינה תקינה';
        },
        phone: function (v) {
            if (!v.trim()) return 'נא למלא טלפון';
            return PHONE_RE.test(phoneDigits(v)) ? '' : 'מספר הטלפון אינו תקין — למשל 050-1234567';
        },
        signup_password: function (v) {
            if (!v) return 'נא לבחור סיסמה';
            if (v.length < 8) return 8 - v.length === 1 ? 'עוד תו אחד' : 'עוד ' + (8 - v.length) + ' תווים';
            return sfPasswordProblem(v);
        },
    };

    function signupChecks(form) {
        if (!form) return;
        const touched = {};

        function check(id) {
            const input = document.getElementById(id);
            const out = document.getElementById(id + '_error');
            if (!input || !out) return true;
            const msg = RULES[id](input.value);
            out.textContent = msg;
            out.hidden = !msg;
            input.setAttribute('aria-invalid', msg ? 'true' : 'false');
            // The error, and the field's own hint if it has one (the password rule)
            const hint = document.getElementById(id + '_hint');
            const described = [msg ? out.id : null, hint ? hint.id : null].filter(Boolean).join(' ');
            if (described) input.setAttribute('aria-describedby', described);
            else input.removeAttribute('aria-describedby');
            return !msg;
        }

        Object.keys(RULES).forEach(function (id) {
            const input = document.getElementById(id);
            if (!input) return;
            input.addEventListener('blur', function () {
                if (input.value) touched[id] = true;
                if (touched[id]) check(id);
            });
            input.addEventListener('input', function () { if (touched[id]) check(id); });
        });

        form.addEventListener('submit', function (e) {
            let first = null;
            Object.keys(RULES).forEach(function (id) {
                touched[id] = true;
                if (!check(id) && !first) first = document.getElementById(id);
            });
            if (first) {
                e.preventDefault();
                first.focus();
            }
        });
    }

    function guardSubmit(form, busyText) {
        if (!form) return;
        const btn = form.querySelector('button[type="submit"]');
        if (!btn) return;
        const idleText = btn.textContent;

        function release() {
            btn.disabled = false;
            btn.textContent = idleText;
        }

        form.addEventListener('submit', function (e) {
            // The live check stopped the submission: the button isn't locked

            if (e.defaultPrevented || btn.disabled) return;
            btn.disabled = true;
            btn.textContent = busyText;
            // Safety valve: if the submission didn't lead to navigation (network
            // drop), the button works again. A retry beats a dead form you can't
            // get out of.

            setTimeout(release, 12000);
        });

        // Going back to the page with "back" serves it from the browser's
        // memory, and the button would come back locked from last time

        window.addEventListener('pageshow', function (e) { if (e.persisted) release(); });
    }

    signupChecks(document.getElementById('signupPanel'));
    guardSubmit(document.getElementById('loginPanel'),  'מתחברים…');
    guardSubmit(document.getElementById('signupPanel'), 'נרשמים…');
})();
