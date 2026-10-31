/** Home-page hooks for the Isaac Lab 3 tutorial: mega-menu, Hermes ticker, resume card. */
import {getLocale, L} from './i18n.js';

const USER_KEY = 'q1-tutorial-user';
const DISMISS_KEY = 'q1-tutorial-resume-dismiss';

function localeTitle(obj) {
  return getLocale() === 'en' ? (obj.title_en || obj.title) : (obj.title || obj.title_en);
}
function localeLabel(obj) {
  return getLocale() === 'en' ? (obj.label_en || obj.label) : (obj.label || obj.label_en);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw Error('HTTP ' + r.status);
  return r.json();
}

function ensureStyles() {
  if (document.querySelector('link[href="/tutorial.css"]')) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/tutorial.css';
  document.head.appendChild(link);
}

function injectNavLink() {
  const nav = document.querySelector('header.nav nav');
  if (!nav || document.getElementById('nav-tutorial-mega')) return;
  const wrap = document.createElement('div');
  wrap.className = 'nav-mega';
  wrap.id = 'nav-tutorial-mega';
  wrap.innerHTML = `<a href="/tutorial" class="nav-highlight" id="nav-tutorial-link">Isaac Lab 3 <span class="badge" id="nav-pending-badge" hidden>0</span></a>
    <div class="nav-mega-panel" id="mega-panel" hidden></div>`;
  const studio = nav.querySelector('a[href="#studio"]');
  if (studio) nav.insertBefore(wrap, studio);
  else nav.appendChild(wrap);
}

function injectTicker() {
  if (document.getElementById('home-ticker')) return;
  const bar = document.createElement('div');
  bar.className = 'tut-ticker';
  bar.id = 'home-ticker';
  bar.innerHTML = `<span class="label">Hermes</span><div class="track"><div class="scroll" id="home-ticker-scroll"><span>Isaac Lab 3 tutorial</span></div></div>
    <span class="count" id="home-ticker-count" hidden>0</span>`;
  const announcement = document.querySelector('.announcement');
  if (announcement) announcement.insertAdjacentElement('afterend', bar);
  else document.querySelector('header.nav')?.insertAdjacentElement('afterend', bar);
}

function bindMegaHover(mega) {
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
    if (!mega.classList.contains('is-open')) {
      e.preventDefault();
      open();
    }
  });
}

function renderMega(catalog, robot) {
  const panel = document.getElementById('mega-panel');
  if (!panel || !catalog) return;
  panel.hidden = false;
  panel.innerHTML = `<div class="nav-mega-inner">
    <div class="nav-mega-head"><strong>Isaac Lab 3 Tutorial</strong>
    <div class="robot-toggle">${['leg','wheel'].map((r) =>
      `<a class="button secondary" style="font-size:11px;min-height:28px;padding:4px 10px${robot===r?';background:#fff;box-shadow:0 1px 3px #0001':''}" href="/tutorial?robot=${r}">${r==='leg'?'雙足':'雙輪'}</a>`).join('')}</div></div>
    <div class="nav-mega-grid">${(catalog.categories||[]).map((cat) => `<div>
      <p class="nav-mega-cat">${escapeHtml(localeLabel(cat))}</p>
      ${(cat.lessons||[]).map((les) => `<a class="nav-mega-card" href="/tutorial#${les.id}"><strong>${escapeHtml(localeTitle(les))}</strong><small>${escapeHtml((getLocale()==='en'?les.summary_en:les.summary)||'').slice(0,90)}</small></a>`).join('')||'<small>—</small>'}
    </div>`).join('')}</div>
    <div class="nav-mega-foot"><span>${catalog.lesson_count||0} lessons</span><a class="text-link" href="/tutorial">開啟教學 ›</a></div>
  </div>`;
  bindMegaHover(document.getElementById('nav-tutorial-mega'));
  const badge = document.getElementById('nav-pending-badge');
  const n = catalog.proposals?.pending || 0;
  if (badge) { badge.hidden = !n; badge.textContent = String(n); }
}

async function renderTicker() {
  try {
    const data = await get('/api/tutorial/ticker');
    const scroll = document.getElementById('home-ticker-scroll');
    if (!scroll) return;
    const items = data.items?.length ? data.items : [{text: getLocale()==='en'?'Enable Hermes discovery in the tutorial.':'到 Tutorial 開啟 Hermes 搜尋進修。'}];
    const html = items.map((it) => {
      if (it.proposal_id) return `<a href="/tutorial#proposal-${it.proposal_id}">${escapeHtml(it.text)}</a>`;
      if (it.lesson_id) return `<a href="/tutorial#${it.lesson_id}">${escapeHtml(it.text)}</a>`;
      return `<span>${escapeHtml(it.text)}</span>`;
    }).join('');
    scroll.innerHTML = html + html;
    const count = document.getElementById('home-ticker-count');
    const n = data.counts?.pending || 0;
    if (count) { count.hidden = !n; count.textContent = n + (getLocale()==='en'?' pending':' 待審'); }
    const badge = document.getElementById('nav-pending-badge');
    if (badge) { badge.hidden = !n; badge.textContent = String(n); }
  } catch {/* home ticker is best-effort */}
}

function showResume(progress) {
  if (!progress?.last?.lesson_id) return;
  if (sessionStorage.getItem(DISMISS_KEY) === progress.last.lesson_id) return;
  if (document.getElementById('resume-card')) return;
  const lesson = (progress.lessons || []).find((l) => l.lesson_id === progress.last.lesson_id);
  const title = lesson ? (getLocale()==='en' ? lesson.title_en || lesson.title : lesson.title) : progress.last.lesson_id;
  const card = document.createElement('div');
  card.className = 'resume-card';
  card.id = 'resume-card';
  card.innerHTML = `<p class="eyebrow">CONTINUE LEARNING</p>
    <strong>${escapeHtml(title)}</strong>
    <p>${getLocale()==='en'
      ? `Resume as ${progress.name || 'you'} · ${progress.percent || 0}% complete · ${localeLabel(progress.level)}`
      : `繼續以 ${progress.name || '你'} 的進度 · 完成 ${progress.percent || 0}% · ${localeLabel(progress.level)}`}</p>
    <div class="actions">
      <a class="button primary" href="/tutorial#${progress.last.lesson_id}" style="min-height:36px;padding:8px 14px">${getLocale()==='en'?'Resume':'繼續上課'}</a>
      <button type="button" class="dismiss" id="resume-dismiss" aria-label="dismiss">×</button>
    </div>`;
  document.body.appendChild(card);
  document.getElementById('resume-dismiss').onclick = () => {
    sessionStorage.setItem(DISMISS_KEY, progress.last.lesson_id);
    card.remove();
  };
}

export async function initTutorialHome() {
  ensureStyles();
  injectNavLink();
  injectTicker();
  let user = null;
  try { user = JSON.parse(localStorage.getItem(USER_KEY)); } catch {}
  try {
    const q = new URLSearchParams({robot: 'leg'});
    if (user?.id) q.set('user', user.id);
    const catalog = await get('/api/tutorial/catalog?' + q);
    renderMega(catalog, 'leg');
    if (catalog.progress?.last) showResume(catalog.progress);
  } catch {/* store may be static-hosted without tutorial API */}
  await renderTicker();
  setInterval(renderTicker, 45000);
}
