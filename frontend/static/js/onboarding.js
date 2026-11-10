// A standalone page (doesn't inherit from base.html): a local escapeHtml

window.escapeHtml = window.escapeHtml || function (s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
};
(function () {
    const errorBox = document.getElementById('onboardingError');
    const steps    = { 1: document.getElementById('step1'), 2: document.getElementById('step2'),
                       3: document.getElementById('step3') };
    const dots     = { 1: document.getElementById('dotStep1'), 2: document.getElementById('dotStep2'),
                       3: document.getElementById('dotStep3') };
    // The invite screen isn't a wizard step but what is shown after it; see the
    // note in the template: an invite code before the family is set up sent
    // people back to the wizard.

    const stepInvite = document.getElementById('stepInvite');

    const step0    = document.getElementById('step0');
    const stepJoin = document.getElementById('stepJoin');
    const dotsBar  = document.getElementById('onboardingDots');

    // The waiting screen, and its refresh button. It must be bound **before**
    // the early return below: it is the only element on this screen, and
    // without this it is dead: the CSP blocks `onclick`, and there is nothing
    // else on the screen to tap.

    const waitingRefreshBtn = document.getElementById('waitingRefreshBtn');
    if (waitingRefreshBtn) {
        waitingRefreshBtn.addEventListener('click', function () {
            window.location.reload();
        });
    }

    // The "joined successfully" screen is served instead of the whole wizard,
    // and has none of the elements below. Without this return the file falls
    // over on the first missing one, and everything after it with it, exactly
    // as happened in core.js.

    if (!step0) return;

    /* Moving between screens: the next one slides in from the side, by reading
     * direction (Matan, 7.10, motion round, item 4). In Hebrew "forward" is to
     * the left: the next comes from the left, and back from the right. Before,
     * the screen switched all at once, and it wasn't clear whether you moved
     * forward or back. `here`: where we are in the order: choice 0, join 1,
     * steps 1–3, invite 4. `animate` and not CSS: the general reduced-motion
     * rule doesn't apply to it, hence the check here. */
    let here = 0;
    const calm = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    function enter(el, at) {
        const dir = at >= here ? 1 : -1;
        here = at;
        if (calm || !el || !el.animate) return;
        el.animate([
            { opacity: 0, transform: 'translateX(' + (dir * -24) + 'px)' },
            { opacity: 1, transform: 'none' },
        ], { duration: 300, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' });
    }

    function goToStep(n) {
        errorBox.textContent = '';
        step0.style.display    = 'none';
        stepJoin.style.display = 'none';
        dotsBar.style.display  = 'flex';
        stepInvite.style.display = 'none';
        [1, 2, 3].forEach(function (i) {
            steps[i].style.display = i === n ? 'block' : 'none';
            dots[i].classList.toggle('active', i <= n);
        });
        enter(steps[n], n);
    }

    function showChoice(which) {
        errorBox.textContent = '';
        [1, 2, 3].forEach(function (i) { steps[i].style.display = 'none'; });
        stepInvite.style.display = 'none';
        dotsBar.style.display  = 'none';
        step0.style.display    = which === 'start' ? 'block' : 'none';
        stepJoin.style.display = which === 'join'  ? 'block' : 'none';
        enter(which === 'join' ? stepJoin : step0, which === 'join' ? 1 : 0);
    }

    // ─── The fork: a new family or joining ──────────────────────────────

    document.getElementById('chooseCreateBtn').addEventListener('click', () => goToStep(1));
    document.getElementById('chooseJoinBtn').addEventListener('click', function () {
        showChoice('join');
        document.getElementById('joinCode').focus();
    });
    document.getElementById('backFromJoinBtn').addEventListener('click', () => showChoice('start'));
    document.getElementById('backToChoiceBtn').addEventListener('click', () => showChoice('start'));

    const joinInput   = document.getElementById('joinCode');
    const joinPreview = document.getElementById('joinPreview');
    let previewTimer  = null;

    // A live preview: the user sees which family they are about to join before
    // confirming, so a wrong code is caught here and not after the fact.

    joinInput.addEventListener('input', function () {
        const code = joinInput.value.trim();
        joinPreview.textContent = '';
        joinPreview.classList.remove('field-hint-ok', 'field-hint-bad');
        clearTimeout(previewTimer);
        if (code.replace(/[^A-Za-z0-9]/g, '').length < 6) return;

        previewTimer = setTimeout(function () {
            fetch('/api/family/preview?code=' + encodeURIComponent(code))
                .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
                .then(function (res) {
                    if (res.ok && res.data.found) {
                        joinPreview.textContent = 'מצטרפים ל: ' + res.data.name;
                        joinPreview.classList.add('field-hint-ok');
                    } else {
                        joinPreview.textContent = 'הקוד לא נמצא או שפג תוקפו — בקשו ממנהל המשפחה קוד חדש';
                        joinPreview.classList.add('field-hint-bad');
                    }
                })
                .catch(function () { /* the preview is only a convenience: silent on failure */ });
        }, 350);
    });

    document.getElementById('joinFamilyBtn').addEventListener('click', function () {
        const code = joinInput.value.trim();
        if (!code) {
            errorBox.textContent = 'נא להזין את קוד ההזמנה';
            return;
        }
        const btn = this;
        btn.disabled = true;
        btn.textContent = 'מצטרפים…';

        fetch('/api/family/join', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ code: code, confirm: true }),
        })
        .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
        .then(function (res) {
            if (!res.ok) {
                errorBox.textContent = res.data.error || 'ההצטרפות נכשלה';
                btn.disabled = false;
                btn.textContent = 'הצטרפות';
                return;
            }
            // A joiner doesn't go through onboarding: the family's categories
            // already exist

            window.location.href = '/';
        })
        .catch(function () {
            errorBox.textContent = window.sfNetError();
            btn.disabled = false;
            btn.textContent = 'הצטרפות';
        });
    });

    document.getElementById('finishOnboardingBtn').addEventListener('click', function () {
        // `welcome=1`: the home page explains the first time how to add a
        // family member

        window.location.href = '/?welcome=1';
    });

    document.getElementById('toStep2Btn').addEventListener('click', function () {
        if (!document.getElementById('familyName').value.trim()) {
            errorBox.textContent = 'נא להזין שם למשפחה';
            return;
        }
        goToStep(2);
    });
    document.getElementById('backTo1Btn').addEventListener('click', () => goToStep(1));
    document.getElementById('toStep3Btn').addEventListener('click', () => goToStep(3));
    document.getElementById('backTo2Btn').addEventListener('click', () => goToStep(2));

    // Copy invite code
    document.getElementById('copyInviteBtn').addEventListener('click', function () {
        const el = document.getElementById('onboardingInviteCode');
        const btn = this;
        const famEl = document.getElementById('familyName');
        const msg = window.sfInviteMessage(el.textContent.trim(),
                                           famEl && famEl.value.trim());
        window.copyToClipboard(msg, el).then(function (copied) {
            if (!copied) return;          // a message was already shown, and the code is marked
            btn.textContent = '✓ הועתקה הזמנה';
            setTimeout(function () { btn.textContent = 'העתקת ההזמנה'; }, 1800);
        });
    });

    // Category type tabs
    const typeTabs = document.querySelectorAll('.onboarding-type-tabs .toggle-btn');
    const panels   = document.querySelectorAll('.onboarding-cat-panel');
    typeTabs.forEach(function (btn) {
        btn.addEventListener('click', function () {
            typeTabs.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const type = btn.dataset.catType;
            panels.forEach(p => { p.style.display = (p.dataset.panelType === type) ? 'block' : 'none'; });
        });
    });

    // "12 of 14 selected" above each grid, so whoever taps to "select" sees
    // right away that the number goes down, and doesn't discover it only after
    // signing up (Matan, 31.10)

    function updateCount(type) {
        const grid = document.querySelector('.onboarding-cat-grid[data-grid-type="' + type + '"]');
        const label = document.querySelector('.onboarding-cat-count[data-count-type="' + type + '"]');
        if (!grid || !label) return;
        const all = grid.querySelectorAll('.onboarding-cat-btn').length;
        const on = grid.querySelectorAll('.onboarding-cat-btn.active').length;
        label.textContent = on === all ? 'כל ' + all + ' הקטגוריות נבחרו'
                          : on === 0 ? 'לא נבחרה אף קטגוריה — לחצו על קטגוריה כדי להחזיר אותה'
                          : 'נבחרו ' + on + ' מתוך ' + all;
        label.classList.toggle('is-empty', on === 0);
    }
    ['expense', 'income', 'savings'].forEach(updateCount);

    // Toggle category chips on/off (multi-select, not radio)
    document.querySelectorAll('.onboarding-cat-grid').forEach(function (grid) {
        grid.addEventListener('click', function (e) {
            const btn = e.target.closest('.onboarding-cat-btn');
            if (!btn) return;
            const on = btn.classList.toggle('active');
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            updateCount(grid.dataset.gridType);
        });
    });

    // Add a custom category chip per type
    document.querySelectorAll('.onboarding-add-btn').forEach(function (addBtn) {
        addBtn.addEventListener('click', function () {
            const type  = addBtn.dataset.addType;
            const panel = document.querySelector(`.onboarding-cat-panel[data-panel-type="${type}"]`);
            const icon  = panel.querySelector('.onboarding-new-icon');
            const name  = panel.querySelector('.onboarding-new-name');
            const nameVal = name.value.trim();
            if (!nameVal) { name.focus(); return; }

            const grid = panel.querySelector('.onboarding-cat-grid');
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'cat-btn onboarding-cat-btn active';
            chip.setAttribute('aria-pressed', 'true');
            chip.dataset.name = nameVal;
            chip.dataset.icon = icon.value.trim() || '🏷';
            chip.dataset.type = type;
            chip.innerHTML = `<span class="cat-emoji">${window.escapeHtml(chip.dataset.icon)}</span><span>${window.escapeHtml(nameVal)}</span>`;
            grid.appendChild(chip);
            updateCount(type);

            name.value = '';
            icon.value = '🏷';
            name.focus();
        });
    });

    // Finish: collect all active chips across all 3 types, submit
    document.getElementById('finishBtn').addEventListener('click', function () {
        const categories = Array.from(document.querySelectorAll('.onboarding-cat-btn.active')).map(btn => ({
            name: btn.dataset.name, icon: btn.dataset.icon, type: btn.dataset.type,
        }));

        if (categories.length === 0) {
            errorBox.textContent = 'נא לבחור לפחות קטגוריה אחת';
            return;
        }

        const btn = this;
        btn.disabled = true;
        btn.textContent = 'שומר…';

        fetch('/api/onboarding/complete', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                family_name: document.getElementById('familyName').value.trim(),
                categories:  categories,
                owner_attribution: {
                    expense: document.getElementById('obAttrExpense').checked,
                    income:  document.getElementById('obAttrIncome').checked,
                    savings: document.getElementById('obAttrSavings').checked,
                },
            }),
        })
        .then(r => r.json())
        .then(function (data) {
            if (data.error) {
                errorBox.textContent = data.error;
                btn.disabled = false;
                btn.textContent = 'סיום והתחלה';
                return;
            }
            // Here, and only here, the family is set up: it has a name,
            // categories and attribution. Now inviting makes sense: the code is
            // shown after finishing, not midway.

            [1, 2, 3].forEach(function (i) { steps[i].style.display = 'none'; });
            dotsBar.style.display = 'none';
            stepInvite.style.display = 'block';
            enter(stepInvite, 4);
        })
        .catch(function () {
            errorBox.textContent = window.sfNetError();
            btn.disabled = false;
            btn.textContent = 'סיום והתחלה';
        });
    });
})();
