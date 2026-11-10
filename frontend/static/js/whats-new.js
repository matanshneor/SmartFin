/* "What's new" after an update, and "Or is in the family!" (Matan, 12.10).
 *
 * What's new: a small sheet, once per version, on the first visit to home
 * after the update. The lines are written here, on every update users will
 * notice, and `VERSION` is bumped. A brand-new family doesn't see it (it has no
 * "before"); it is only recorded as seen.
 *
 * A family member joined: a card on home, once, for whoever was already in the
 * family. The first time on this phone we only remember who is in the family,
 * with no card; otherwise everyone is "new". */
(function () {
    'use strict';
    const VERSION = '2026-10-13';
    const NEWS = [
        ['🔓', 'פותחים את האפליקציה ונכנסים ישר — בלי להתחבר שוב'],
        ['🎨', 'כל קטגוריה בעיגול ההוצאות בצבע משלה'],
    ];

    function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* no storage */ } }

    const page = document.getElementById('sfHomeInfo');
    if (!page) return;

    function sheet(title, lines, button) {
        const box = document.createElement('div');
        box.className = 'color-sheet whats-new';
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-modal', 'true');
        box.setAttribute('aria-label', title);
        const card = document.createElement('div');
        card.className = 'color-sheet-card';
        const h = document.createElement('p');
        h.className = 'color-sheet-title';
        h.textContent = title;
        card.appendChild(h);
        const ul = document.createElement('ul');
        ul.className = 'whats-new-list';
        lines.forEach(function (l) {
            const li = document.createElement('li');
            const icon = document.createElement('span');
            icon.className = 'whats-new-icon';
            icon.textContent = l[0];
            const text = document.createElement('span');
            text.textContent = l[1];
            li.appendChild(icon);
            li.appendChild(text);
            ul.appendChild(li);
        });
        card.appendChild(ul);
        const ok = document.createElement('button');
        ok.type = 'button';
        ok.className = 'submit-btn';
        ok.textContent = button;
        card.appendChild(ok);
        box.appendChild(card);
        document.body.appendChild(box);
        const close = function () { box.remove(); };
        ok.addEventListener('click', close);
        box.addEventListener('click', function (e) { if (e.target === box) close(); });
        requestAnimationFrame(function () { ok.focus(); });
    }

    // ── What's new ──

    const seen = get('sf_whats_new');
    if (page.dataset.newFamily === 'true') set('sf_whats_new', VERSION);
    else if (seen !== VERSION) {
        set('sf_whats_new', VERSION);
        sheet('מה חדש ב-SmartFin', NEWS, 'יופי, הבנתי');
    }

    // ── A family member joined ──

    let members = [];
    try { members = JSON.parse(page.dataset.members || '[]'); } catch (e) { members = []; }
    const key = 'sf_known_members_' + (page.dataset.family || '');
    const known = get(key);
    const ids = members.map(function (m) { return m.id; });
    set(key, JSON.stringify(ids));
    if (known === null) return;                      // first time on this phone
    let before = [];
    try { before = JSON.parse(known) || []; } catch (e) { before = []; }
    const joined = members.filter(function (m) { return m.id !== page.dataset.me && before.indexOf(m.id) === -1; });
    const slot = document.getElementById('memberJoined');
    if (!joined.length || !slot) return;
    const m = joined[0];
    slot.querySelector('.member-joined-text').textContent =
        '🎉 ' + m.name + ' במשפחה! מעכשיו גם ההוצאות של ' + m.name + ' כאן';
    if (m.color != null) slot.classList.add('owner-' + m.color);
    slot.hidden = false;
    slot.querySelector('.member-joined-close').addEventListener('click', function () { slot.hidden = true; });
})();
