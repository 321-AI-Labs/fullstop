"""Hand-written front-end assets: one stylesheet, one script, one page shell.

Zero dependencies, zero external requests (architecture law 5). Every
user-visible word the script renders comes from the STRINGS blob injected by
the server (naming law) — the assets contain no prose of their own.
"""

APP_CSS = """\
/* fullstop activity view — one hand-written stylesheet, dark by default. */
:root {
  --bg: #0e1116;
  --bg-raised: #151a22;
  --bg-inset: #0b0e13;
  --border: #232b37;
  --border-strong: #33405280;
  --text: #e6ebf2;
  --text-dim: #9aa7b8;
  --text-faint: #67748a;
  --accent: #7aa2f7;
  --accent-ink: #0e1116;
  --ok: #9ece6a;
  --warn: #e0af68;
  --err: #f7768e;
  --chip-ok-bg: #9ece6a1f;
  --chip-warn-bg: #e0af681f;
  --chip-err-bg: #f7768e1f;
  --mono: ui-monospace, "Cascadia Mono", "Segoe UI Mono", Consolas, Menlo, monospace;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --radius: 10px;
  --focus-ring: 2px solid var(--accent);
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 var(--sans);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
a { color: var(--accent); }
:focus-visible { outline: var(--focus-ring); outline-offset: 1px; border-radius: 4px; }

header {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 14px 22px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-raised);
  position: sticky;
  top: 0;
  z-index: 5;
}
.brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
.mark {
  width: 14px; height: 14px; border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #ffd580, #e0af68 70%);
  box-shadow: 0 0 0 4px #e0af6822;
  flex: none;
}
.brand h1 { font-size: 15px; margin: 0; font-weight: 650; letter-spacing: .02em; }
.brand p { margin: 0; font-size: 11.5px; color: var(--text-faint); }

.tabs { display: flex; gap: 6px; margin-left: auto; }
.tabs button {
  background: none;
  border: 1px solid transparent;
  color: var(--text-dim);
  font: inherit;
  font-size: 13px;
  padding: 6px 14px;
  border-radius: 999px;
  cursor: pointer;
}
.tabs button:hover { color: var(--text); border-color: var(--border); }
.tabs button[aria-selected="true"] {
  color: var(--text);
  background: var(--bg-inset);
  border-color: var(--border);
}

.live { display: flex; align-items: center; gap: 8px; font-size: 12px;
  color: var(--text-dim); white-space: nowrap; }
.live .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); }
.live[data-state="live"] .dot { animation: pulse 1.6s ease-out infinite; }
.live[data-state="waiting"] .dot { background: var(--warn); }
.live[data-state="stopped"] .dot { background: var(--text-faint); animation: none; }
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 #9ece6a55; }
  70% { box-shadow: 0 0 0 7px #9ece6a00; }
  100% { box-shadow: 0 0 0 0 #9ece6a00; }
}

main { flex: 1; width: min(1060px, 100%); margin: 0 auto; padding: 20px 22px 40px; }
.hidden { display: none !important; }

.state-empty, .state-loading, .state-error {
  margin: 14px 0; padding: 18px 20px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  color: var(--text-dim);
  background: var(--bg-inset);
}
.state-error { border-color: #f7768e66; color: var(--err); }

/* -- approval cards (the showpiece) ------------------------------------ */
.approvals { margin: 6px 0 18px; display: grid; gap: 14px; }
.approval-card {
  border: 1px solid var(--warn);
  border-left-width: 4px;
  border-radius: var(--radius);
  background: linear-gradient(180deg, #e0af680d, var(--bg-raised) 70%);
  padding: 16px 18px;
}
.approval-card.answered { border-color: var(--border-strong); opacity: .75; }
.approval-card.expired { border-color: var(--text-faint); opacity: .7; }
.approval-card h2 { margin: 0 0 2px; font-size: 15px; color: var(--warn); }
.approval-card.answered h2, .approval-card.expired h2 { color: var(--text-dim); }
.approval-card .sub { margin: 0 0 12px; color: var(--text-dim); font-size: 12.5px; }
.approval-kv { display: grid; grid-template-columns: 140px 1fr; gap: 4px 12px;
  font-size: 13px; margin-bottom: 10px; }
.approval-kv dt { color: var(--text-faint); }
.approval-kv dd { margin: 0; font-family: var(--mono); overflow-wrap: anywhere; }
.approval-request {
  margin: 0 0 12px;
  padding: 12px 14px;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: 8px;
  font: 12.5px/1.55 var(--mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 340px;
  overflow: auto;
}
.approval-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
button.primary, button.danger, button.ghost {
  font: inherit; font-size: 13.5px; font-weight: 600;
  border-radius: 8px; padding: 8px 18px; cursor: pointer;
  border: 1px solid transparent;
}
button.primary { background: var(--ok); color: var(--accent-ink); }
button.primary:hover { filter: brightness(1.08); }
button.danger { background: var(--err); color: var(--accent-ink); }
button.danger:hover { filter: brightness(1.08); }
button.ghost { background: none; color: var(--text-dim); border-color: var(--border); }
button.ghost:hover { color: var(--text); }
button[disabled] { opacity: .55; cursor: default; }
.kbd-hint { font-size: 12px; color: var(--text-faint); }
kbd {
  font: 11px var(--mono); border: 1px solid var(--border);
  border-bottom-width: 2px; border-radius: 4px; padding: 0 5px;
  background: var(--bg-inset);
}
.answered-note { font-size: 12.5px; color: var(--text-dim); }

/* -- run panel ---------------------------------------------------------- */
.run-header {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-raised);
  padding: 14px 18px;
  margin-bottom: 16px;
}
.run-header .row { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.run-header h2 { margin: 0; font-size: 13px; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .08em; }
.run-goal { font-size: 15px; margin: 6px 0 10px; }
.run-stats { display: flex; gap: 26px; flex-wrap: wrap; }
.stat { min-width: 80px; }
.stat .label { font-size: 11px; color: var(--text-faint);
  text-transform: uppercase; letter-spacing: .06em; }
.stat .value { font-family: var(--mono); font-size: 15px; margin-top: 2px; }
.stat .value.usd::before { content: "$"; }
.badge {
  display: inline-block; font: 600 11.5px var(--sans);
  padding: 3px 10px; border-radius: 999px; letter-spacing: .02em;
}
.badge.running { background: #7aa2f71f; color: var(--accent); }
.badge.completed { background: var(--chip-ok-bg); color: var(--ok); }
.badge.failed { background: var(--chip-err-bg); color: var(--err); }
.badge.stopped { background: #e0af681f; color: var(--warn); }
.badge.interrupted { background: #67748a26; color: var(--text-dim); }
.badge.script_exhausted { background: #67748a26; color: var(--text-dim); }
.run-failure { margin-top: 8px; color: var(--err);
  font-family: var(--mono); font-size: 12.5px; overflow-wrap: anywhere; }
.verify-row { margin-top: 12px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.verify-result { font-size: 13px; }
.verify-result.ok { color: var(--ok); }
.verify-result.bad { color: var(--err); font-weight: 600; }

.timeline h2 { font-size: 13px; color: var(--text-faint); text-transform: uppercase;
  letter-spacing: .08em; margin: 22px 0 4px; }
.timeline-note { font-size: 11.5px; color: var(--text-faint); margin: 0 0 10px; }
.timeline { display: grid; gap: 8px; }
.event {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-raised);
  padding: 8px 12px;
  display: grid;
  grid-template-columns: 74px 150px 1fr;
  gap: 4px 14px;
  align-items: start;
  font-size: 13px;
}
.event .time { color: var(--text-faint); font: 11.5px var(--mono); padding-top: 2px; }
.event .what { color: var(--text-dim); }
.event .detail { min-width: 0; overflow-wrap: anywhere; }
.event pre {
  margin: 4px 0 0; padding: 8px 10px;
  background: var(--bg-inset); border: 1px solid var(--border); border-radius: 6px;
  font: 11.5px/1.5 var(--mono);
  white-space: pre-wrap; overflow-wrap: anywhere;
  max-height: 300px; overflow: auto;
}
.event details > summary { cursor: pointer; color: var(--accent); font-size: 12px; }
.event details[open] > summary { margin-bottom: 2px; }
.mono { font-family: var(--mono); font-size: 12.5px; }
.dim { color: var(--text-faint); }
.chip {
  display: inline-block; font: 600 11px var(--sans);
  padding: 2px 9px; border-radius: 999px; margin-right: 8px;
}
.chip.allow { background: var(--chip-ok-bg); color: var(--ok); }
.chip.ask { background: var(--chip-warn-bg); color: var(--warn); }
.chip.deny { background: var(--chip-err-bg); color: var(--err); }
.gate-reason { color: var(--text-dim); }
.ok-text { color: var(--ok); }
.err-text { color: var(--err); }
.warn-text { color: var(--warn); }

/* -- history --------------------------------------------------------------- */
.runs { display: grid; gap: 10px; }
.run-item {
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-raised); padding: 12px 16px;
  display: grid; grid-template-columns: 1fr auto; gap: 4px 16px;
}
.run-item .goal { font-size: 13.5px; }
.run-item .meta { font-size: 12px; color: var(--text-faint);
  display: flex; gap: 14px; flex-wrap: wrap; }
.run-item .meta .mono { color: var(--text-dim); }
.run-item .side { display: grid; gap: 6px; justify-items: end; }
.current-tag { font-size: 11px; color: var(--accent);
  border: 1px solid #7aa2f755; padding: 1px 8px; border-radius: 999px; }

/* -- wizard ------------------------------------------------------------------ */
.wizard-intro { color: var(--text-dim); font-size: 13px; max-width: 720px; }
.wizard-form { display: grid; gap: 22px; margin-top: 8px; }
fieldset {
  border: 1px solid var(--border); border-radius: var(--radius);
  padding: 14px 18px 16px; margin: 0; min-width: 0;
}
legend { font-size: 12px; color: var(--text-faint); text-transform: uppercase;
  letter-spacing: .08em; padding: 0 6px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px 18px; }
.field { display: grid; gap: 4px; min-width: 0; }
.field label { font-size: 12.5px; color: var(--text-dim); }
.field .hint { font-size: 11.5px; color: var(--text-faint); }
.field input, .field select, .field textarea {
  font: 13px var(--mono); color: var(--text);
  background: var(--bg-inset); border: 1px solid var(--border);
  border-radius: 7px; padding: 7px 10px; width: 100%;
}
.field textarea { min-height: 64px; resize: vertical; }
.field.has-error input, .field.has-error select, .field.has-error textarea {
  border-color: var(--err); }
.field-errors { color: var(--err); font-size: 12px; margin: 0;
  padding-left: 0; list-style: none; display: grid; gap: 2px; }
.field-errors li { font-family: var(--mono); overflow-wrap: anywhere; }
.wizard-global { border: 1px solid #f7768e55; background: #f7768e0d;
  border-radius: 8px; padding: 10px 14px; color: var(--err); font-size: 13px; }
.wizard-global ul { margin: 6px 0 0; padding-left: 18px;
  font-family: var(--mono); font-size: 12px; }
.wizard-ok { border: 1px solid #9ece6a55; background: #9ece6a0d;
  border-radius: 8px; padding: 10px 14px; color: var(--ok); font-size: 13px; }
.wizard-actions { display: flex; gap: 12px; align-items: center; }

footer {
  border-top: 1px solid var(--border); color: var(--text-faint);
  font-size: 11.5px; text-align: center; padding: 12px;
}
"""

APP_JS = """\
'use strict';
/* fullstop activity view — hand-written, stdlib-served, no dependencies.
   All prose comes from window.STRINGS (injected by the server). */

const S = window.STRINGS;
const $ = (sel, root) => (root || document).querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
};
const fmtTime = (ts) => {
  if (!ts) return '';
  try { return new Date(ts).toLocaleTimeString([], {hour12: false}); }
  catch (e) { return ts; }
};
const fmtUsd = (v) => (typeof v === 'number' ? v.toFixed(4) : String(v || 0));

const state = {
  lastSeq: 0,
  entries: [],
  answered: {},   // approval id -> 'approve' | 'deny' (optimistic, until gone)
  tab: 'run',
  pollTimer: null,
  reachable: true,
};

/* -- page chrome --------------------------------------------------------- */
function buildChrome() {
  document.title = S.pageTitle;
  $('#product-name').textContent = S.productName;
  $('#tagline').textContent = S.tagline;
  $('#footer-note').textContent = S.footerOffline;
  $('#timeline-heading').textContent = S.logTitle;
  $('#timeline-note-holder').textContent = S.logTruncationNote;
  $('#timeline-empty').textContent = S.logLoading;
  $('#history-heading').textContent = S.historyTitle;
  $('#history-list').replaceChildren(el('div', 'state-loading', S.historyLoading));
  const runView = $('#run-view');
  runView.replaceChildren(el('div', 'state-loading', S.runLoading));
  const tabs = [['run', S.tabRun], ['history', S.tabHistory], ['wizard', S.tabWizard]];
  const nav = $('#tabs');
  nav.setAttribute('aria-label', S.tabsAriaLabel);
  for (const [id, label] of tabs) {
    const b = el('button', null, label);
    b.setAttribute('role', 'tab');
    b.dataset.tab = id;
    b.setAttribute('aria-selected', id === state.tab ? 'true' : 'false');
    b.addEventListener('click', () => selectTab(id));
    nav.appendChild(b);
  }
  selectTab(state.tab);
}

function selectTab(id) {
  state.tab = id;
  for (const b of $('#tabs').children)
    b.setAttribute('aria-selected', b.dataset.tab === id ? 'true' : 'false');
  for (const p of ['run', 'history', 'wizard'])
    $('#panel-' + p).classList.toggle('hidden', p !== id);
}

/* -- polling ---------------------------------------------------------------- */
async function fetchJson(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = '';
    try { detail = (await r.json()).error || ''; } catch (e) {}
    throw new Error(S.httpErrorPrefix + r.status + (detail ? ': ' + detail : ''));
  }
  return r.json();
}

function setReachable(ok, err) {
  state.reachable = ok;
  const banner = $('#conn-error');
  banner.classList.toggle('hidden', ok);
  if (!ok) banner.textContent = S.errorGeneric.replace('{error}', err || '');
}

async function poll() {
  try {
    const [st, lg] = await Promise.all([
      fetchJson('/api/state'),
      fetchJson('/api/log?after_seq=' + state.lastSeq),
    ]);
    setReachable(true);
    renderState(st);
    appendLog(lg.items || []);
    state.lastSeq = lg.last_seq || state.lastSeq;
  } catch (e) {
    setReachable(false, e.message);
  }
}

/* -- run panel ---------------------------------------------------------------- */
function renderState(st) {
  const live = $('#live-indicator');
  const snap = st.snapshot;
  live.dataset.state = snap && snap.live ? 'live'
    : (snap && ['completed', 'failed'].includes(snap.status) ? 'stopped' : 'waiting');
  $('#live-label').textContent =
    live.dataset.state === 'live' ? S.liveLive
    : live.dataset.state === 'stopped' ? S.liveStopped : S.liveWaiting;

  const runBox = $('#run-view');
  // Rebuild the header only when what it SHOWS changes: a poll-driven
  // rebuild would wipe transient state inside it (the verify result) and
  // churn the DOM every cycle.
  const sig = JSON.stringify([snap, st.history && st.history.length
    ? st.history[st.history.length - 1].run_id : null]);
  if (sig === (runBox.dataset.sig || '')) {
    renderApprovals(st.pending || []);
    renderHistory(st.history || []);
    return;
  }
  runBox.dataset.sig = sig;
  if (!snap && !st.history.length) {
    runBox.replaceChildren(empty(S.runEmpty));
    renderApprovals(st.pending || []);
    renderHistory(st.history || []);
    return;
  }
  const box = el('div', 'run-header');
  const row = el('div', 'row');
  row.appendChild(el('h2', null, S.runHeaderTitle));
  if (snap) {
    row.appendChild(badge(snap.status));
    if (st.history && st.history.length &&
        st.history[st.history.length - 1].run_id === snap.run_id)
      row.appendChild(el('span', 'current-tag', S.historyCurrentTag));
  }
  box.appendChild(row);
  if (snap) {
    box.appendChild(el('div', 'run-goal', snap.goal));
    const stats = el('div', 'run-stats');
    stats.appendChild(stat(S.runStepsLabel, snap.steps_done));
    stats.appendChild(stat(S.runCostLabel, fmtUsd(snap.cost_usd), 'usd', S.runCostUnit));
    stats.appendChild(stat(S.runTokensLabel,
      S.runTokensValue
        .replace('{input}', snap.tokens_in || 0)
        .replace('{output}', snap.tokens_out || 0)));
    const idStat = stat(S.runRunIdLabel, snap.run_id, 'mono');
    idStat.querySelector('.value').style.fontSize = '11.5px';
    stats.appendChild(idStat);
    box.appendChild(stats);
    if (snap.failure) {
      const f = el('div', 'run-failure', S.runFailureLabel + ': ' + snap.failure);
      box.appendChild(f);
    }
    const vr = el('div', 'verify-row');
    const vb = el('button', 'ghost', S.runVerifyButton);
    vb.id = 'verify-button';
    vb.addEventListener('click', runVerify);
    vr.appendChild(vb);
    const res = el('span', 'verify-result', '');
    res.id = 'verify-result';
    vr.appendChild(res);
    box.appendChild(vr);
  } else {
    box.appendChild(el('div', 'state-empty', S.runEmpty));
  }
  runBox.replaceChildren(box);
  renderApprovals(st.pending || []);
  renderHistory(st.history || []);
}

function empty(text) {
  const n = el('div', 'state-empty', text);
  return n;
}

function badge(status) {
  let cls = 'badge ';
  if (status === 'running') cls += 'running';
  else if (status === 'completed') cls += 'completed';
  else if (status === 'failed') cls += 'failed';
  else if (status === 'interrupted' || status === 'script_exhausted') cls += 'interrupted';
  else if (status && status.startsWith('stopped_')) cls += 'stopped';
  else cls += 'interrupted';
  const b = el('span', cls, status);
  return b;
}

function stat(label, value, extra, unit) {
  const n = el('div', 'stat');
  n.appendChild(el('div', 'label', label));
  const v = el('div', 'value' + (extra ? ' ' + extra : ''), value);
  if (unit) v.appendChild(el('span', 'dim', ' ' + unit));
  n.appendChild(v);
  return n;
}

async function runVerify() {
  const res = $('#verify-result');
  const btn = $('#verify-button');
  btn.disabled = true;
  res.className = 'verify-result';
  res.textContent = S.runVerifyQueued;
  try {
    const v = await fetchJson('/api/verify');
    if (v.ok) {
      res.className = 'verify-result ok';
      res.textContent = '\\u2713 ' +
        S.runVerifyOk.replace('{entries}', v.entries);
    } else {
      res.className = 'verify-result bad';
      res.textContent = '\\u2717 ' +
        S.runVerifyBad.replace('{first_bad}', v.first_bad_seq);
    }
  } catch (e) {
    res.className = 'verify-result bad';
    res.textContent = S.runVerifyError.replace('{error}', e.message);
  } finally {
    btn.disabled = false;
  }
}

/* -- timeline ------------------------------------------------------------------ */
function appendLog(items) {
  const list = $('#timeline-list');
  const note = $('#timeline-note-holder');
  if (note.classList.contains('hidden') && items.length) note.classList.remove('hidden');
  for (const entry of items) state.entries.push(entry);
  if (!state.entries.length) return;
  $('#timeline-empty').classList.add('hidden');
  for (const entry of items) list.appendChild(renderEvent(entry));
  list.scrollTop = list.scrollHeight;
}

function chip(kind, reason) {
  const n = el('span', null);
  const c = el('span', 'chip ' + kind,
    kind === 'allow' ? S.chipAllow : kind === 'deny' ? S.chipDeny : S.chipAsk);
  n.appendChild(c);
  if (reason) n.appendChild(el('span', 'gate-reason',
    S.chipReasonLabel + ': ' + reason));
  return n;
}

function kv(label, value) {
  const n = el('span', null);
  n.appendChild(el('span', 'dim', label + ': '));
  n.appendChild(el('span', 'mono', value));
  return n;
}

function preBlock(text) {
  const pre = el('pre', null, text);
  return pre;
}

function renderEvent(e) {
  const row = el('div', 'event');
  row.appendChild(el('span', 'time', fmtTime(e.ts)));
  row.appendChild(el('span', 'what', S.eventLabels[e.event] || e.event));
  const d = el('div', 'detail');
  const ev = e.event;
  if (ev === 'run_start') {
    d.appendChild(kv(S.kvGoalLabel, e.goal));
  } else if (ev === 'resume') {
    d.appendChild(kv(S.kvStepsDoneLabel, e.steps_done));
  } else if (ev === 'model_reply') {
    const det = el('details');
    det.appendChild(el('summary', null, S.modelReplySummary
      .replace('{input}', e.input_tokens || 0)
      .replace('{output}', e.output_tokens || 0)));
    det.appendChild(preBlock(e.content || ''));
    d.appendChild(det);
  } else if (ev === 'tool_call') {
    d.appendChild(kv(e.tool, ''));
    const det = el('details');
    det.appendChild(el('summary', null, S.detailArgsLabel));
    det.appendChild(preBlock(JSON.stringify(e.args, null, 2)));
    d.appendChild(det);
  } else if (ev === 'gate_decision') {
    const kind = e.action === 'allow' ? 'allow'
      : e.action === 'deny' ? 'deny' : 'ask';
    d.appendChild(chip(kind, e.reason));
    if (e.classification) d.appendChild(el('span', 'dim', ' \\u00b7 ' + e.classification));
  } else if (ev === 'approval_request') {
    d.appendChild(chip('ask', e.reason));
    d.appendChild(el('span', 'mono', ' ' + (e.tool || '')));
  } else if (ev === 'approval_response') {
    d.appendChild(e.approved ? chip('allow', e.reason) : chip('deny', e.reason));
  } else if (ev === 'sandbox_block') {
    d.appendChild(chip('deny', e.detail));
    d.appendChild(el('span', 'mono', ' ' + (e.tool || '')));
  } else if (ev === 'tool_result') {
    const head = el('div');
    head.appendChild(el('span', e.ok ? 'ok-text' : 'err-text',
      (e.ok ? S.toolResultOk : S.toolResultError + (e.error_code || '')) + ' '));
    head.appendChild(el('span', 'mono', e.tool || ''));
    d.appendChild(head);
    if (e.error) d.appendChild(el('div', 'err-text mono', e.error));
    if (e.output) {
      const det = el('details');
      det.appendChild(el('summary', null, S.detailOutputLabel));
      det.appendChild(preBlock(e.output));
      d.appendChild(det);
    }
  } else if (ev === 'step') {
    d.appendChild(kv(S.kvNLabel, e.n));
  } else if (ev === 'checkpoint') {
    d.appendChild(kv(S.kvNLabel, e.n));
    if (e.status) d.appendChild(el('span', 'dim', ' \\u00b7 ' + e.status));
  } else if (ev === 'guard_trip') {
    d.appendChild(chip('deny', e.kind));
    if (e.limit !== undefined && e.limit !== null)
      d.appendChild(el('span', 'dim',
        ' \\u00b7 ' + S.fragLimit + ' ' + e.limit +
        ' \\u00b7 ' + S.fragValue + ' ' + e.value));
    if (e.tool) d.appendChild(el('span', 'mono', ' ' + e.tool));
  } else if (ev === 'config_loaded') {
    d.appendChild(kv(S.kvManifestLabel,
      String(e.manifest_sha256 || '').slice(0, 12)));
    d.appendChild(el('span', 'dim', ' \\u00b7 ' + S.fragPolicy + ' ' +
      String(e.policy_sha256 || '').slice(0, 12)));
  } else if (ev === 'run_end') {
    d.appendChild(badge(e.status));
  }
  row.appendChild(d);
  return row;
}

/* -- approval cards --------------------------------------------------------------- */
function renderApprovals(cards) {
  const host = $('#approvals');
  // Rebuild ONLY when the card set actually changes: a poll-driven rebuild
  // every cycle would churn the DOM, drop focus mid-keystroke, and yank the
  // buttons out from under a click (keyboard-operable law).
  const sig = cards.map((c) =>
    c.id + ':' + (state.answered[c.id] ? 'a' : c.expired ? 'e' : 'p')).join('|');
  if (sig === (host.dataset.sig || '') && host.children.length === cards.length)
    return;
  host.dataset.sig = sig;
  host.replaceChildren();
  if (!cards.length) return;
  for (const card of cards) {
    const answered = state.answered[card.id];
    const box = el('section', 'approval-card' +
      (answered ? ' answered' : card.expired ? ' expired' : ''));
    box.tabIndex = 0;
    box.dataset.approvalId = card.id;
    box.appendChild(el('h2', null, S.approvalTitle));
    box.appendChild(el('p', 'sub', answered
      ? (answered === 'approve' ? S.approvalAnsweredApprove : S.approvalAnsweredDeny)
      : card.expired ? S.approvalExpired : S.approvalSubtitle));
    const dl = el('dl', 'approval-kv');
    const add = (label, value) => {
      dl.appendChild(el('dt', null, label));
      dl.appendChild(el('dd', null, value));
    };
    add(S.approvalToolLabel, card.tool);
    add(S.approvalReasonLabel, card.reason);
    box.appendChild(dl);
    box.appendChild(el('h3', 'dim', S.approvalRequestLabel));
    box.appendChild(preBlock(card.rendered));
    const actions = el('div', 'approval-actions');
    if (!answered && !card.expired) {
      const yes = el('button', 'primary', S.approvalApprove);
      const no = el('button', 'danger', S.approvalDeny);
      yes.addEventListener('click', () => answer(card.id, true));
      no.addEventListener('click', () => answer(card.id, false));
      actions.appendChild(yes);
      actions.appendChild(no);
      const hint = el('span', 'kbd-hint', S.approvalKeyboardHint);
      actions.appendChild(hint);
    }
    box.appendChild(actions);
    host.appendChild(box);
  }
}

async function answer(id, approve) {
  if (state.answered[id]) return;
  state.answered[id] = approve ? 'approve' : 'deny';
  try {
    await fetchJson('/api/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Fullstop-UI': '1'},
      body: JSON.stringify({id: id, decision: approve ? 'approve' : 'deny'}),
    });
  } catch (e) {
    delete state.answered[id];
    setReachable(false, e.message);
  }
  poll();
}

function onKey(e) {
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT'))
    return;
  const cards = $('#approvals').querySelectorAll('.approval-card:not(.answered):not(.expired)');
  if (!cards.length) return;
  if (e.key === 'y' || e.key === 'Y') {
    e.preventDefault();
    answer(cards[0].dataset.approvalId, true);
  } else if (e.key === 'n' || e.key === 'N') {
    e.preventDefault();
    answer(cards[0].dataset.approvalId, false);
  }
}

/* -- history -------------------------------------------------------------------- */
function renderHistory(runs) {
  const host = $('#history-list');
  host.replaceChildren();
  if (!runs.length) {
    host.appendChild(empty(S.historyEmpty));
    return;
  }
  const list = el('div', 'runs');
  for (let i = runs.length - 1; i >= 0; i--) {
    const r = runs[i];
    const item = el('div', 'run-item');
    const main = el('div');
    main.appendChild(el('div', 'goal', r.goal));
    const meta = el('div', 'meta');
    meta.appendChild(el('span', 'mono', String(r.run_id || '').slice(0, 12)));
    meta.appendChild(el('span', null, S.historyRunSteps.replace('{steps}', r.steps)));
    meta.appendChild(el('span', null, S.historyRunTokens
      .replace('{input}', r.tokens_in).replace('{output}', r.tokens_out)));
    meta.appendChild(el('span', null, S.historyStarted.replace('{ts}', fmtTime(r.started))));
    if (r.ended) meta.appendChild(el('span', null,
      S.historyEnded.replace('{ts}', fmtTime(r.ended))));
    main.appendChild(meta);
    item.appendChild(main);
    const side = el('div', 'side');
    side.appendChild(badge(r.status));
    if (i === runs.length - 1) side.appendChild(el('span', 'current-tag',
      S.historyCurrentTag));
    item.appendChild(side);
    list.appendChild(item);
  }
  host.appendChild(list);
}

/* -- wizard ---------------------------------------------------------------------- */
const WIZARD_FIELDS = [
  ['name', 'text'], ['role', 'text'], ['home', 'text'], ['goal', 'textarea'],
];
const WIZARD_PROVIDER = [
  ['providerType', 'select', ['scripted', 'openai_compat']],
  ['scriptPath', 'text'], ['baseUrl', 'text'], ['apiKeyEnv', 'text'],
  ['model', 'text'], ['priceIn', 'text'], ['priceOut', 'text'],
];
const WIZARD_LIMITS = [
  ['maxSteps', 'text'], ['maxCost', 'text'], ['maxCalls', 'text'],
  ['maxContext', 'text'], ['logTruncate', 'text'], ['credentials', 'text'],
];
const WIZARD_POLICY = [
  ['protected', 'textarea'], ['preapproved', 'textarea'],
  ['shellAllow', 'textarea'], ['shellDeny', 'textarea'],
  ['webAllow', 'textarea'], ['webDeny', 'textarea'],
];
const HINTS = {home: 'homeHint', apiKeyEnv: 'apiKeyEnvHint',
  credentials: 'credentialsHint', shellAllow: 'shellAllowHint',
  savePath: 'savePathHint'};

function fieldRow(key, kind, options) {
  const wrap = el('div', 'field');
  wrap.dataset.field = key;
  const id = 'wiz-' + key;
  const label = el('label', null, S.wizardFields[key]);
  label.setAttribute('for', id);
  wrap.appendChild(label);
  let input;
  if (kind === 'select') {
    input = el('select');
    for (const o of options) {
      const opt = el('option', null, o);
      opt.value = o;
      input.appendChild(opt);
    }
  } else if (kind === 'textarea') {
    input = el('textarea');
  } else {
    input = el('input');
    input.type = 'text';
  }
  input.id = id;
  input.dataset.wizInput = key;
  wrap.appendChild(input);
  if (HINTS[key]) wrap.appendChild(el('div', 'hint', S.wizardFields[HINTS[key]]));
  const errs = el('ul', 'field-errors hidden');
  errs.dataset.errors = key;
  wrap.appendChild(errs);
  return wrap;
}

function buildWizard() {
  const intro = $('#wizard-intro');
  intro.textContent = S.wizardIntro;
  const form = el('div', 'wizard-form');
  const section = (title, fields) => {
    const fs = el('fieldset');
    fs.appendChild(el('legend', null, title));
    const grid = el('div', 'grid2');
    for (const f of fields) grid.appendChild(fieldRow(f[0], f[1], f[2]));
    fs.appendChild(grid);
    return fs;
  };
  form.appendChild(el('h2', null, S.wizardTitle));
  form.appendChild(section(S.wizardSectionIdentity, WIZARD_FIELDS.slice(0, 2)));
  form.appendChild(section(S.wizardSectionGoal, [WIZARD_FIELDS[3]]));
  form.appendChild(section(S.wizardSectionProvider, WIZARD_PROVIDER));
  form.appendChild(section(S.wizardSectionLimits, WIZARD_LIMITS));
  form.appendChild(section(S.wizardSectionPolicy, WIZARD_POLICY));
  const saveFs = el('fieldset');
  saveFs.appendChild(el('legend', null, S.wizardSectionSave));
  const grid = el('div', 'grid2');
  grid.appendChild(fieldRow('savePath', 'text'));
  saveFs.appendChild(grid);
  const result = el('div');
  result.id = 'wizard-result';
  saveFs.appendChild(result);
  const actions = el('div', 'wizard-actions');
  const validate = el('button', 'ghost', S.wizardValidateButton);
  validate.addEventListener('click', () => submitWizard(false));
  const save = el('button', 'primary', S.wizardSaveButton);
  save.addEventListener('click', () => submitWizard(true));
  actions.appendChild(validate);
  actions.appendChild(save);
  saveFs.appendChild(actions);
  form.appendChild(saveFs);
  $('#wizard-holder').replaceChildren(form);
}

function collectForm() {
  const out = {};
  document.querySelectorAll('[data-wiz-input]').forEach((input) => {
    out[input.dataset.wizInput] = input.value;
  });
  return out;
}

function clearErrors() {
  document.querySelectorAll('.field-errors').forEach((u) => {
    u.classList.add('hidden');
    u.replaceChildren();
  });
  document.querySelectorAll('.field').forEach((f) =>
    f.classList.remove('has-error'));
  $('#wizard-result').replaceChildren();
}

function showErrors(payload) {
  clearErrors();
  const box = $('#wizard-result');
  const bad = el('div', 'wizard-global');
  bad.appendChild(el('div', null, S.wizardValidBad));
  const ul = el('ul');
  for (const line of payload.global_errors || []) ul.appendChild(el('li', null, line));
  bad.appendChild(ul);
  box.appendChild(bad);
  for (const [field, messages] of Object.entries(payload.field_errors || {})) {
    const host = document.querySelector('[data-errors="' + field + '"]');
    const wrap = document.querySelector('[data-field="' + field + '"]');
    if (!host || !wrap) {
      for (const m of messages) ul.appendChild(el('li', null, m));
      continue;
    }
    wrap.classList.add('has-error');
    host.classList.remove('hidden');
    for (const m of messages) host.appendChild(el('li', null, m));
  }
}

async function submitWizard(save) {
  clearErrors();
  const box = $('#wizard-result');
  const note = el('div', 'state-loading', S.wizardValidating);
  box.appendChild(note);
  try {
    const payload = await fetchJson(save ? '/api/wizard/save' : '/api/wizard/validate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Fullstop-UI': '1'},
      body: JSON.stringify({form: collectForm()}),
    });
    if (payload.ok) {
      const ok = el('div', 'wizard-ok');
      ok.textContent = payload.saved_path
        ? S.wizardSaved.replace('{path}', payload.saved_path)
          .replace('{bytes}', payload.bytes)
        : S.wizardValidOk;
      box.appendChild(ok);
    } else {
      showErrors(payload);
    }
  } catch (e) {
    const bad = el('div', 'state-error',
      S.errorGeneric.replace('{error}', e.message));
    box.appendChild(bad);
  }
}

/* -- boot -------------------------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', () => {
  buildChrome();
  buildWizard();
  document.addEventListener('keydown', onKey);
  poll();
  state.pollTimer = setInterval(poll, 1200);
});
"""

APP_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title></title>
<link rel="stylesheet" href="/app.css">
<script>window.STRINGS = {strings};</script>
<script defer src="/app.js"></script>
</head>
<body>
<header>
  <div class="brand">
    <span class="mark" aria-hidden="true"></span>
    <div><h1 id="product-name"></h1><p id="tagline"></p></div>
  </div>
  <nav class="tabs" id="tabs" role="tablist"></nav>
  <div class="live" id="live-indicator" data-state="waiting">
    <span class="dot" aria-hidden="true"></span><span id="live-label"></span>
  </div>
</header>
<main>
  <div id="conn-error" class="state-error hidden" role="alert"></div>
  <section id="approvals" class="approvals" aria-live="polite"></section>
  <section id="panel-run" class="panel" role="tabpanel">
    <div id="run-view"></div>
    <div class="timeline">
      <h2 id="timeline-heading"></h2>
      <p id="timeline-note-holder" class="timeline-note hidden"></p>
      <div id="timeline-list" class="timeline"></div>
      <div id="timeline-empty" class="state-loading"></div>
    </div>
  </section>
  <section id="panel-history" class="panel hidden" role="tabpanel">
    <div class="timeline"><h2 id="history-heading"></h2></div>
    <div id="history-list"></div>
  </section>
  <section id="panel-wizard" class="panel hidden" role="tabpanel">
    <p id="wizard-intro" class="wizard-intro"></p>
    <div id="wizard-holder"></div>
  </section>
</main>
<footer id="footer-note"></footer>
</body>
</html>
"""


def render_html(strings_json: str) -> str:
    """The page shell. Strings blob injected verbatim; heading placeholders
    are filled by the script (so the shell itself carries no prose)."""
    # The blob lands inside an inline <script>; a "</script" anywhere in it
    # would break out of the element and turn string content into markup.
    assert "</script" not in strings_json.lower(), (
        "strings blob must never contain a script-close tag; it is "
        "injected verbatim into the HTML")
    return APP_HTML.replace("{strings}", strings_json)
