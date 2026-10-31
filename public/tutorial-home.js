/** Home-page hooks for Q1 School: nav link + resume card (ticker only on /tutorial). */
import {getLocale, t, applyI18n} from './i18n.js';

const USER_KEY = 'q1-tutorial-user';
const DISMISS_KEY = 'q1-tutorial-resume-dismiss';

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
  if (!nav || document.getElementById('nav-tutorial-link')) return;
  // Plain link only — no mega dropdown; CES alone keeps the red light.
  const link = document.createElement('a');
  link.href = '/tutorial';
  link.id = 'nav-tutorial-link';
  link.dataset.i18n = 'nav_school';
  link.textContent = t('nav_school');
  const sdk = nav.querySelector('#nav-sdk') || nav.querySelector('a[href="#sdk"]');
  if (sdk) sdk.insertAdjacentElement('afterend', link);
  else nav.appendChild(link);
  applyI18n(link);
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
  let user = null;
  try { user = JSON.parse(localStorage.getItem(USER_KEY)); } catch {}
  try {
    const q = new URLSearchParams({robot: 'leg'});
    if (user?.id) q.set('user', user.id);
    const catalog = await get('/api/tutorial/catalog?' + q);
    if (catalog.progress?.last) showResume(catalog.progress);
  } catch {/* store may be static-hosted without tutorial API */}
  window.addEventListener('q1:locale', () => {
    applyI18n(document.getElementById('nav-tutorial-link') || document);
  });
}
