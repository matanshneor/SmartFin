(function () {
    // The token arrives from Supabase's email in the fragment:
    // #access_token=...&type=recovery

    const params      = new URLSearchParams(window.location.hash.slice(1));
    const accessToken = params.get('access_token');
    const form        = document.getElementById('resetForm');
    const errorBox    = document.getElementById('resetError');

    if (!accessToken) {
        document.getElementById('tokenError').style.display = 'block';
        form.style.display = 'none';
        return;
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        errorBox.textContent = '';

        const password = document.getElementById('newPassword').value;
        const confirm  = document.getElementById('passwordConfirm').value;

        if (password.length < 8) {
            errorBox.textContent = 'הסיסמה חייבת להכיל לפחות 8 תווים';
            return;
        }
        if (password !== confirm) {
            errorBox.textContent = 'הסיסמאות אינן תואמות';
            return;
        }

        const btn = document.getElementById('resetBtn');
        btn.disabled = true;
        btn.textContent = 'מעדכן…';

        fetch('/api/auth/reset', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                access_token: accessToken,
                password: password,
                password_confirm: confirm,
            }),
        })
        .then(r => r.json())
        .then(function (d) {
            if (d.error) {
                errorBox.textContent = d.error;
                btn.disabled = false;
                btn.textContent = 'עדכון הסיסמה';
                return;
            }
            window.location.href = '/login?reset=1';
        })
        .catch(function () {
            errorBox.textContent = window.sfNetError();
            btn.disabled = false;
            btn.textContent = 'עדכון הסיסמה';
        });
    });
})();
