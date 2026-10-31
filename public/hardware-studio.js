/** Interactive Q1 hardware / explode studio for tutorial l09. */
import {createRobotViewer} from './robot-viewer.js';
import {getLocale} from './i18n.js';

const VIDEO = '/assets/tutorial/l09-hardware-explode/hardware.webm';

function $(sel, root = document) {
  return typeof sel === 'string' ? root.querySelector(sel) : sel;
}

function en() {
  return getLocale() === 'en';
}

function readParam(key, fallback) {
  const el = document.querySelector(`#params [data-param="${key}"]`);
  if (!el) return fallback;
  if (el.type === 'checkbox') return el.checked;
  if (el.type === 'range') {
    const n = el.step && String(el.step).includes('.') ? parseFloat(el.value) : parseInt(el.value, 10);
    return Number.isFinite(n) ? n : fallback;
  }
  return el.value ?? fallback;
}

function writeParam(key, value) {
  const el = document.querySelector(`#params [data-param="${key}"]`);
  if (!el) return;
  if (el.type === 'checkbox') el.checked = !!value;
  else el.value = value;
  const out = document.querySelector(`#params [data-out="${key}"]`);
  if (out) out.textContent = String(value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
}

function configFromParams() {
  return {
    explode: readParam('explode_amount', 0),
    skin: readParam('skin', 'steel'),
    camera: readParam('camera_preset', 'three_quarter'),
    tof: {
      enabled: readParam('tof_enabled', true),
      x: readParam('tof_x', 0.08),
      y: readParam('tof_y', 0),
      z: readParam('tof_z', 0.12),
      size: readParam('tof_size', 0.04),
      mass: readParam('tof_mass', 0.08),
    },
    audio: {
      enabled: readParam('audio_enabled', true),
      x: readParam('audio_x', -0.06),
      y: readParam('audio_y', 0.05),
      z: readParam('audio_z', 0.05),
      size: readParam('audio_size', 0.035),
      mass: readParam('audio_mass', 0.05),
    },
  };
}

export function createHardwareStudio(host, {ensureRobotManifest, getRobotId, onStatus} = {}) {
  if (!host) return null;
  let viewer = null;
  let ready = false;

  host.hidden = false;
  host.innerHTML = `
    <div class="hw-studio">
      <div class="hw-studio-head">
        <strong>${en() ? 'Hardware / explode studio' : '硬體／爆炸圖工作室'}</strong>
        <span class="muted" id="hw-status">${en() ? 'Loading…' : '載入中…'}</span>
      </div>
      <div class="hw-studio-grid">
        <div class="hw-stage-wrap">
          <div class="hw-stage" id="hw-stage"></div>
          <div class="hw-actions">
            <button type="button" class="button secondary" id="hw-regen">${en() ? 'Regenerate look' : '重新生成外形'}</button>
            <button type="button" class="button primary" id="hw-explode">${en() ? 'Analyze / explode' : '分析／分解'}</button>
            <button type="button" class="button secondary" id="hw-assemble">${en() ? 'Assemble' : '重組'}</button>
          </div>
        </div>
        <div class="hw-side">
          <video class="hw-ref" id="hw-ref" controls playsinline muted loop
            src="${VIDEO}" poster=""></video>
          <p class="hw-ref-cap">${en()
            ? 'Reference: RoboParty RP1 hardware-wall explode'
            : '參考：RoboParty RP1 硬體牆爆炸動畫'}</p>
          <p class="hw-hint muted">${en()
            ? 'Tweaks sync with the lesson sliders. Run sends the same ToF/audio mass into ImplicitActuatorCfg.'
            : '此處調整會同步左側參數滑桿；按「執行」會把 ToF／音訊質量送進 ImplicitActuatorCfg。'}</p>
        </div>
      </div>
    </div>`;

  const statusEl = $('#hw-status', host);
  const setStatus = (text) => {
    if (statusEl) statusEl.textContent = text;
    onStatus?.(text);
  };

  async function boot() {
    const stage = $('#hw-stage', host);
    viewer = await createRobotViewer(stage, {onStatus: setStatus});
    const entry = await ensureRobotManifest(getRobotId?.() || 'leg');
    await viewer.loadRobot(entry);
    viewer.setPlaying(false);
    viewer.setRenderingPaused(false);
    applyAll({animate: false});
    ready = true;
    setStatus(en() ? 'Ready — edit parts, then explode' : '就緒 — 調元件後按分析／分解');
  }

  function applyAll({animate = false, explodeTo = null} = {}) {
    if (!viewer) return;
    const cfg = configFromParams();
    viewer.regenerateAppearance({
      skin: cfg.skin,
      camera: cfg.camera,
      tof: cfg.tof,
      audio: cfg.audio,
      explode: animate ? undefined : (explodeTo ?? cfg.explode),
    });
    if (animate) {
      const target = explodeTo ?? Math.max(0.55, Number(cfg.explode) || 0.85);
      writeParam('explode_amount', target);
      viewer.animateExplode(target, {duration: 1.35, from: viewer.getExplode?.() || 0});
    }
  }

  $('#hw-regen', host)?.addEventListener('click', () => {
    applyAll({animate: false});
    setStatus(en() ? 'Appearance regenerated' : '外形已重新生成');
  });
  $('#hw-explode', host)?.addEventListener('click', () => {
    const target = Math.max(0.55, Number(readParam('explode_amount', 0)) || 0.85);
    applyAll({animate: true, explodeTo: target});
  });
  $('#hw-assemble', host)?.addEventListener('click', () => {
    writeParam('explode_amount', 0);
    viewer?.animateExplode(0, {duration: 0.9, from: viewer.getExplode?.() || 0});
    setStatus(en() ? 'Assembling…' : '重組中…');
  });

  // Live-sync when lesson param sliders change.
  const paramsBox = document.getElementById('params');
  const onParamInput = (e) => {
    if (!ready || !viewer) return;
    const key = e.target?.dataset?.param;
    if (!key) return;
    if (['skin', 'tof_enabled', 'audio_enabled', 'tof_x', 'tof_y', 'tof_z', 'tof_size',
      'audio_x', 'audio_y', 'audio_z', 'audio_size', 'camera_preset'].includes(key)) {
      applyAll({animate: false});
    } else if (key === 'explode_amount') {
      viewer.setExplode(readParam('explode_amount', 0));
    }
  };
  paramsBox?.addEventListener('input', onParamInput);
  paramsBox?.addEventListener('change', onParamInput);

  boot().catch((err) => setStatus(err.message || String(err)));

  return {
    refresh() { if (ready) applyAll({animate: false}); },
    dispose() {
      paramsBox?.removeEventListener('input', onParamInput);
      paramsBox?.removeEventListener('change', onParamInput);
      viewer?.dispose?.();
      viewer = null;
      host.innerHTML = '';
      host.hidden = true;
    },
  };
}
