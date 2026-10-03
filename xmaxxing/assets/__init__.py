from __future__ import annotations

# The dashboard ships as three string constants rather than files on disk.
# One module, no asset pipeline, nothing to resolve at runtime - and the CSP
# stays strict because every byte is same-origin.

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>xmaxxing</title>
<link rel="stylesheet" href="/app.css">
</head>
<body>
<header>
  <h1>xmaxxing</h1>
  <div class="controls">
    <label>minutes <input id="minutes" type="number" min="1" max="240" value="30"></label>
    <label>source
      <select id="source"><option value="search">search</option><option value="home">home</option></select>
    </label>
    <label class="check"><input id="dryrun" type="checkbox"> dry run</label>
    <label class="check"><input id="noResolve" type="checkbox"> no-resolve</label>
    <label>reply links <input id="replyLinks" type="number" min="0" max="50" value="0"></label>
    <button id="start" class="primary">Start</button>
    <button id="stop" disabled>Stop</button>
  </div>
</header>

<section class="status" aria-live="polite">
  <span id="pill" class="pill idle">idle</span>
  <span id="phase"></span>
  <span id="message"></span>
</section>

<section id="counters" class="counters" aria-live="polite"></section>

<section class="policy">
  <span id="policyMode" class="tag"></span>
  <span id="policyWhy"></span>
  <button id="notifyBtn" class="ghost">Enable notifications</button>
</section>

<nav class="tabs" role="tablist">
  <button class="tab active" data-tab="jobs" role="tab">Jobs <span class="n" id="nJobs">0</span></button>
  <button class="tab" data-tab="hackathons" role="tab">Hackathons <span class="n" id="nHack">0</span></button>
  <button class="tab" data-tab="actions" role="tab">Outreach <span class="n" id="nActions">0</span></button>
  <button class="tab" data-tab="rejected" role="tab">Rejected <span class="n" id="nRejected">0</span></button>
  <button class="tab" data-tab="links" role="tab">Reply links <span class="n" id="nLinks">0</span></button>
</nav>

<div class="filterbar">
  <input id="filter" type="search" placeholder="filter by company, role, text or handle">
  <label class="check"><input id="hideExpired" type="checkbox" checked> hide expired</label>
  <label class="check"><input id="hideResolved" type="checkbox"> hide resolved outreach</label>
</div>

<main id="panel"></main>

<div id="toasts" class="toasts" aria-live="assertive"></div>

<footer>
  <span id="conn" class="tag">connecting...</span>
  <span>local only &middot; nothing leaves this machine</span>
</footer>

<script>window.XMAXXING_TOKEN = "__TOKEN__";</script>
<script src="/app.js"></script>
</body>
</html>
"""

APP_CSS = r"""
:root {
  --bg: #0e1116;
  --panel: #161b22;
  --panel-2: #1c2430;
  --line: #2a3441;
  --ink: #e6edf3;
  --dim: #8b98a5;
  --accent: #4c9aff;
  --good: #3fb950;
  --warn: #d29922;
  --bad: #f85149;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
h1 { font-size: 18px; margin: 0; letter-spacing: .5px; }
header {
  display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
  justify-content: space-between; padding: 12px 18px;
  background: var(--panel); border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 5;
}
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
label { display: inline-flex; align-items: center; gap: 6px; color: var(--dim); font-size: 12px; }
label.check { gap: 4px; }
input[type=number], input[type=search], select {
  background: var(--bg); color: var(--ink); border: 1px solid var(--line);
  border-radius: 6px; padding: 5px 8px; font: inherit; font-size: 13px;
}
input[type=number] { width: 68px; }
input[type=search] { flex: 1; min-width: 180px; }
button {
  background: var(--panel-2); color: var(--ink); border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 14px; font: inherit; font-size: 13px; cursor: pointer;
}
button:hover:not(:disabled) { border-color: var(--accent); }
button:disabled { opacity: .45; cursor: not-allowed; }
button.primary { background: var(--accent); border-color: var(--accent); color: #04101f; font-weight: 600; }
button.ghost { background: transparent; }
button.tiny { padding: 3px 8px; font-size: 12px; }

.status { display: flex; gap: 12px; align-items: center; padding: 10px 18px; color: var(--dim); }
.pill { padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; text-transform: uppercase; }
.pill.idle { background: #22303f; color: var(--dim); }
.pill.running { background: rgba(76,154,255,.16); color: var(--accent); }
.pill.stopping { background: rgba(210,153,34,.16); color: var(--warn); }
.pill.error { background: rgba(248,81,73,.16); color: var(--bad); }

.counters { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 18px 12px; }
.counter { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; min-width: 74px; }
.counter b { display: block; font-size: 18px; }
.counter span { color: var(--dim); font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }

.policy { display: flex; gap: 10px; align-items: center; padding: 0 18px 12px; color: var(--dim); font-size: 12px; }
.tag { background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px; }

.tabs { display: flex; gap: 4px; padding: 0 18px; border-bottom: 1px solid var(--line); }
.tab { border: none; border-bottom: 2px solid transparent; border-radius: 0; background: none; color: var(--dim); }
.tab.active { color: var(--ink); border-bottom-color: var(--accent); }
.tab .n { color: var(--dim); font-size: 11px; margin-left: 4px; }
.filterbar { display: flex; gap: 14px; align-items: center; padding: 12px 18px; }

main { padding: 0 18px 40px; }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 10px;
}
.card.expired { opacity: .62; }
.card.resolved { opacity: .72; border-left: 3px solid var(--good); }
.card.failed { border-left: 3px solid var(--bad); }
.card h2 { font-size: 15px; margin: 0 0 2px; }
.card h2 a { color: var(--ink); text-decoration: none; }
.card h2 a:hover { text-decoration: underline; }
.meta { color: var(--dim); font-size: 12px; display: flex; flex-wrap: wrap; gap: 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin: 7px 0; }
.chip { background: var(--panel-2); border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px; font-size: 11px; color: var(--dim); }
.chip.ok { color: var(--good); border-color: rgba(63,185,80,.4); }
.chip.warn { color: var(--warn); border-color: rgba(210,153,34,.4); }
.chip.bad { color: var(--bad); border-color: rgba(248,81,73,.4); }
.text { white-space: pre-wrap; color: #c3cdd8; font-size: 13px; margin: 8px 0; max-height: 12em; overflow: auto; }
.draft { border-left: 2px solid var(--accent); padding-left: 10px; margin: 8px 0; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
a { color: var(--accent); }
.empty { color: var(--dim); padding: 30px 0; text-align: center; }

.toasts { position: fixed; right: 16px; bottom: 16px; display: flex; flex-direction: column; gap: 8px; z-index: 20; }
.toast {
  background: var(--panel-2); border: 1px solid var(--line); border-left: 3px solid var(--accent);
  border-radius: 8px; padding: 9px 13px; max-width: 380px; box-shadow: 0 6px 20px rgba(0,0,0,.4);
}
.toast.bad { border-left-color: var(--bad); }
.toast.good { border-left-color: var(--good); }

footer {
  display: flex; gap: 12px; align-items: center; padding: 12px 18px;
  border-top: 1px solid var(--line); color: var(--dim); font-size: 12px;
}
footer .tag.ok { color: var(--good); border-color: rgba(63,185,80,.4); }
footer .tag.bad { color: var(--bad); border-color: rgba(248,81,73,.4); }
"""

APP_JS = r"""
'use strict';
const TOKEN = window.XMAXXING_TOKEN || new URLSearchParams(location.search).get('t') || '';
const $ = (id) => document.getElementById(id);
let SNAP = { run: { status: 'idle', counts: {} }, policy: {}, jobs: [], hackathons: [], actions: [], rejected: [], reply_links: [] };
let TAB = 'jobs';
const seenActionKeys = new Set();

async function api(path, body) {
  const opts = {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Xmaxxing-Token': TOKEN },
  };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(data.error || data.message || ('HTTP ' + res.status));
  return data;
}

// Everything below renders scraped text, which is untrusted input.
// textContent throughout - never innerHTML with server data - and hrefs are
// scheme-checked, so a post containing markup or a javascript: URL can't execute.
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function safeLink(url, label) {
  let parsed = null;
  try { parsed = new URL(url); } catch (_) { return null; }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
  const a = el('a', null, label || parsed.hostname);
  a.href = parsed.href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  return a;
}
function chip(text, cls) { return el('span', 'chip' + (cls ? ' ' + cls : ''), text); }

function matches(row, needle) {
  if (!needle) return true;
  const hay = [row.company, row.handle, row.display_name, row.role_hit,
               (row.roles || []).join(' '), (row.locations || []).join(' '),
               (row.comp || []).join(' '), row.text, row.reason]
              .filter(Boolean).join(' ').toLowerCase();
  return hay.includes(needle);
}

function postingCard(row) {
  const card = el('div', 'card' + (row.expired ? ' expired' : ''));
  const h = el('h2');
  const title = row.company || row.handle || 'post';
  const label = title + (row.roles && row.roles.length ? ' - ' + row.roles[0] : '');
  if (row.tweet_url && safeLink(row.tweet_url, label)) h.appendChild(safeLink(row.tweet_url, label));
  else h.textContent = title;
  card.appendChild(h);

  const meta = el('div', 'meta');
  meta.appendChild(el('span', null, '@' + (row.handle || '?')));
  if (row.posted_at || row.relative_time) meta.appendChild(el('span', null, row.posted_at || row.relative_time));
  if (row.scraped_at) meta.appendChild(el('span', null, 'found ' + row.scraped_at));
  card.appendChild(meta);

  const chips = el('div', 'chips');
  (row.roles || []).forEach((r) => chips.appendChild(chip(r)));
  (row.locations || []).forEach((l) => chips.appendChild(chip(l)));
  (row.comp || []).forEach((c) => chips.appendChild(chip(c, 'ok')));
  if (row.eligibility) chips.appendChild(chip(row.eligibility, 'warn'));
  if (row.deadline_date) chips.appendChild(chip('deadline ' + row.deadline_date + (row.expired ? ' (passed)' : ''), row.expired ? 'bad' : ''));
  if (row.score !== undefined && row.score !== null) chips.appendChild(chip('score ' + row.score));
  (row.signals || []).forEach((s) => chips.appendChild(chip(s)));
  if (chips.childNodes.length) card.appendChild(chips);

  (row.apply_urls || []).forEach((u) => {
    const a = safeLink(u, 'apply link');
    if (a) { const r = el('div', 'row'); r.appendChild(a); card.appendChild(r); }
  });
  if (row.text) card.appendChild(el('div', 'text', row.text));
  return card;
}

function actionCard(row) {
  const resolved = row.status && row.status !== 'pending';
  const card = el('div', 'card' + (resolved ? ' resolved' : '') + (row.status === 'failed' ? ' failed' : ''));
  card.id = 'action-' + String(row.key).replace(/[^a-z0-9]/gi, '-');

  const h = el('h2');
  const title = (row.company || row.handle || 'post') + (row.roles && row.roles.length ? ' - ' + row.roles[0] : '');
  const link = safeLink(row.tweet_url, title);
  if (link) h.appendChild(link); else h.textContent = title;
  card.appendChild(h);

  const chips = el('div', 'chips');
  chips.appendChild(chip(row.action || 'reply', row.action === 'dm' ? 'warn' : 'ok'));
  chips.appendChild(chip(row.status || 'pending', resolved ? 'ok' : ''));
  if (row.score !== undefined) chips.appendChild(chip('score ' + row.score));
  if (row.resolved_at) chips.appendChild(chip('resolved ' + row.resolved_at));
  if (row.note) chips.appendChild(chip(row.note, 'bad'));
  card.appendChild(chips);

  if (row.draft) card.appendChild(el('div', 'text draft', row.draft));
  if (row.text) card.appendChild(el('div', 'text', row.text));

  const controls = el('div', 'row');
  const open = safeLink(row.tweet_url, 'open in X');
  if (open) controls.appendChild(open);
  if (!resolved) {
    [['sent', 'Mark sent'], ['skipped', 'Skip'], ['dismissed', 'Dismiss']].forEach(function (pair) {
      const b = el('button', 'tiny', pair[1]);
      b.addEventListener('click', async function () {
        b.disabled = true;
        try { await api('/api/actions/resolve', { key: row.key, status: pair[0] }); await refresh(); }
        catch (e) { toast(e.message, 'bad'); b.disabled = false; }
      });
      controls.appendChild(b);
    });
  }
  card.appendChild(controls);
  return card;
}

function rejectedCard(row) {
  const card = el('div', 'card');
  const h = el('h2');
  const link = safeLink(row.tweet_url, '@' + (row.handle || '?'));
  if (link) h.appendChild(link); else h.textContent = '@' + (row.handle || '?');
  card.appendChild(h);
  const chips = el('div', 'chips');
  chips.appendChild(chip(row.reason || 'rejected', 'bad'));
  if (row.score !== undefined) chips.appendChild(chip('score ' + row.score));
  (row.signals || []).forEach((s) => chips.appendChild(chip(s)));
  card.appendChild(chips);
  if (row.snippet) card.appendChild(el('div', 'text', row.snippet));
  return card;
}

function linkCard(row) {
  const card = el('div', 'card');
  card.appendChild(el('h2', null, '@' + (row.handle || '?')));
  (row.urls || []).forEach(function (u) {
    const a = safeLink(u, u);
    if (a) { const r = el('div', 'row'); r.appendChild(a); card.appendChild(r); }
  });
  return card;
}

function render() {
  $('nJobs').textContent = SNAP.jobs.length;
  $('nHack').textContent = SNAP.hackathons.length;
  $('nActions').textContent = SNAP.actions.filter(function (a) { return (a.status || 'pending') === 'pending'; }).length;
  $('nRejected').textContent = SNAP.rejected.length;
  $('nLinks').textContent = SNAP.reply_links.length;

  const run = SNAP.run || {};
  const pill = $('pill');
  pill.textContent = run.status || 'idle';
  pill.className = 'pill ' + (run.status || 'idle');
  $('phase').textContent = run.phase ? 'phase: ' + run.phase : '';
  $('message').textContent = run.error || run.message || '';

  const counters = $('counters');
  counters.replaceChildren();
  const counts = run.counts || {};
  ['scanned', 'kept', 'postings', 'rejected', 'duplicates', 'collapsed', 'promoted'].forEach(function (key) {
    if (counts[key] === undefined) return;
    const c = el('div', 'counter');
    c.appendChild(el('b', null, counts[key]));
    c.appendChild(el('span', null, key));
    counters.appendChild(c);
  });
  const send = run.send || {};
  if (send.attempted || send.sent) {
    const c = el('div', 'counter');
    c.appendChild(el('b', null, send.sent || 0));
    c.appendChild(el('span', null, 'sent'));
    counters.appendChild(c);
  }

  const policy = SNAP.policy || {};
  $('policyMode').textContent = 'mode: ' + (policy.mode || 'review');
  $('policyWhy').textContent = policy.would_send
    ? 'auto-send active' + (policy.max_sends ? ' (cap ' + policy.max_sends + '/run)' : '')
    : 'not sending: ' + (policy.reason || 'review mode') + ' | resolved ' + (policy.resolved_before || 0);

  $('start').disabled = !!SNAP.busy;
  $('stop').disabled = !SNAP.busy;

  const needle = ($('filter').value || '').trim().toLowerCase();
  const hideExpired = $('hideExpired').checked;
  const hideResolved = $('hideResolved').checked;
  const panel = $('panel');
  panel.replaceChildren();

  let rows = [];
  let builder = postingCard;
  if (TAB === 'jobs') rows = SNAP.jobs.slice().reverse();
  else if (TAB === 'hackathons') rows = SNAP.hackathons.slice().reverse();
  else if (TAB === 'actions') { rows = SNAP.actions.slice().reverse(); builder = actionCard; }
  else if (TAB === 'rejected') { rows = SNAP.rejected.slice().reverse(); builder = rejectedCard; }
  else { rows = SNAP.reply_links.slice().reverse(); builder = linkCard; }

  rows = rows.filter(function (r) {
    if (!matches(r, needle)) return false;
    if (hideExpired && r.expired) return false;
    if (hideResolved && TAB === 'actions' && r.status && r.status !== 'pending') return false;
    return true;
  });

  if (!rows.length) {
    panel.appendChild(el('div', 'empty', SNAP.busy ? 'nothing yet - the run is still going' : 'nothing here yet. press start.'));
    return;
  }
  const frag = document.createDocumentFragment();
  rows.forEach(function (r) { frag.appendChild(builder(r)); });
  panel.appendChild(frag);
}

function toast(message, kind) {
  const node = el('div', 'toast' + (kind ? ' ' + kind : ''), message);
  $('toasts').appendChild(node);
  setTimeout(function () { node.remove(); }, 6000);
}

// http://localhost is a secure context, so this needs no HTTPS. It still needs
// permission, and it only surfaces when the tab is backgrounded - the in-page
// toast above is the fallback either way.
function notify(title, body, key) {
  toast(title + (body ? ' - ' + body : ''), 'good');
  try {
    if (window.Notification && Notification.permission === 'granted') {
      const n = new Notification(title, { body: body || '', tag: key || undefined });
      n.onclick = function () { window.focus(); if (key) focusAction(key); n.close(); };
    }
  } catch (_) { /* no Notification API; the toast already showed it */ }
}

function focusAction(key) {
  document.querySelector('.tab[data-tab="actions"]').click();
  const node = document.getElementById('action-' + String(key).replace(/[^a-z0-9]/gi, '-'));
  if (node) { node.scrollIntoView({ block: 'center' }); node.style.outline = '2px solid var(--accent)'; }
}

function connect() {
  const es = new EventSource('/api/events?t=' + encodeURIComponent(TOKEN));
  const conn = $('conn');
  es.onopen = function () { conn.textContent = 'live'; conn.className = 'tag ok'; };
  es.onerror = function () { conn.textContent = 'reconnecting...'; conn.className = 'tag bad'; };
  es.onmessage = function (evt) {
    let e = {};
    try { e = JSON.parse(evt.data); } catch (_) { return; }
    if (e.type === 'action') {
      const key = e.action && e.action.key;
      if (key && !seenActionKeys.has(key)) {
        seenActionKeys.add(key);
        notify('New outreach item',
          (e.action.company || e.action.handle || '') + ' - ' + ((e.action.roles || [])[0] || e.action.action || ''), key);
      }
      refresh();
    } else if (e.type === 'send_result') {
      toast('reply ' + e.status + (e.detail ? ': ' + e.detail : ''), e.status === 'sent' ? 'good' : 'bad');
      refresh();
    } else if (e.type === 'send_policy') {
      if (!e.allowed) $('policyWhy').textContent = 'not sending: ' + e.reason;
    } else if (e.type === 'state' || e.type === 'progress' || e.type === 'resolved' || e.type === 'done') {
      refresh();
    }
  };
}

async function refresh() {
  try { SNAP = await api('/api/state'); render(); }
  catch (e) { toast(e.message, 'bad'); }
}

$('start').addEventListener('click', async function () {
  $('start').disabled = true;
  try {
    await api('/api/start', {
      minutes: Number($('minutes').value) || 30,
      source: $('source').value,
      dry_run: $('dryrun').checked,
      no_resolve: $('noResolve').checked,
      reply_links: Number($('replyLinks').value) || 0,
    });
    toast('run started - a browser window will open', 'good');
  } catch (e) { toast(e.message, 'bad'); }
  refresh();
});

$('stop').addEventListener('click', async function () {
  $('stop').disabled = true;
  try { await api('/api/stop', {}); toast('stop requested - finishing the current step', 'good'); }
  catch (e) { toast(e.message, 'bad'); }
  refresh();
});

document.querySelectorAll('.tab').forEach(function (tab) {
  tab.addEventListener('click', function () {
    document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
    tab.classList.add('active');
    TAB = tab.dataset.tab;
    render();
  });
});

['filter', 'hideExpired', 'hideResolved'].forEach(function (id) {
  $(id).addEventListener('input', render);
});

$('notifyBtn').addEventListener('click', async function () {
  if (!window.Notification) { toast('this browser has no Notification API', 'bad'); return; }
  let permission;
  try { permission = await Notification.requestPermission(); }
  catch (_) { toast('notification permission was refused', 'bad'); return; }
  toast('notifications ' + permission, permission === 'granted' ? 'good' : 'bad');
  if (permission === 'granted') {
    new Notification('xmaxxing', { body: 'notifications are on' });
    $('notifyBtn').disabled = true;
    $('notifyBtn').textContent = 'notifications on';
  }
});

refresh();
connect();
if (location.hash.indexOf('#action-') === 0) focusAction(location.hash.slice(8));
if (window.Notification && Notification.permission === 'granted') {
  $('notifyBtn').disabled = true;
  $('notifyBtn').textContent = 'notifications on';
}
"""