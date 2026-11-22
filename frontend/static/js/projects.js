/* The "new project" screen (`/projects/new`): its own screen since 5.10, not a
 * form opening inside the project list. After creating, straight into the new
 * project. */
(function () {
const form      = document.getElementById('newProjectForm');
const errorEl   = document.getElementById('newProjectError');
const ownerSharedBtn   = document.getElementById('projectOwnerShared');
const ownerPersonalBtn = document.getElementById('projectOwnerPersonal');
let isPersonal = false;

[ownerSharedBtn, ownerPersonalBtn].forEach(function (btn) {
    btn.addEventListener('click', function () {
        ownerSharedBtn.classList.toggle('active', btn === ownerSharedBtn);
        ownerPersonalBtn.classList.toggle('active', btn === ownerPersonalBtn);
        ownerSharedBtn.setAttribute('aria-pressed', btn === ownerSharedBtn ? 'true' : 'false');
        ownerPersonalBtn.setAttribute('aria-pressed', btn === ownerPersonalBtn ? 'true' : 'false');
        isPersonal = btn.dataset.personal === 'true';
    });
});

// The name takes the focus on a computer only (Matan, 23.11). It had
// `autofocus`: on a phone the field got the focus but no keyboard came, and
// since the bottom menu hides while a field has focus, the page opened with an
// empty strip where the menu should be.
if (!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches)) {
    document.getElementById('projectName').focus();
}

// The kind of project (Matan, 22.11): one of two cards. It sets what belongs
// to the project (the three hidden checkboxes) and what the amount is called.
let kind = 'expense';
const kindCards = document.querySelectorAll('#projectKind .kind-card');
function setKind(next) {
    kind = next;
    kindCards.forEach(function (c) { c.setAttribute('aria-checked', c.dataset.kind === kind ? 'true' : 'false'); });
    document.getElementById('projectTrackExpense').checked = kind === 'expense';
    document.getElementById('projectTrackIncome').checked  = kind === 'expense';
    document.getElementById('projectTrackSavings').checked = kind === 'savings';
    document.getElementById('projectBudgetLabel').textContent =
        kind === 'savings' ? 'יעד חיסכון (אופציונלי)' : 'תקציב (אופציונלי)';
    const name = document.getElementById('projectName');
    name.placeholder = kind === 'savings' ? 'לדוגמה: קרן לרכב' : 'לדוגמה: טיול ליפן';
}
kindCards.forEach(function (c) { c.addEventListener('click', function () { setKind(c.dataset.kind); }); });

form.addEventListener('submit', function (e) {
    e.preventDefault();
    errorEl.textContent = '';
    const name = document.getElementById('projectName').value.trim();
    const icon = document.getElementById('projectIcon').value.trim();
    const description = document.getElementById('projectDescription').value.trim();
    const budgetVal = document.getElementById('projectBudget').value.replace(/,/g, '').trim();
    const trackExpense = document.getElementById('projectTrackExpense').checked;
    const trackIncome  = document.getElementById('projectTrackIncome').checked;
    const trackSavings = document.getElementById('projectTrackSavings').checked;
    if (!name) { errorEl.textContent = 'נא להזין שם לפרויקט'; return; }
    if (!trackExpense && !trackIncome && !trackSavings) {
        errorEl.textContent = 'נא לבחור לפחות מחלקה אחת למעקב';
        return;
    }

    const submitBtn = document.querySelector('button[form="newProjectForm"]');   // in the bar, outside the form
    submitBtn.disabled = true;
    fetch('/api/projects', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
            name: name,
            icon: icon,
            description: description,
            budget_target: budgetVal ? parseFloat(budgetVal) : null,
            is_personal: isPersonal,
            kind: kind,
            track_expense: trackExpense,
            track_income: trackIncome,
            track_savings: trackSavings,
        }),
    })
    .then(r => r.json())
    .then(function (data) {
        if (data.error) { submitBtn.disabled = false; errorEl.textContent = data.error; return; }
        window.location.href = data.id ? '/projects/' + encodeURIComponent(data.id) : '/projects';
    })
    .catch(function () { submitBtn.disabled = false; errorEl.textContent = window.sfNetError(); });
});

// Deletion moved into the project (project-edit.js).
//
// It sat here as a ✕ next to every row in the list: the most irreversible
// action on the page, one tap away from the name and with no context about what
// is about to be deleted. Someone **inside** the project has already seen the
// amounts and the transactions.

})();
