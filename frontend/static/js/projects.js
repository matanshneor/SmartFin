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
        isPersonal = btn.dataset.personal === 'true';
    });
});

form.addEventListener('submit', function (e) {
    e.preventDefault();
    errorEl.textContent = '';
    const name = document.getElementById('projectName').value.trim();
    const icon = document.getElementById('projectIcon').value.trim();
    const description = document.getElementById('projectDescription').value.trim();
    const budgetVal = document.getElementById('projectBudget').value;
    const trackExpense = document.getElementById('projectTrackExpense').checked;
    const trackIncome  = document.getElementById('projectTrackIncome').checked;
    const trackSavings = document.getElementById('projectTrackSavings').checked;
    if (!name) { errorEl.textContent = 'נא להזין שם לפרויקט'; return; }
    if (!trackExpense && !trackIncome && !trackSavings) {
        errorEl.textContent = 'יש לבחור לפחות מחלקה אחת למעקב';
        return;
    }

    const submitBtn = form.querySelector('button[type="submit"]');
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
