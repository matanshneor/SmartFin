/* The projects list that loads its own data (`projects_view.html`, the
 * frontend/backend split).
 *
 * The page arrives with no project in it. This file asks
 * `/api/screens/projects` and builds the hero and the tiles from the page's
 * `<template>`s. The sentences arrive ready from the server ("יצאו ₪4,200 מתוך
 * ₪10,000"): what an amount means follows the project's kind, and that is
 * decided in one place.
 *
 * Three moments ask for the data:
 *   · the page opens;
 *   · the user arrives from another tab without a full load: the empty page
 *     was swapped in, and `sf:refreshed` says so;
 *   · something was saved (`softReload`): asked again and redrawn in place,
 *     with what is on screen staying until the new answer is here.
 *
 * Everything is written with `textContent`: names and descriptions are the
 * user's own text. A failed first load says so and offers another try; it
 * never shows the "no projects yet" invitation (see `DataUnavailable`). */
(function () {
const VIEW = 'projectsView';
const byId = id => document.getElementById(id);
const slot = (root, name) => root.querySelector('[data-slot="' + name + '"]');
const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
};
// A piece the project doesn't have leaves the tile, as on the server-built page
function put(el, text) {
    if (text == null) el.remove(); else el.textContent = text;
}

function tile(p) {
    const node = byId('tplProjectTile').content.cloneNode(true), box = node.querySelector('.project-tile');
    box.classList.add('tint-' + p.tint);
    if (p.over) box.classList.add('is-over');
    box.dataset.id = p.id;
    slot(node, 'icon').textContent = p.icon;
    const name = slot(node, 'name');
    name.href = p.href;
    name.textContent = p.name;
    const pct = slot(node, 'pct');
    if (p.over) pct.classList.add('is-over');
    put(pct, p.pct == null ? null : p.pct + '%');
    put(slot(node, 'description'), p.description);

    const numbers = slot(node, 'numbers');
    if (p.numbers) numbers.append(p.numbers.lead + ' ', make('b', '', p.numbers.amount), ' ' + p.numbers.tail);
    else numbers.remove();
    const bar = slot(node, 'bar');
    if (p.bar) {
        if (p.bar.kind) bar.firstElementChild.className = p.bar.kind;
        bar.firstElementChild.style.width = p.bar.width + '%';
    } else bar.remove();

    const tags = slot(node, 'tags');
    if (p.over_tag) tags.appendChild(make('span', 'project-tile-tag is-over', p.over_tag));
    if (p.personal) tags.appendChild(byId('tplPersonalTag').content.cloneNode(true));
    if (p.savings) tags.appendChild(make('span', 'project-tile-tag', 'חיסכון'));
    if (p.last) tags.appendChild(make('span', 'project-tile-last', p.last));

    const add = slot(node, 'add');
    add.dataset.addToProject = p.id;
    add.setAttribute('aria-label', p.add_label);
    return node;
}

function fill(view) {
    const hero = view.hero;
    byId('newProjectBtn').hidden = !hero.new_button;
    const summary = byId('projectsSummary');
    summary.hidden = !hero.summary;
    if (hero.summary) {
        byId('projectsSummaryName').textContent = hero.summary.name;
        const main = byId('projectsSummaryMain');
        main.textContent = hero.summary.main;
        main.classList.toggle('is-over', hero.summary.over);
        const sub = byId('projectsSummarySub');
        sub.textContent = hero.summary.sub || '';
        sub.hidden = !hero.summary.sub;
    }
    const count = byId('projectsCount');
    count.textContent = hero.count || '';
    count.hidden = !hero.count;

    byId('sfPageProject').dataset.projectId = view.single_id || '';
    const list = byId('projectsList');
    if (view.tiles.length) {
        list.replaceChildren.apply(list, view.tiles.map(tile));
    } else {
        const none = byId('tplNoProjects').content.cloneNode(true);
        slot(none, 'title').textContent = view.finished.length ? 'פרויקט חדש' : 'הפרויקט הראשון שלכם';
        list.replaceChildren(none);
    }
    byId('activeProjects').hidden = false;

    byId('finishedList').replaceChildren.apply(byId('finishedList'), view.finished.map(function (p) {
        const row = byId('tplFinishedRow').content.cloneNode(true);
        row.querySelector('a').href = p.href;
        slot(row, 'icon').textContent = p.icon;
        slot(row, 'name').append(' ' + p.name);
        put(slot(row, 'sum'), p.sum);
        return row;
    }));
    byId('finishedProjects').hidden = !view.finished.length;
}

function ask(holder) {
    return fetch(holder.dataset.viewUrl, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then(function (r) {
            // 401: `auth-guard.js` is already sending the user to sign in
            if (r.status === 401) return null;
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
}

/* `opening`: the data arrived while the page was opening, so this is its
 * entrance (`motion.js` brings the tiles in). Otherwise it is what the
 * server-built page got after a swap: `sf:refreshed`. */
function show(holder, view, opening) {
    fill(view);
    holder.dataset.filled = '1';
    holder.removeAttribute('aria-busy');
    window.dispatchEvent(opening ? new CustomEvent('sf:view-entered', { detail: { root: holder } })
                                 : new CustomEvent('sf:refreshed'));
}

function load(opening) {
    const holder = byId(VIEW);
    if (!holder || holder.dataset.filled || holder.dataset.loading) return;
    holder.dataset.loading = '1';
    byId('projectsError').hidden = true;
    ask(holder)
        .then(function (view) { if (view && byId(VIEW) === holder) show(holder, view, opening); })
        .catch(function () {
            if (byId(VIEW) !== holder) return;
            byId('projectsErrorText').textContent = window.sfNetError ? window.sfNetError() : 'נסו שוב';
            byId('projectsError').hidden = false;
            holder.removeAttribute('aria-busy');
        })
        .finally(function () { delete holder.dataset.loading; });
}

// After a save (`softReload` in core.js): the same page, new numbers. A failure
// here is the caller's to handle: it falls back to a full load.
window.sfViews = window.sfViews || {};
window.sfViews[VIEW] = function () {
    const holder = byId(VIEW);
    if (!holder) return Promise.reject(new Error('no view'));
    return ask(holder).then(function (view) { if (view && byId(VIEW) === holder) show(holder, view, false); });
};

document.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('#projectsRetry')) load(false);
});
// The empty page was swapped in (a tab switch): fill it. One already filled
// raised this event itself, and is left alone.
window.addEventListener('sf:refreshed', function () { load(false); });
load(document.readyState === 'loading');
})();
