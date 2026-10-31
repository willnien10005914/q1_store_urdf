import {initI18n, getLocale, L, t, setLocale, applyI18n} from './i18n.js';
import {renderMarkdown} from './markdown.js';

const USER_KEY = 'q1-tutorial-user';
const ROBOT_KEY = 'q1-tutorial-robot';
const LAYOUT_KEY = 'q1-tutorial-layout';
const SIDE_MIN = 160;
const SIDE_MAX = 480;
const AGENT_MIN = 240;
const AGENT_MAX = 560;
const $ = (id) => document.getElementById(id);

function loadLayoutPrefs() {
  try {
    return JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}') || {};
  } catch {
    return {};
  }
}
function saveLayoutPrefs() {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify({
    sideWidth: state.sideWidth,
    agentWidth: state.agentWidth,
  }));
}
let toastTimer;
function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

async function api(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Error(data.error || 'HTTP ' + r.status);
  return data;
}

const state = {
  user: null,
  progress: null,
  catalog: null,
  robot: localStorage.getItem(ROBOT_KEY) || 'leg',
  lessonId: null,
  lesson: null,
  originalCode: '',
  baselineCode: '',
  codeMarks: {}, // lineIndex -> {source:'manual'|'hermes'|'note', note?}
  codeEditTimer: null,
  codeWide: false,   // widen code vs params inside .tut-lab
  codeFocus: false,  // hide left lesson nav + right Hermes (full-bleed code)
  sideWidth: Number(loadLayoutPrefs().sideWidth) || 240,
  agentWidth: Number(loadLayoutPrefs().agentWidth) || 340,
  runBusy: false,
  runId: null,
  lastRun: null,
  runPoll: null,
  llm: null,
  pendingProposal: null,
  pendingDiff: null,
};

/* ---------- Solarized-lite code editor (line numbers + change marks) ---------- */
const NOTE_HERMES = /^#\s*\[Hermes/;
const NOTE_MANUAL = /^#\s*\[手動|#\s*\[Manual/i;

function labEl() {
  return document.querySelector('.tut-lab');
}
function pageEl() {
  return document.querySelector('.tut-page');
}
function layoutEl() {
  return document.querySelector('.tut-layout');
}

function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

function applyPaneWidths() {
  const layout = layoutEl();
  if (!layout) return;
  const side = clamp(Math.round(state.sideWidth || 240), SIDE_MIN, SIDE_MAX);
  const agent = clamp(Math.round(state.agentWidth || 340), AGENT_MIN, AGENT_MAX);
  state.sideWidth = side;
  state.agentWidth = agent;
  layout.style.setProperty('--tut-side-w', `${side}px`);
  layout.style.setProperty('--tut-agent-w', `${agent}px`);
  if (state.codeFocus) {
    layout.style.removeProperty('grid-template-columns');
  } else {
    layout.style.gridTemplateColumns = `${side}px minmax(0,1fr) ${agent}px`;
  }
}

function syncLayoutChrome() {
  const editing = state.codeWide || state.codeFocus || document.activeElement === $('code');
  const lab = labEl();
  const page = pageEl();
  const layout = layoutEl();
  lab?.classList.toggle('is-editing', editing || state.codeWide);
  lab?.classList.toggle('is-wide', state.codeWide || state.codeFocus);
  // Widen middle code without hiding sidebars; focus is the only mode that collapses them.
  page?.classList.toggle('is-code-editing', (editing || state.codeWide) && !state.codeFocus);
  page?.classList.toggle('is-code-focus', state.codeFocus);
  layout?.classList.toggle('is-code-editing', (editing || state.codeWide) && !state.codeFocus);
  layout?.classList.toggle('is-code-focus', state.codeFocus);
  document.body.classList.toggle('tut-code-focus', state.codeFocus);
  if (!state.codeWide && !state.codeFocus && lab) lab.style.removeProperty('grid-template-columns');
  applyPaneWidths();

  const widenBtn = $('code-widen');
  if (widenBtn) {
    widenBtn.textContent = state.codeWide
      ? (getLocale() === 'en' ? 'Narrow code' : '縮回程式區')
      : (getLocale() === 'en' ? 'Widen code' : '加寬程式區');
    widenBtn.title = state.codeWide
      ? (getLocale() === 'en' ? 'Restore code / params split' : '還原程式區與參數欄比例')
      : (getLocale() === 'en' ? 'Give example.py more width (keeps side panels)' : '加寬 example.py（保留左右欄）');
    widenBtn.setAttribute('aria-pressed', state.codeWide ? 'true' : 'false');
  }
  const focusBtn = $('code-focus');
  if (focusBtn) {
    focusBtn.textContent = state.codeFocus
      ? (getLocale() === 'en' ? 'Show side panels' : '顯示左右欄')
      : (getLocale() === 'en' ? 'Focus' : '專注');
    focusBtn.title = state.codeFocus
      ? (getLocale() === 'en' ? 'Restore lesson list and Hermes panels' : '還原左側課程列表與右側 Hermes')
      : (getLocale() === 'en' ? 'Hide side panels so code fills the page' : '收合左右欄，程式區佔滿畫面');
    focusBtn.setAttribute('aria-pressed', state.codeFocus ? 'true' : 'false');
  }
  const dock = $('layout-restore-dock');
  if (dock) dock.hidden = !state.codeFocus;
  requestAnimationFrame(() => syncCodeChrome());
}

function wirePaneResize() {
  const layout = layoutEl();
  if (!layout) return;

  const bind = (handleId, which) => {
    const handle = $(handleId);
    if (!handle) return;
    let dragging = false;
    handle.addEventListener('pointerdown', (e) => {
      if (state.codeFocus || window.matchMedia('(max-width: 1100px)').matches) return;
      dragging = true;
      handle.classList.add('is-active');
      layout.classList.add('is-pane-resizing');
      handle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    handle.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const rect = layout.getBoundingClientRect();
      const gap = 16;
      if (which === 'side') {
        const maxSide = Math.min(SIDE_MAX, rect.width - state.agentWidth - gap * 2 - 320);
        state.sideWidth = clamp(e.clientX - rect.left, SIDE_MIN, Math.max(SIDE_MIN, maxSide));
      } else {
        const maxAgent = Math.min(AGENT_MAX, rect.width - state.sideWidth - gap * 2 - 320);
        state.agentWidth = clamp(rect.right - e.clientX, AGENT_MIN, Math.max(AGENT_MIN, maxAgent));
      }
      applyPaneWidths();
    });
    const stop = () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove('is-active');
      layout.classList.remove('is-pane-resizing');
      saveLayoutPrefs();
    };
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  };

  bind('resize-side', 'side');
  bind('resize-agent', 'agent');
  applyPaneWidths();
}

function setCodeExpanded(on) {
  if (!on) {
    state.codeWide = false;
    state.codeFocus = false;
  }
  syncLayoutChrome();
}

function setCodeWide(on) {
  state.codeWide = !!on;
  if (state.codeWide) state.codeFocus = false;
  syncLayoutChrome();
}

function setCodeFocus(on) {
  state.codeFocus = !!on;
  if (state.codeFocus) {
    state.codeWide = true;
    document.querySelectorAll('.tut-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === 'lab'));
    document.querySelectorAll('.tut-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === 'lab'));
  }
  syncLayoutChrome();
  if (state.codeFocus) $('code')?.focus({preventScroll: true});
}

function lineDiffIndexes(before, after) {
  const a = String(before || '').split('\n');
  const b = String(after || '').split('\n');
  const n = a.length, m = b.length;
  const dp = Array.from({length: n + 1}, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const changed = new Set();
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { i++; }
    else { changed.add(j); j++; }
  }
  while (j < m) { changed.add(j); j++; }
  return changed;
}

function syncCodeChrome() {
  const ta = $('code');
  const gutter = $('code-gutter');
  const backdrop = $('code-backdrop');
  if (!ta || !gutter || !backdrop) return;
  const lines = ta.value.split('\n');
  const n = Math.max(lines.length, 1);
  gutter.textContent = Array.from({length: n}, (_, i) => String(i + 1)).join('\n');
  backdrop.innerHTML = lines.map((line, i) => {
    const mark = state.codeMarks[i];
    let cls = 'ln';
    if (mark?.source === 'hermes') cls += ' hermes';
    else if (mark?.source === 'manual') cls += ' manual';
    if (NOTE_HERMES.test(line) || NOTE_MANUAL.test(line)) cls += ' note';
    return `<span class="${cls}">${escapeHtml(line) || ' '}</span>`;
  }).join('\n');
  gutter.scrollTop = ta.scrollTop;
  backdrop.style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
}

function setCode(value, {baseline = false, marks = null, expand = false} = {}) {
  const ta = $('code');
  if (!ta) return;
  ta.value = value ?? '';
  if (baseline || !state.baselineCode) state.baselineCode = ta.value;
  if (marks) state.codeMarks = marks;
  else if (baseline) state.codeMarks = {};
  syncCodeChrome();
  if (expand) setCodeExpanded(true);
}

function annotateChangedLines(before, after, source, explanation) {
  const changed = lineDiffIndexes(before, after);
  if (!changed.size) return after;
  const lines = after.split('\n');
  const note = (explanation || '').replace(/\s+/g, ' ').trim().slice(0, 140);
  const tag = source === 'hermes'
    ? `# [Hermes] ${note || (getLocale() === 'en' ? 'Suggested edit — press Run to verify.' : '建議修改 — 請按「執行」驗證。')}`
    : `# [手動] ${note || (getLocale() === 'en' ? 'Your edit — press Run to verify.' : '你的修改 — 請按「執行」驗證。')}`;
  const out = [];
  const newMarks = {...state.codeMarks};
  let inserted = 0;
  const sorted = [...changed].sort((a, b) => a - b);
  let cursor = 0;
  for (const idx of sorted) {
    while (cursor < idx) {
      out.push(lines[cursor]);
      cursor++;
    }
    const prev = out[out.length - 1] || '';
    if (!NOTE_HERMES.test(prev) && !NOTE_MANUAL.test(prev)) {
      out.push(tag);
      newMarks[out.length - 1] = {source: 'note'};
      inserted++;
    }
    out.push(lines[idx]);
    newMarks[out.length - 1] = {source, note};
    cursor = idx + 1;
  }
  while (cursor < lines.length) {
    out.push(lines[cursor]);
    cursor++;
  }
  // Remap any old marks roughly by shifting after inserts — keep only newMarks for changed region.
  state.codeMarks = newMarks;
  // Also mark remaining changed content lines that already had comments
  const remapped = lineDiffIndexes(before, out.join('\n'));
  remapped.forEach((i) => {
    const line = out[i] || '';
    if (NOTE_HERMES.test(line) || NOTE_MANUAL.test(line)) state.codeMarks[i] = {source: 'note'};
    else if (!state.codeMarks[i] || state.codeMarks[i].source === 'note') state.codeMarks[i] = {source, note};
  });
  return out.join('\n');
}

function onCodeInput() {
  syncLayoutChrome();
  const ta = $('code');
  const before = state.baselineCode || state.originalCode || '';
  const after = ta.value;
  const changed = lineDiffIndexes(before, after);
  const next = {};
  Object.entries(state.codeMarks).forEach(([k, v]) => {
    if (v.source === 'hermes' || v.source === 'note') next[k] = v;
  });
  changed.forEach((i) => {
    const line = after.split('\n')[i] || '';
    if (NOTE_HERMES.test(line)) next[i] = {source: 'note'};
    else if (NOTE_MANUAL.test(line)) next[i] = {source: 'note'};
    else if (state.codeMarks[i]?.source === 'hermes') next[i] = state.codeMarks[i];
    else next[i] = {source: 'manual'};
  });
  state.codeMarks = next;
  syncCodeChrome();
  clearTimeout(state.codeEditTimer);
  state.codeEditTimer = setTimeout(() => {
    // After idle, inject a single manual annotation block if missing.
    const cur = $('code').value;
    if (cur === before) return;
    const annotated = annotateChangedLines(before, cur, 'manual', '');
    if (annotated !== cur) {
      const sel = $('code').selectionStart;
      setCode(annotated, {marks: state.codeMarks});
      try { $('code').selectionStart = $('code').selectionEnd = sel; } catch {}
    }
    state.baselineCode = $('code').value;
  }, 1600);
}

function wireCodeEditor() {
  const ta = $('code');
  const scroll = $('code-scroll');
  const gutter = $('code-gutter');
  if (!ta) return;
  ta.addEventListener('input', onCodeInput);
  ta.addEventListener('scroll', () => {
    if (gutter) gutter.scrollTop = ta.scrollTop;
    const backdrop = $('code-backdrop');
    if (backdrop) backdrop.style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
  });
  ta.addEventListener('focus', () => syncLayoutChrome());
  ta.addEventListener('blur', () => requestAnimationFrame(() => syncLayoutChrome()));
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.codeFocus) {
      e.preventDefault();
      setCodeFocus(false);
      return;
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = ta.selectionStart, end = ta.selectionEnd;
      ta.value = ta.value.slice(0, start) + '    ' + ta.value.slice(end);
      ta.selectionStart = ta.selectionEnd = start + 4;
      onCodeInput();
    }
  });
  $('code-widen')?.addEventListener('click', () => {
    setCodeWide(!state.codeWide);
    if (state.codeWide) $('code')?.focus({preventScroll: true});
  });
  $('code-focus')?.addEventListener('click', () => setCodeFocus(!state.codeFocus));
  $('layout-restore')?.addEventListener('click', () => setCodeFocus(false));
  const handle = $('code-resize');
  if (handle) {
    let dragging = false;
    handle.addEventListener('pointerdown', (e) => {
      dragging = true;
      // Drag only resizes code vs params inside the lab — never hide page sidebars.
      state.codeWide = true;
      state.codeFocus = false;
      syncLayoutChrome();
      labEl()?.classList.add('is-resizing');
      handle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    handle.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const lab = labEl();
      if (!lab) return;
      const rect = lab.getBoundingClientRect();
      const x = Math.min(rect.width - 180, Math.max(rect.width * 0.45, e.clientX - rect.left));
      const codePx = Math.round(x);
      const sidePx = Math.round(rect.width - x);
      lab.style.gridTemplateColumns = `minmax(0,${codePx}px) minmax(180px,${sidePx}px)`;
    });
    const stop = () => { dragging = false; labEl()?.classList.remove('is-resizing'); };
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  }
  if (scroll) {
    const ro = new ResizeObserver(() => syncCodeChrome());
    ro.observe(scroll);
  }
}

function localeTitle(obj) {
  return getLocale() === 'en' ? (obj.title_en || obj.title) : (obj.title || obj.title_en);
}
function localeSummary(obj) {
  return getLocale() === 'en' ? (obj.summary_en || obj.summary) : (obj.summary || obj.summary_en);
}
function localeLabel(obj) {
  return getLocale() === 'en' ? (obj.label_en || obj.label) : (obj.label || obj.label_en);
}

function loadUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
}
function saveUser(user) {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  state.user = user;
}

async function refreshCatalog() {
  const q = new URLSearchParams({robot: state.robot});
  if (state.user?.id) q.set('user', state.user.id);
  state.catalog = await api('/api/tutorial/catalog?' + q);
  renderSidebar();
  renderMega();
  updateDiscovery();
  updateTicker();
  if (state.progress || state.catalog.progress) {
    state.progress = state.catalog.progress || state.progress;
    renderUser();
  }
}

function renderUser() {
  const box = $('user-box');
  if (!state.user) {
    $('user-level').hidden = true;
    $('user-bar').hidden = true;
    return;
  }
  $('user-name').value = state.user.name;
  $('user-name').disabled = true;
  $('user-register').textContent = getLocale() === 'en' ? 'Switch' : '換人';
  const p = state.progress;
  if (p) {
    $('user-level').hidden = false;
    $('user-level').textContent = localeLabel(p.level) + ` · ${p.percent}%`;
    $('user-bar').hidden = false;
    $('user-bar').querySelector('i').style.width = p.percent + '%';
  }
  box.dataset.ready = '1';
}

function renderSidebar() {
  const nav = $('lesson-nav');
  if (!state.catalog) return;
  const prog = Object.fromEntries((state.progress?.lessons || []).map((l) => [l.lesson_id, l]));
  nav.innerHTML = state.catalog.categories.map((cat) => {
    if (!cat.lessons?.length) return '';
    return `<div class="tut-cat-label">${localeLabel(cat)}</div>` + cat.lessons.map((les) => {
      const st = prog[les.id]?.state || 'not_started';
      const label = st === 'completed' ? (getLocale() === 'en' ? 'done' : '已完成')
        : st === 'in_progress' ? (getLocale() === 'en' ? 'in progress' : '進行中') : '';
      return `<a class="tut-lesson-link ${st}${state.lessonId === les.id ? ' active' : ''}" href="#${les.id}" data-lesson="${les.id}">
        <strong>${localeTitle(les)}</strong>
        <span class="state">${label || (les.minutes ? les.minutes + ' min' : '')}</span>
      </a>`;
    }).join('');
  }).join('');
  nav.querySelectorAll('[data-lesson]').forEach((a) => a.addEventListener('click', (e) => {
    e.preventDefault();
    openLesson(a.dataset.lesson);
  }));
}

function bindMegaHover(root) {
  const mega = root || document.getElementById('nav-tutorial-mega');
  if (!mega || mega.dataset.boundHover) return;
  mega.dataset.boundHover = '1';
  let closeTimer = 0;
  const open = () => { clearTimeout(closeTimer); mega.classList.add('is-open'); };
  const scheduleClose = () => {
    clearTimeout(closeTimer);
    closeTimer = setTimeout(() => mega.classList.remove('is-open'), 280);
  };
  mega.addEventListener('pointerenter', open);
  mega.addEventListener('pointerleave', scheduleClose);
  mega.addEventListener('focusin', open);
  mega.addEventListener('focusout', (e) => { if (!mega.contains(e.relatedTarget)) scheduleClose(); });
  const trigger = mega.querySelector(':scope > a');
  trigger?.addEventListener('click', (e) => {
    // First click opens the panel; second click (when already open) follows the link.
    if (!mega.classList.contains('is-open')) {
      e.preventDefault();
      open();
    }
  });
}

function renderMega() {
  const panel = $('mega-panel');
  if (!panel || !state.catalog) return;
  panel.hidden = false;
  panel.innerHTML = `<div class="nav-mega-inner">
    <div class="nav-mega-head"><strong>Isaac Lab 3 Tutorial</strong>
    <div class="robot-toggle" role="group">${['leg','wheel'].map((r) =>
      `<button type="button" data-mega-robot="${r}" class="${state.robot===r?'active':''}" aria-pressed="${state.robot===r}">${r==='leg'?'雙足':'雙輪'}</button>`).join('')}</div></div>
    <div class="nav-mega-grid">${state.catalog.categories.map((cat) => `<div>
      <p class="nav-mega-cat">${localeLabel(cat)}</p>
      ${(cat.lessons||[]).map((les) => `<a class="nav-mega-card" href="/tutorial#${les.id}"><strong>${localeTitle(les)}</strong><small>${localeSummary(les).slice(0,90)}</small></a>`).join('') || '<small>—</small>'}
    </div>`).join('')}</div>
    <div class="nav-mega-foot"><span>${state.catalog.lesson_count||0} lessons</span><a class="text-link" href="/tutorial">開啟教學 ›</a></div>
  </div>`;
  panel.querySelectorAll('[data-mega-robot]').forEach((b) => b.addEventListener('click', () => setRobot(b.dataset.megaRobot)));
  bindMegaHover();
  const badge = $('nav-pending-badge');
  const n = state.catalog.proposals?.pending || 0;
  if (badge) { badge.hidden = !n; badge.textContent = String(n); }
}

async function updateTicker() {
  try {
    const data = await api('/api/tutorial/ticker');
    const scroll = $('ticker-scroll');
    const items = data.items?.length ? data.items : [{text: getLocale()==='en'?'No new suggestions yet.':'尚無新建議。'}];
    const html = items.map((it) => {
      if (it.proposal_id) return `<a href="#proposal-${it.proposal_id}" data-proposal="${it.proposal_id}">${escapeHtml(it.text)}</a>`;
      if (it.lesson_id) return `<a href="#${it.lesson_id}">${escapeHtml(it.text)}</a>`;
      return `<span>${escapeHtml(it.text)}</span>`;
    }).join('');
    scroll.innerHTML = html + html;
    scroll.querySelectorAll('[data-proposal]').forEach((a) => a.addEventListener('click', (e) => {
      e.preventDefault();
      showProposal(a.dataset.proposal);
    }));
    const count = $('ticker-count');
    const n = data.counts?.pending || 0;
    count.hidden = !n;
    count.textContent = n + (getLocale()==='en' ? ' pending' : ' 待審');
    const badge = $('nav-pending-badge');
    if (badge) { badge.hidden = !n; badge.textContent = String(n); }
  } catch {/* ticker is best-effort */}
}

function updateDiscovery() {
  const d = state.catalog?.discovery;
  if (!d) return;
  $('discovery-toggle').checked = !!d.enabled;
  const base = getLocale()==='en'
    ? 'New hits become pending proposals + ticker items. No LLM required.'
    : '新素材會進「待審提案」並出現在上排走馬燈；不需 LLM。';
  $('discovery-meta').textContent = d.enabled
    ? (getLocale()==='en'
      ? `On · every ${d.interval_minutes} min · last ${d.last_run || 'never'}. ${base}`
      : `已開啟 · 每 ${d.interval_minutes} 分鐘 · 上次 ${d.last_run || '尚未'}。${base}`)
    : (getLocale()==='en'
      ? `Off — click Scan now once, or enable for periodic search. ${base}`
      : `關閉中 — 可按「立刻掃描一次」，或勾選後定期搜尋。${base}`);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

async function setRobot(robot) {
  state.robot = robot;
  localStorage.setItem(ROBOT_KEY, robot);
  document.querySelectorAll('[data-robot]').forEach((b) => {
    const on = b.dataset.robot === robot;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on);
  });
  await refreshCatalog();
  if (state.lessonId) await openLesson(state.lessonId);
}

async function openLesson(id) {
  if (!state.user) { toast(getLocale()==='en' ? 'Register a name first.' : '請先註冊名字。'); $('user-name').focus(); return; }
  state.lessonId = id;
  location.hash = id;
  const lesson = await api(`/api/tutorial/lessons/${id}?robot=${state.robot}&user=${state.user.id}`);
  state.lesson = lesson;
  state.originalCode = lesson.code || '';
  await api(`/api/tutorial/users/${state.user.id}/lessons/${id}/start`, {robot: state.robot});
  $('welcome').hidden = true;
  $('lesson-view').hidden = false;
  $('lesson-cat').textContent = (lesson.category || '').toUpperCase();
  $('lesson-title').textContent = localeTitle(lesson);
  $('lesson-summary').textContent = localeSummary(lesson);
  $('lesson-runtime').textContent = lesson.runtime?.label || '';
  $('lesson-minutes').textContent = (lesson.minutes || '?') + ' min';
  $('lesson-md').innerHTML = renderMarkdown(lesson.markdown || '');
  state.codeWide = false;
  state.codeFocus = false;
  setCode(lesson.draft_code || lesson.code || '', {baseline: true});
  setCodeExpanded(false);
  renderParams(lesson);
  renderQuiz(lesson);
  renderRefs(lesson);
  document.querySelectorAll('.tut-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === 'read'));
  document.querySelectorAll('.tut-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === 'read'));
  renderSidebar();
  loadHistory();
  loadRuns();
  $('run-status').hidden = true;
  $('run-media').innerHTML = '';
  $('cancel-btn').hidden = true;
  $('resume-btn').hidden = true;
}

function renderParams(lesson) {
  const box = $('params');
  const last = lesson.last_params || {};
  box.innerHTML = (lesson.params || []).map((p) => {
    const val = last[p.key] ?? p.default;
    if (p.type === 'select') {
      return `<div class="tut-param"><label><span>${escapeHtml(p.label)}</span></label>
        <select data-param="${p.key}">${(p.options||[]).map((o) => `<option value="${escapeHtml(o)}" ${o===val?'selected':''}>${escapeHtml(o)}</option>`).join('')}</select>
        ${p.help ? `<small>${escapeHtml(p.help)}</small>` : ''}</div>`;
    }
    if (p.type === 'bool') {
      return `<div class="tut-param"><label><span>${escapeHtml(p.label)}</span>
        <input type="checkbox" data-param="${p.key}" ${val ? 'checked' : ''}></label>
        ${p.help ? `<small>${escapeHtml(p.help)}</small>` : ''}</div>`;
    }
    if (p.type === 'int' || p.type === 'float') {
      return `<div class="tut-param"><label><span>${escapeHtml(p.label)}</span><output data-out="${p.key}">${val}</output></label>
        <input type="range" data-param="${p.key}" min="${p.min??0}" max="${p.max??100}" step="${p.step??(p.type==='int'?1:0.1)}" value="${val}">
        ${p.help ? `<small>${escapeHtml(p.help)}</small>` : ''}</div>`;
    }
    return `<div class="tut-param"><label><span>${escapeHtml(p.label)}</span></label>
      <input type="text" data-param="${p.key}" value="${escapeHtml(val??'')}">
      ${p.help ? `<small>${escapeHtml(p.help)}</small>` : ''}</div>`;
  }).join('');
  box.querySelectorAll('input[type=range]').forEach((el) => el.addEventListener('input', () => {
    const out = box.querySelector(`[data-out="${el.dataset.param}"]`);
    if (out) out.textContent = el.value;
  }));
}

function collectParams() {
  const params = {};
  $('params').querySelectorAll('[data-param]').forEach((el) => {
    const key = el.dataset.param;
    if (el.type === 'checkbox') params[key] = el.checked;
    else if (el.type === 'range') params[key] = el.step && el.step.includes('.') ? parseFloat(el.value) : parseInt(el.value, 10);
    else params[key] = el.value;
  });
  return params;
}

function renderQuiz(lesson) {
  const box = $('quiz');
  $('quiz-result').textContent = '';
  if (!lesson.quiz?.length) { box.innerHTML = '<p class="tut-empty">這堂課沒有測驗。</p>'; return; }
  box.innerHTML = lesson.quiz.map((q, i) => `<div class="q" data-qid="${q.id}">
    <strong>${i + 1}. ${escapeHtml(q.question)}</strong>
    ${(q.choices||[]).map((c, j) => `<label><input type="radio" name="${q.id}" value="${j}"> <span>${escapeHtml(c)}</span></label>`).join('')}
  </div>`).join('');
}

function renderRefs(lesson) {
  const box = $('refs');
  box.innerHTML = (lesson.references || []).map((r) =>
    `<li><a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title)}<small>${escapeHtml(r.type || 'web')} · ${escapeHtml(r.url)}</small></a></li>`
  ).join('') || '<li class="tut-empty">無參考資料</li>';
}

function runIsBusy(run) {
  return !!run && ['queued', 'starting', 'running'].includes(run.state);
}

function setRunBusy(busy, run = null) {
  state.runBusy = !!busy;
  const btn = $('run-btn');
  if (!btn) return;
  btn.disabled = !!busy;
  if (!busy) {
    btn.textContent = getLocale() === 'en' ? 'Run' : '執行';
    return;
  }
  const label = {
    queued: getLocale() === 'en' ? 'Queued…' : '排隊中…',
    starting: getLocale() === 'en' ? 'Starting…' : '啟動中…',
    running: getLocale() === 'en' ? 'Running…' : '執行中…',
  };
  btn.textContent = label[run?.state] || (getLocale() === 'en' ? 'Running…' : '執行中…');
}

function statusLabel(state) {
  const zh = {queued: '排隊中', starting: '啟動中', running: '執行中', complete: '完成', failed: '失敗', cancelled: '已取消', blocked: '已封鎖', interrupted: '中斷'};
  const en = {queued: 'queued', starting: 'starting', running: 'running', complete: 'complete', failed: 'failed', cancelled: 'cancelled', blocked: 'blocked', interrupted: 'interrupted'};
  return (getLocale() === 'en' ? en : zh)[state] || state;
}

async function startRun() {
  if (!state.user || !state.lessonId) return;
  if (state.runBusy) {
    toast(getLocale() === 'en' ? 'A run is already in progress.' : '已有執行在進行中，請先等待或取消。');
    return;
  }
  setRunBusy(true, {state: 'queued'});
  try {
    const params = collectParams();
    const code = $('code').value;
    await api(`/api/tutorial/users/${state.user.id}/lessons/${state.lessonId}/draft`, {code, params});
    const run = await api('/api/tutorial/runs', {user: state.user.id, lesson: state.lessonId, robot: state.robot, params, code});
    state.runId = run.id;
    showRun(run);
    pollRun();
  } catch (err) {
    setRunBusy(false);
    throw err;
  }
}

function showRun(run) {
  const el = $('run-status');
  el.hidden = false;
  const p = run.progress || {};
  const busy = runIsBusy(run);
  setRunBusy(busy, run);
  const pct = p.max_iterations
    ? Math.min(100, Math.round(100 * (p.iteration || 0) / p.max_iterations))
    : (run.state === 'complete' ? 100 : run.state === 'running' ? 35 : run.state === 'starting' ? 12 : 4);
  const logLines = Array.isArray(run.log_tail) ? run.log_tail : String(run.log_tail || '').split('\n').filter(Boolean);
  const statusLines = [];
  if (run.wait_reason) statusLines.push(run.wait_reason);
  if (run.state === 'queued' && !run.wait_reason) statusLines.push(getLocale() === 'en' ? 'Queued — waiting for a free worker…' : '已排入佇列，等待啟動…');
  if (run.state === 'starting' && !logLines.length) statusLines.push(getLocale() === 'en' ? 'Launching Isaac Lab (uv / importers)… first start can take a minute.' : '正在啟動 Isaac Lab（uv / importers）… 第一次可能要一分鐘。');
  if (p.phase) statusLines.push(`phase: ${p.phase}` + (p.message ? ` — ${p.message}` : ''));
  if (p.iteration != null && p.max_iterations) statusLines.push(`progress: ${p.iteration}/${p.max_iterations}`);
  if (run.error) statusLines.push(run.error);
  const shownLog = [...statusLines, ...logLines].slice(-40);
  state.lastRun = {
    id: run.id,
    state: run.state,
    error: run.error || null,
    log_tail: logLines.slice(-60).join('\n'),
    runtime: run.runtime?.label || null,
  };
  el.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><strong>${escapeHtml(statusLabel(run.state))}</strong><span>${escapeHtml(run.runtime?.label || '')}</span></div>
    <div style="font-size:10px;color:#8a8a92;margin-top:4px">${escapeHtml(run.id.slice(0, 8))} · pid ${run.pid || '—'}</div>
    <progress max="100" value="${pct}"></progress>
    <div>${escapeHtml(p.message || p.phase || run.wait_reason || run.error || (busy ? (getLocale()==='en'?'Working…':'進行中…') : ''))}</div>
    <div class="log">${shownLog.length ? shownLog.map(escapeHtml).join('\n') : (getLocale()==='en'?'(no log yet)':'(尚無 log — 啟動後會出現在這裡)')}</div>`;
  $('cancel-btn').hidden = !busy;
  $('resume-btn').hidden = !run.can_resume;
  const media = $('run-media');
  media.innerHTML = '';
  if (run.files?.includes('result.mp4')) {
    media.innerHTML = `<video controls src="/media/tutorial/${run.id}/result.mp4"></video>`;
  } else if (run.files?.includes('rollout.json')) {
    media.innerHTML = `<p style="font-size:11px;color:#666;margin-top:8px"><a class="text-link" href="/media/tutorial/${run.id}/rollout.json" target="_blank">rollout.json</a> — 可在瀏覽器重播（Three.js）</p>`;
  }
  if (run.result) {
    el.insertAdjacentHTML('beforeend', `<pre style="margin-top:8px;font-size:10px;overflow:auto">${escapeHtml(JSON.stringify(run.result, null, 2).slice(0, 1200))}</pre>`);
  }
}

async function pollRun() {
  clearInterval(state.runPoll);
  if (!state.runId) return;
  const tick = async () => {
    try {
      const run = await api('/api/tutorial/runs/' + state.runId);
      showRun(run);
      if (['complete','failed','cancelled','blocked','interrupted'].includes(run.state)) {
        clearInterval(state.runPoll);
        state.runPoll = null;
        setRunBusy(false);
        loadRuns();
        await refreshCatalog();
      }
    } catch {
      clearInterval(state.runPoll);
      state.runPoll = null;
      setRunBusy(false);
    }
  };
  await tick();
  state.runPoll = setInterval(tick, 1200);
}

async function loadRuns() {
  if (!state.user) return;
  const data = await api(`/api/tutorial/runs?user=${state.user.id}` + (state.lessonId ? `&lesson=${state.lessonId}` : ''));
  const box = $('my-runs');
  const runs = (data.runs || []).slice(0, 8);
  if (!runs.length) { box.innerHTML = `<p class="tut-empty" style="padding:12px;font-size:11px">${getLocale()==='en'?'No runs yet.':'尚無執行紀錄。'}</p>`; return; }
  box.innerHTML = runs.map((r) => `<a class="tut-lesson-link" href="#run-${r.id}" data-run="${r.id}">
    <strong>${escapeHtml(r.lesson_title || r.lesson_id)}</strong>
    <span class="state">${escapeHtml(r.state)} · ${escapeHtml(r.robot)}</span></a>`).join('');
  box.querySelectorAll('[data-run]').forEach((a) => a.addEventListener('click', async (e) => {
    e.preventDefault();
    state.runId = a.dataset.run;
    const run = await api('/api/tutorial/runs/' + state.runId);
    document.querySelectorAll('.tut-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === 'lab'));
    document.querySelectorAll('.tut-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === 'lab'));
    showRun(run);
    if (['queued','starting','running'].includes(run.state)) pollRun();
  }));
}

async function loadProposals() {
  const data = await api('/api/tutorial/proposals');
  const box = $('proposals-box');
  const items = data.proposals || [];
  if (!items.length) { box.innerHTML = `<p style="font-size:11px;color:#8a8a92">${getLocale()==='en'?'No proposals.':'尚無提案。'}</p>`; return; }
  box.innerHTML = items.slice(0, 12).map((p) => `<div class="item">
    <strong>${escapeHtml(p.title)}</strong>
    <span style="color:#8a8a92">${escapeHtml(p.status)} · ${escapeHtml(p.category || '')}</span>
    <div class="row"><button type="button" class="text-link" data-show="${p.id}">檢視</button>
      ${p.status==='pending'?`<button type="button" class="text-link" data-approve="${p.id}">同意</button>`:''}
      ${p.status==='approved'?`<button type="button" class="text-link" data-prompt="${p.id}">Cursor prompt</button>`:''}
    </div></div>`).join('');
  box.querySelectorAll('[data-show]').forEach((b) => b.addEventListener('click', () => showProposal(b.dataset.show)));
  box.querySelectorAll('[data-approve]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/tutorial/proposals/${b.dataset.approve}/approve`, {});
    toast('已同意'); await loadProposals(); await updateTicker();
  }));
  box.querySelectorAll('[data-prompt]').forEach((b) => b.addEventListener('click', () => showProposal(b.dataset.prompt, true)));
}

async function showProposal(id, showPrompt) {
  const p = await api('/api/tutorial/proposals/' + id);
  state.pendingProposal = p;
  $('proposal-title').textContent = p.meta?.title || p.id;
  $('proposal-body').innerHTML = renderMarkdown(p.markdown || '');
  $('proposal-cursor').hidden = true;
  $('proposal-dialog').showModal();
  if (showPrompt) {
    const data = await api(`/api/tutorial/proposals/${id}/cursor-prompt`);
    $('proposal-cursor').hidden = false;
    $('proposal-cursor').textContent = data.prompt;
  }
}

function llmHowto(status) {
  const rt = status.runtimes || {};
  const en = getLocale() === 'en';
  const steps = [];
  const hasRuntime = !!(rt.llama_cpp?.available || rt.vllm?.available || rt.transformers?.available || rt.external?.available);
  const hasModel = (status.models || []).some((m) => m.local);
  const ready = status.chat_ready;
  if (ready) {
    steps.push(en ? 'Hermes is online — chat in the right panel.' : 'Hermes 已上線，可在右側對話。');
    return steps;
  }
  steps.push(en
    ? 'Offline = no local LLM server is running yet (GPU is OK).'
    : '離線＝尚未啟動本機 LLM 伺服器（GPU 已偵測到，但還不能推理）。');
  if (!hasRuntime) {
    steps.push(en
      ? 'Install a runtime, e.g. pip install "llama-cpp-python[server]" (or put llama-server on PATH), then click Refresh.'
      : '先裝推理 runtime：pip install "llama-cpp-python[server]"（或把 llama-server 加入 PATH），再按「重新偵測」。');
  } else {
    steps.push(en ? 'Runtime detected.' : '已偵測到 runtime。');
  }
  if (!hasModel) {
    steps.push(en
      ? 'Download Hermes 3 · 8B Q4_K_M below (~4.9 GB). Progress shows here.'
      : '下方按「下載」取得 Hermes 3 · 8B Q4_K_M（約 4.9 GB），進度會顯示在此。');
  } else {
    steps.push(en ? 'Model is on disk — click Start.' : '模型已在本機 — 按「啟動」。');
  }
  steps.push(en
    ? 'Optional: export Q1_LLM_BASE_URL=http://127.0.0.1:PORT/v1 to use an existing OpenWebUI / vLLM.'
    : '也可設 Q1_LLM_BASE_URL=http://127.0.0.1:埠/v1 接既有 OpenWebUI／vLLM。');
  steps.push(en
    ? 'Course search does not need the LLM: enable “搜尋進修課程” or click Scan now.'
    : '搜尋新課程不需 LLM：左側勾「搜尋進修課程」或按「立刻掃描一次」即可。');
  return steps;
}

async function refreshLlm() {
  try {
    state.llm = await api('/api/llm/status');
  } catch (e) {
    $('llm-status').textContent = String(e.message || e);
    return;
  }
  const gpu = state.llm.gpu;
  const ready = state.llm.chat_ready;
  const rt = state.llm.runtimes || {};
  $('agent-mode').textContent = ready ? 'Hermes' : (getLocale()==='en'?'Offline':'離線');
  $('agent-mode').classList.toggle('on', !!ready);
  const runtimeBits = [
    rt.llama_cpp?.available ? 'llama.cpp' : null,
    rt.vllm?.available ? 'vLLM' : null,
    rt.transformers?.available ? 'transformers' : null,
    rt.external?.available ? 'external' : null,
    rt.huggingface_hub?.cli || rt.huggingface_hub?.available ? 'hf-cli' : null,
  ].filter(Boolean);
  $('llm-status').textContent = (gpu.available
    ? `${gpu.gpus?.[0]?.name || 'GPU'} · ${Math.round((gpu.gpus?.[0]?.memory_total_mb||0)/1024)} GB`
    : (gpu.reason || 'No GPU'))
    + ` · runtime: ${runtimeBits.join('/') || 'none'}`
    + ` · server: ${state.llm.server?.state || 'stopped'}`;
  const howto = $('llm-howto');
  if (howto) howto.innerHTML = llmHowto(state.llm).map((s) => `<li>${escapeHtml(s)}</li>`).join('');
  $('llm-stop').hidden = !(state.llm.server?.state === 'running' || state.llm.server?.state === 'starting');
  const box = $('llm-models');
  const canStart = !!(rt.llama_cpp?.available || rt.vllm?.available || rt.transformers?.available);
  box.innerHTML = (state.llm.models || []).map((m) => {
    const dl = m.download;
    const pct = dl?.percent ?? (m.local ? 100 : 0);
    const startDisabled = !(m.runnable_backends || []).length || !canStart;
    return `<div class="tut-model"><div class="row"><strong>${escapeHtml(m.label)}</strong>
      <span>${m.local ? (getLocale()==='en'?'local':'已有') : (dl?.state || '—')}</span></div>
      ${!m.local && dl ? `<progress max="100" value="${pct||0}"></progress>` : ''}
      <div class="row">
        ${m.local
          ? `<button type="button" class="button secondary" style="font-size:10px;min-height:28px;padding:4px 10px" data-start="${m.id}" ${startDisabled?'disabled':''} title="${startDisabled?(getLocale()==='en'?'Install llama-cpp-python or llama-server first':'請先安裝 llama-cpp-python 或 llama-server'):''}">啟動</button>`
          : `<button type="button" class="button secondary" style="font-size:10px;min-height:28px;padding:4px 10px" data-dl="${m.id}">下載</button>`}
        <small style="color:#8a8a92">${escapeHtml(m.note||'')}</small>
      </div></div>`;
  }).join('');
  box.querySelectorAll('[data-dl]').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api('/api/llm/download', {model_id: b.dataset.dl});
      toast(getLocale()==='en'?'Download started':'開始下載');
      pollDownloads();
    } catch (e) { toast(e.message); }
  }));
  box.querySelectorAll('[data-start]').forEach((b) => b.addEventListener('click', async () => {
    try {
      await api('/api/llm/server', {model_id: b.dataset.start});
      toast(getLocale()==='en'?'Starting server…':'啟動伺服器中…');
      pollDownloads();
    } catch (e) { toast(e.message); }
  }));
}

function pollDownloads() {
  clearInterval(state.dlPoll);
  state.dlPoll = setInterval(async () => {
    await refreshLlm();
    const busy = (state.llm?.models || []).some((m) => m.download && ['queued','downloading'].includes(m.download.state))
      || state.llm?.server?.state === 'starting';
    if (!busy) clearInterval(state.dlPoll);
  }, 2000);
}

function appendChat(role, text, extra) {
  const chat = $('chat');
  const div = document.createElement('div');
  div.className = 'tut-msg ' + role;
  if (extra?.reasoning) {
    const r = document.createElement('span');
    r.className = 'reason';
    r.textContent = extra.reasoning;
    div.appendChild(r);
  }
  const body = document.createElement('span');
  body.className = 'body';
  body.textContent = text;
  div.appendChild(body);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return {div, body};
}

async function loadHistory() {
  if (!state.user || !state.lessonId) return;
  const data = await api(`/api/agent/history?user=${state.user.id}&lesson=${state.lessonId}`);
  $('chat').innerHTML = '';
  (data.messages || []).forEach((m) => {
    if (m.role === 'proposal') return;
    appendChat(m.role === 'user' ? 'user' : m.role === 'assistant' ? 'assistant' : 'system', m.content || '', {reasoning: m.reasoning});
  });
}

async function sendChat() {
  if (!state.user || !state.lessonId) { toast(getLocale()==='en'?'Open a lesson first.':'請先開啟課程。'); return; }
  const input = $('chat-input');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  appendChat('user', message);
  const {div, body} = appendChat('assistant', '');
  const reasonEl = document.createElement('span');
  reasonEl.className = 'reason';
  reasonEl.hidden = true;
  div.insertBefore(reasonEl, body);
  $('chat-send').disabled = true;
  try {
    const res = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        user: state.user.id,
        lesson: state.lessonId,
        message,
        context: {
          robot: state.robot,
          params: collectParams(),
          code: $('code').value,
          last_run: state.lastRun || null,
        },
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw Error(err.error || 'chat failed');
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      const parts = buf.split('\n\n');
      buf = parts.pop() || '';
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        const raw = line.slice(5).trim();
        if (raw === '[DONE]') continue;
        let ev;
        try { ev = JSON.parse(raw); } catch { continue; }
        if (ev.type === 'status') {
          $('agent-mode').textContent = ev.mode === 'hermes' ? 'Hermes' : (getLocale()==='en'?'Offline':'離線');
          $('agent-mode').classList.toggle('on', ev.mode === 'hermes');
        } else if (ev.type === 'reasoning') {
          reasonEl.hidden = false;
          reasonEl.textContent += ev.text || '';
        } else if (ev.type === 'token') {
          body.textContent += ev.text || '';
        } else if (ev.type === 'tool_call') {
          const t = document.createElement('span');
          t.className = 'tool';
          t.textContent = `⚙ ${ev.name}`;
          div.insertBefore(t, body);
        } else if (ev.type === 'code_proposal') {
          state.pendingDiff = ev;
          $('diff-explain').textContent = ev.explanation || '';
          $('diff-body').textContent = ev.diff || ev.code || '';
          $('diff-dialog').showModal();
        } else if (ev.type === 'params') {
          Object.entries(ev.params || {}).forEach(([k, v]) => {
            const el = $('params').querySelector(`[data-param="${k}"]`);
            if (!el) return;
            if (el.type === 'checkbox') el.checked = !!v;
            else el.value = v;
            const out = $('params').querySelector(`[data-out="${k}"]`);
            if (out) out.textContent = v;
          });
        } else if (ev.type === 'run') {
          state.runId = ev.run?.id;
          if (state.runId) { showRun(ev.run); pollRun(); }
        } else if (ev.type === 'proposal') {
          loadProposals(); updateTicker();
        } else if (ev.type === 'error') {
          body.textContent += '\n' + (ev.text || '');
        }
      }
      $('chat').scrollTop = $('chat').scrollHeight;
    }
  } catch (e) {
    body.textContent = String(e.message || e);
  } finally {
    $('chat-send').disabled = false;
  }
}

function wire() {
  initI18n();
  document.querySelectorAll('.lang-switch [data-lang]').forEach((b) => b.addEventListener('click', () => {
    setLocale(b.dataset.lang);
    applyI18n();
    document.querySelectorAll('.lang-switch [data-lang]').forEach((x) => {
      const on = x.dataset.lang === getLocale();
      x.classList.toggle('active', on);
      x.setAttribute('aria-pressed', on);
    });
    if (state.lesson) openLesson(state.lessonId);
    else { renderSidebar(); renderMega(); renderUser(); }
  }));

  document.querySelectorAll('.tut-side [data-robot]').forEach((b) => b.addEventListener('click', () => setRobot(b.dataset.robot)));

  $('user-register').addEventListener('click', async () => {
    if ($('user-name').disabled) {
      $('user-name').disabled = false;
      $('user-name').value = '';
      localStorage.removeItem(USER_KEY);
      state.user = null;
      state.progress = null;
      renderUser();
      $('user-register').textContent = getLocale()==='en'?'Enter':'進入';
      return;
    }
    try {
      const data = await api('/api/tutorial/users', {name: $('user-name').value.trim()});
      saveUser(data.user);
      state.progress = data.progress;
      renderUser();
      await refreshCatalog();
      toast(getLocale()==='en' ? `Welcome, ${data.user.name}` : `歡迎，${data.user.name}`);
      if (location.hash.slice(1)) openLesson(location.hash.slice(1));
    } catch (e) { toast(e.message); }
  });

  document.querySelectorAll('.tut-tabs button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.tut-tabs button').forEach((x) => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tut-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === b.dataset.tab));
    if (b.dataset.tab === 'lab') {
      requestAnimationFrame(() => { syncCodeChrome(); $('code')?.focus({preventScroll: true}); });
    }
  }));

  $('code-reset').addEventListener('click', () => {
    setCode(state.originalCode, {baseline: true});
    setCodeExpanded(false);
    toast(getLocale()==='en'?'Restored original example.py':'已還原原文');
  });
  $('code-save').addEventListener('click', async () => {
    if (!state.user || !state.lessonId) return;
    await api(`/api/tutorial/users/${state.user.id}/lessons/${state.lessonId}/draft`, {code: $('code').value, params: collectParams()});
    state.baselineCode = $('code').value;
    toast(getLocale()==='en'?'Draft saved':'草稿已存');
  });
  wireCodeEditor();
  $('run-btn').addEventListener('click', () => startRun().catch((e) => toast(e.message)));
  $('cancel-btn').addEventListener('click', async () => {
    if (!state.runId) return;
    $('cancel-btn').disabled = true;
    try {
      const run = await api(`/api/tutorial/runs/${state.runId}/cancel`, {});
      showRun(run);
      setRunBusy(false);
    } finally {
      $('cancel-btn').disabled = false;
    }
  });
  $('resume-btn').addEventListener('click', async () => {
    if (!state.runId || state.runBusy) return;
    const run = await api(`/api/tutorial/runs/${state.runId}/resume`, {params: collectParams()});
    showRun(run); pollRun();
  });
  $('quiz-submit').addEventListener('click', async () => {
    if (!state.user || !state.lessonId) return;
    const answers = {};
    (state.lesson.quiz || []).forEach((q) => {
      const checked = document.querySelector(`input[name="${q.id}"]:checked`);
      if (checked) answers[q.id] = parseInt(checked.value, 10);
    });
    const result = await api(`/api/tutorial/users/${state.user.id}/lessons/${state.lessonId}/quiz`, {answers});
    state.progress = result.progress;
    renderUser();
    renderSidebar();
    $('quiz-result').textContent = (getLocale()==='en' ? `Score ${result.score} · ${result.passed ? 'passed' : 'try again'}` : `分數 ${result.score} · ${result.passed ? '通過' : '再試一次'}`);
    result.results.forEach((r) => {
      const q = document.querySelector(`.q[data-qid="${r.id}"]`);
      if (!q) return;
      q.classList.toggle('ok', r.correct);
      q.classList.toggle('bad', !r.correct);
      let ex = q.querySelector('.explain');
      if (!ex) { ex = document.createElement('div'); ex.className = 'explain'; q.appendChild(ex); }
      ex.textContent = r.explain || '';
    });
  });

  $('chat-send').addEventListener('click', sendChat);
  $('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  $('chat-clear').addEventListener('click', async () => {
    if (!state.user || !state.lessonId) return;
    await api('/api/agent/clear', {user: state.user.id, lesson: state.lessonId});
    $('chat').innerHTML = '';
  });

  $('llm-refresh').addEventListener('click', () => refreshLlm());
  $('llm-stop').addEventListener('click', async () => { await api('/api/llm/server', {action: 'stop'}); refreshLlm(); });
  $('discovery-toggle').addEventListener('change', async () => {
    await api('/api/tutorial/discovery', {enabled: $('discovery-toggle').checked});
    await refreshCatalog();
  });
  $('discovery-now').addEventListener('click', async () => {
    await api('/api/tutorial/discovery', {run_now: true});
    toast(getLocale()==='en'?'Scan started':'開始掃描');
    setTimeout(() => { loadProposals(); updateTicker(); refreshCatalog(); }, 4000);
  });

  $('proposal-close').addEventListener('click', () => $('proposal-dialog').close());
  $('proposal-approve').addEventListener('click', async () => {
    if (!state.pendingProposal) return;
    await api(`/api/tutorial/proposals/${state.pendingProposal.id}/approve`, {});
    toast('已同意'); $('proposal-dialog').close(); loadProposals(); updateTicker();
  });
  $('proposal-reject').addEventListener('click', async () => {
    if (!state.pendingProposal) return;
    await api(`/api/tutorial/proposals/${state.pendingProposal.id}/reject`, {});
    $('proposal-dialog').close(); loadProposals(); updateTicker();
  });
  $('proposal-prompt').addEventListener('click', async () => {
    if (!state.pendingProposal) return;
    const data = await api(`/api/tutorial/proposals/${state.pendingProposal.id}/cursor-prompt`);
    $('proposal-cursor').hidden = false;
    $('proposal-cursor').textContent = data.prompt;
    try { await navigator.clipboard.writeText(data.prompt); toast('已複製'); } catch { toast('請手動複製'); }
  });
  $('diff-close').addEventListener('click', () => $('diff-dialog').close());
  $('diff-dismiss').addEventListener('click', () => $('diff-dialog').close());
  $('diff-accept').addEventListener('click', async () => {
    if (!state.pendingDiff || !state.user) return;
    await api('/api/agent/accept', {user: state.user.id, lesson: state.lessonId, proposal_id: state.pendingDiff.proposal_id});
    const before = $('code').value;
    const raw = state.pendingDiff.code || before;
    const annotated = annotateChangedLines(before, raw, 'hermes', state.pendingDiff.explanation || '');
    setCode(annotated, {expand: true});
    state.baselineCode = annotated;
    document.querySelectorAll('.tut-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === 'lab'));
    document.querySelectorAll('.tut-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === 'lab'));
    $('diff-dialog').close();
    toast(getLocale()==='en'?'Hermes edit applied — review yellow lines, then Run':'已採用 Hermes 修改（黃線），請檢查後按「執行」');
  });

  window.addEventListener('hashchange', () => {
    const id = location.hash.replace(/^#/, '');
    if (id && !id.startsWith('run-') && !id.startsWith('proposal-') && id !== state.lessonId) openLesson(id);
  });
}

async function boot() {
  wire();
  state.user = loadUser();
  document.querySelectorAll('[data-robot]').forEach((b) => {
    const on = b.dataset.robot === state.robot;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on);
  });
  try {
    await refreshCatalog();
    await refreshLlm();
    await loadProposals();
    if (state.user?.id) {
      const data = await api(`/api/tutorial/users/${state.user.id}`);
      state.user = data.user;
      state.progress = data.progress;
      saveUser(data.user);
      renderUser();
      loadRuns();
    }
    const hash = location.hash.replace(/^#/, '');
    if (hash && !hash.startsWith('run-') && !hash.startsWith('proposal-') && state.user) await openLesson(hash);
  } catch (e) {
    $('lesson-nav').innerHTML = `<p class="tut-empty">${escapeHtml(e.message)}</p>`;
  }
  setInterval(updateTicker, 30000);
}

boot();
