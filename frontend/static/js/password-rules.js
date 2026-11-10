/* The rules for a new password (Matan, 10.11.2026): at least 8 characters, with
 * at least one letter (Hebrew or English) and at least one digit.
 *
 * The same rules and messages as backend/passwords.py. The server is the one that
 * enforces them; this only says it before the round trip. Loaded on the three
 * pages where a password is chosen: signup, reset, and settings. */
(function () {
    'use strict';
    window.sfPasswordProblem = function (password) {
        if (password.length < 8) return 'הסיסמה חייבת להכיל לפחות 8 תווים';
        if (!/\p{L}/u.test(password)) return 'הסיסמה חייבת להכיל לפחות אות אחת';
        if (!/[0-9]/.test(password)) return 'הסיסמה חייבת להכיל לפחות ספרה אחת';
        return '';
    };
})();
