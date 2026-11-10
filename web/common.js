/* 공통 유틸 · 네온 차트 · 원형 게이지 (외부 라이브러리 없음) */
const API = {
  async get(url) { const r = await fetch(url); return r.json(); },
  async post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    return r.json();
  },
};
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt0 = (v) => (v === null || v === undefined ? '-' : Math.round(v).toLocaleString());
function levelOf(c) {
  if (c <= 100) return { code: 'safe', label: '안전' };
  if (c <= 300) return { code: 'interest', label: '관심' };
  if (c <= 600) return { code: 'caution', label: '주의' };
  return { code: 'danger', label: '위험' };
}
const LV_RGB = { safe: '46,245,165', interest: '255,216,74', caution: '255,143,63', danger: '255,61,104' };
const FAN_SVG = '<svg class="fan-ico" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="2.2"/><path d="M12 2c2.8 0 3.6 3.2 2 6.2l-1.2 2.2h-1.6C10 7.4 9.2 2 12 2zm10 10c0 2.8-3.2 3.6-6.2 2l-2.2-1.2v-1.6c3-1.2 8.4-2 8.4.8zM12 22c-2.8 0-3.6-3.2-2-6.2l1.2-2.2h1.6c1.2 3 2 8.4-.8 8.4zM2 12c0-2.8 3.2-3.6 6.2-2l2.2 1.2v1.6C7.4 14 2 14.8 2 12z"/></svg>';
function fanPill(on, text) { return `<span class="fan-pill ${on ? 'on' : ''}">${FAN_SVG}${text ?? (on ? '환기팬 가동' : '환기팬 정지')}</span>`; }
const parseT = (s) => { const [d, t] = s.split(' '); const [y, m, dd] = d.split('-').map(Number); const [h, mi] = t.split(':').map(Number); return new Date(y, m - 1, dd, h, mi).getTime(); };
const hhmm = (ms) => { const d = new Date(ms); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };

function toast(msg, kind = '') {
  let box = $('.toast-box');
  if (!box) { box = document.createElement('div'); box.className = 'toast-box'; document.body.appendChild(box); }
  const el = document.createElement('div');
  el.className = 'toast ' + kind; el.textContent = msg; box.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

/* 숫자 부드럽게 변화 */
function animNum(el, to, digits = 0) {
  if (!el) return;
  const from = parseFloat(el.dataset.v || to);
  el.dataset.v = to;
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / 600), v = from + (to - from) * (1 - Math.pow(1 - k, 3));
    el.textContent = digits ? v.toFixed(digits) : Math.round(v).toLocaleString();
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ---------------- 원형 게이지 (SVG) ---------------- */
const GMAX = 800;
function gaugeSVG() {
  const R = 120, C = 150, start = 135, sweep = 270;
  const pt = (deg, r) => { const a = (deg * Math.PI) / 180; return [C + r * Math.cos(a), C + r * Math.sin(a)]; };
  const arc = (r, d0, d1) => { const [x0, y0] = pt(d0, r), [x1, y1] = pt(d1, r); return `M${x0},${y0} A${r},${r} 0 ${d1 - d0 > 180 ? 1 : 0} 1 ${x1},${y1}`; };
  const v2d = (v) => start + (Math.min(v, GMAX) / GMAX) * sweep;
  let ticks = '';
  for (let v = 0; v <= GMAX; v += 50) {
    const big = v % 100 === 0, [x0, y0] = pt(v2d(v), big ? 136 : 139), [x1, y1] = pt(v2d(v), 146);
    ticks += `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="rgba(150,200,255,${big ? 0.6 : 0.3})" stroke-width="${big ? 2 : 1}"/>`;
  }
  const zones = [[0, 100, '#2ef5a5'], [100, 300, '#ffd84a'], [300, 600, '#ff8f3f'], [600, 800, '#ff3d68']]
    .map(([a, b, c]) => `<path d="${arc(158, v2d(a) + 0.8, v2d(b) - 0.8)}" stroke="${c}" stroke-width="5" fill="none" opacity=".85" filter="url(#gGlow)"/>`).join('');
  const lbl = [0, 100, 300, 600, 800].map((v) => { const [x, y] = pt(v2d(v), 176); return `<text x="${x}" y="${y}" fill="#6a81a6" font-size="11" text-anchor="middle" dominant-baseline="middle" font-family="Orbitron, sans-serif">${v}</text>`; }).join('');
  return `<svg viewBox="-30 -30 360 360">
    <defs>
      <linearGradient id="gGrad" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#22e3ff"/><stop offset=".5" stop-color="#3d8bff"/><stop offset="1" stop-color="#ff3d68"/></linearGradient>
      <filter id="gGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <radialGradient id="gCore"><stop offset="0" stop-color="rgba(61,139,255,.35)"/><stop offset="1" stop-color="rgba(61,139,255,0)"/></radialGradient>
    </defs>
    <circle cx="${C}" cy="${C}" r="112" fill="url(#gCore)"/>
    <circle cx="${C}" cy="${C}" r="98" fill="none" stroke="rgba(34,227,255,.15)" stroke-dasharray="2 6"/>
    ${zones}${ticks}${lbl}
    <path d="${arc(R, start, start + sweep)}" stroke="rgba(255,255,255,.07)" stroke-width="16" fill="none" stroke-linecap="round"/>
    <path id="gArc" d="${arc(R, start, start + sweep)}" stroke="url(#gGrad)" stroke-width="16" fill="none" stroke-linecap="round" filter="url(#gGlow)" pathLength="1000" stroke-dasharray="0 1000" style="transition: stroke-dasharray .8s cubic-bezier(.2,.8,.2,1)"/>
    <g id="gNeedle" style="transform-origin:${C}px ${C}px; transition: transform .8s cubic-bezier(.2,.8,.2,1)"><circle cx="${pt(start, R)[0]}" cy="${pt(start, R)[1]}" r="9" fill="#fff" filter="url(#gGlow)"/></g>
    <g id="gFc"></g>
  </svg>`;
}
function setGauge(root, v, fc60) {
  const frac = Math.min(v, GMAX) / GMAX;
  root.querySelector('#gArc').setAttribute('stroke-dasharray', `${frac * 1000} 1000`);
  root.querySelector('#gNeedle').style.transform = `rotate(${frac * 270}deg)`;
  const g = root.querySelector('#gFc');
  if (fc60 != null) {
    const a = ((135 + (Math.min(fc60, GMAX) / GMAX) * 270) * Math.PI) / 180;
    g.innerHTML = `<circle cx="${150 + 120 * Math.cos(a)}" cy="${150 + 120 * Math.sin(a)}" r="6" fill="none" stroke="#22e3ff" stroke-width="2.5" stroke-dasharray="3 2"><animate attributeName="r" values="5;9;5" dur="1.6s" repeatCount="indefinite"/></circle>`;
  }
}

/* ---------------- 라돈 네온 차트 ---------------- */
const LV_BANDS = [
  [0, 100, 'rgba(46,245,165,.06)'], [100, 300, 'rgba(255,216,74,.05)'],
  [300, 600, 'rgba(255,143,63,.06)'], [600, 99999, 'rgba(255,61,104,.08)'],
];
function drawRadonChart(canvas, h, opt = {}) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const g = canvas.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const compact = opt.compact;
  const pad = { l: compact ? 34 : 46, r: compact ? 8 : 14, t: 12, b: compact ? 34 : 40 };
  const fanH = compact ? 6 : 9;
  const now = parseT(h.now);
  const ts = h.t.map(parseT);
  const t0 = ts.length ? ts[0] : now - 6 * 3600e3, t1 = now + 120 * 60e3;
  const fN = h.forecast_natural || [], fP = h.forecast_planned || [];
  const vals = h.radon.filter((v) => v !== null).concat(fN, fP);
  let ymax = Math.max(150, ...vals) * 1.12;
  ymax = ymax <= 200 ? Math.ceil(ymax / 50) * 50 : ymax <= 700 ? Math.ceil(ymax / 100) * 100 : Math.ceil(ymax / 200) * 200;
  const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b - fanH - 6;
  const X = (t) => pad.l + ((t - t0) / (t1 - t0)) * pw;
  const Y = (v) => pad.t + ph - (Math.min(v, ymax) / ymax) * ph;
  g.font = `${compact ? 10 : 11}px Orbitron, Pretendard, sans-serif`;

  for (const [a, b, col] of LV_BANDS) {
    if (a >= ymax) continue;
    g.fillStyle = col; g.fillRect(pad.l, Y(Math.min(b, ymax)), pw, Y(a) - Y(Math.min(b, ymax)));
  }
  g.setLineDash([4, 4]); g.lineWidth = 1;
  for (const [v, col] of [[100, '46,245,165'], [300, '255,216,74'], [600, '255,61,104']]) {
    if (v >= ymax) continue;
    g.strokeStyle = `rgba(${col},.55)`; g.beginPath(); g.moveTo(pad.l, Y(v)); g.lineTo(pad.l + pw, Y(v)); g.stroke();
    if (!compact) { g.fillStyle = `rgba(${col},.9)`; g.fillText(v + '', pad.l + pw - 28, Y(v) - 4); }
  }
  g.setLineDash([]);
  for (const [s, e, label] of h.entries || []) {
    const xs = Math.max(X(parseT(s)), pad.l), xe = Math.min(X(parseT(e)), pad.l + pw);
    if (xe <= xs) continue;
    const gr = g.createLinearGradient(0, pad.t, 0, pad.t + ph);
    gr.addColorStop(0, 'rgba(255,216,74,.14)'); gr.addColorStop(1, 'rgba(255,216,74,.02)');
    g.fillStyle = gr; g.fillRect(xs, pad.t, xe - xs, ph);
    g.strokeStyle = 'rgba(255,216,74,.5)'; g.beginPath(); g.moveTo(xs, pad.t); g.lineTo(xs, pad.t + ph); g.stroke();
    if (!compact && xe - xs > 50) { g.fillStyle = '#ffd84a'; g.font = '11px Pretendard, sans-serif'; g.fillText('🚶 ' + label, xs + 5, pad.t + 14); g.font = '11px Orbitron, sans-serif'; }
  }
  g.fillStyle = '#6a81a6'; g.strokeStyle = 'rgba(96,180,255,.08)';
  const step = ymax <= 200 ? 50 : ymax <= 700 ? 100 : 200;
  for (let v = 0; v <= ymax; v += step) { g.beginPath(); g.moveTo(pad.l, Y(v)); g.lineTo(pad.l + pw, Y(v)); g.stroke(); g.fillText(v, 4, Y(v) + 4); }
  const span = (t1 - t0) / 3600e3, every = span > 30 ? 6 : span > 14 ? 2 : 1;
  const first = new Date(t0); first.setMinutes(0, 0, 0);
  for (let t = first.getTime() + 3600e3; t < t1; t += 3600e3) {
    if (new Date(t).getHours() % every) continue;
    g.fillStyle = '#6a81a6'; g.fillText(hhmm(t), X(t) - 16, H - pad.b + fanH + 22);
  }
  // 미래 구간 + 현재선
  const fg = g.createLinearGradient(X(now), 0, pad.l + pw, 0);
  fg.addColorStop(0, 'rgba(34,227,255,.10)'); fg.addColorStop(1, 'rgba(34,227,255,.02)');
  g.fillStyle = fg; g.fillRect(X(now), pad.t, pad.l + pw - X(now), ph);
  g.strokeStyle = '#22e3ff'; g.shadowColor = '#22e3ff'; g.shadowBlur = 10; g.lineWidth = 1.4;
  g.beginPath(); g.moveTo(X(now), pad.t); g.lineTo(X(now), pad.t + ph + fanH + 6); g.stroke(); g.shadowBlur = 0;
  g.fillStyle = '#22e3ff'; g.fillText('NOW', X(now) + 5, pad.t + 12);
  if (!compact) { g.fillStyle = 'rgba(34,227,255,.8)'; g.fillText('AI FORECAST ▸', X(now) + 5, pad.t + ph - 6); }

  // 환기팬 스트립
  const fy = pad.t + ph + 6;
  g.fillStyle = 'rgba(255,255,255,.05)'; g.fillRect(pad.l, fy, pw, fanH);
  for (let i = 0; i < ts.length; i++) if (h.fan[i] > 0) {
    g.fillStyle = `rgba(54,185,255,${0.3 + 0.7 * h.fan[i]})`; g.fillRect(X(ts[i] - 600e3), fy, Math.max(1, X(ts[i]) - X(ts[i] - 600e3)), fanH);
  }
  (h.plan || []).forEach((v, k) => { if (v > 0) { g.fillStyle = `rgba(54,185,255,${0.15 + 0.3 * v})`; const a = now + k * 600e3; g.fillRect(X(a), fy, X(a + 600e3) - X(a), fanH); } });
  if (!compact) { g.fillStyle = '#6a81a6'; g.font = '10px Pretendard, sans-serif'; g.fillText('환기팬', 4, fy + fanH); }

  const pts = ts.map((t, i) => [t, h.radon[i]]).filter((p) => p[1] !== null);
  // 측정값 면적 그라디언트
  if (pts.length > 1) {
    const ag = g.createLinearGradient(0, pad.t, 0, pad.t + ph);
    ag.addColorStop(0, 'rgba(34,227,255,.35)'); ag.addColorStop(1, 'rgba(34,227,255,0)');
    g.beginPath(); g.moveTo(X(pts[0][0]), Y(0));
    pts.forEach(([t, v]) => g.lineTo(X(t), Y(v)));
    g.lineTo(X(pts[pts.length - 1][0]), Y(0)); g.closePath(); g.fillStyle = ag; g.fill();
  }
  const line = (pp, col, w, dash, glow) => {
    g.strokeStyle = col; g.lineWidth = w; g.setLineDash(dash || []); g.shadowColor = col; g.shadowBlur = glow || 0; g.beginPath();
    let pen = false;
    for (const [t, v] of pp) { if (v === null) { pen = false; continue; } const x = X(t), y = Y(v); pen ? g.lineTo(x, y) : g.moveTo(x, y); pen = true; }
    g.stroke(); g.setLineDash([]); g.shadowBlur = 0;
  };
  const last = ts.length ? [ts[ts.length - 1], h.radon[h.radon.length - 1]] : null;
  if (fN.length && last && last[1] !== null) line([last, ...fN.map((v, k) => [now + (k + 1) * 600e3, v])], '#ff3d68', compact ? 1.4 : 1.8, [6, 5], 8);
  if (fP.length && last && last[1] !== null) line([last, ...fP.map((v, k) => [now + (k + 1) * 600e3, v])], '#22e3ff', compact ? 2 : 2.6, [8, 5], 14);
  line(pts, '#eaf6ff', compact ? 1.8 : 2.2, null, 12);
  if (last && last[1] !== null) {
    const x = X(last[0]), y = Y(last[1]);
    g.fillStyle = '#fff'; g.shadowColor = '#22e3ff'; g.shadowBlur = 16; g.beginPath(); g.arc(x, y, 4.5, 0, 7); g.fill(); g.shadowBlur = 0;
  }
  canvas._chart = { X, Y, t0, t1, ts, h, now, pad, pw };
}

function attachTooltip(canvas, tip) {
  canvas.addEventListener('mousemove', (ev) => {
    const c = canvas._chart; if (!c) return;
    const rect = canvas.getBoundingClientRect(); const x = ev.clientX - rect.left;
    const t = c.t0 + ((x - c.pad.l) / c.pw) * (c.t1 - c.t0);
    let html = '';
    if (t <= c.now) {
      let bi = -1, bd = 1e18; c.ts.forEach((tt, i) => { const d = Math.abs(tt - t); if (d < bd) { bd = d; bi = i; } });
      if (bi < 0) return;
      const v = c.h.radon[bi];
      html = `${c.h.t[bi].slice(5)}<br>라돈 <b>${v === null ? '결측' : fmt0(v)}</b> Bq/㎥<br>환기팬 ${Math.round(c.h.fan[bi] * 100)}% · 기압 ${c.h.pressure[bi]}hPa`;
    } else {
      const k = Math.min(11, Math.max(0, Math.round((t - c.now) / 600e3) - 1));
      html = `<b style="color:#22e3ff">AI 예측</b> ${k * 10 + 10}분 후 (${hhmm(c.now + (k + 1) * 600e3)})<br>제어 반영 <b>${fmt0((c.h.forecast_planned || [])[k])}</b> Bq/㎥<br>환기 안 할 경우 ${fmt0((c.h.forecast_natural || [])[k])}`;
    }
    tip.innerHTML = html; tip.style.display = 'block';
    tip.style.left = Math.min(x + 14, rect.width - 200) + 'px'; tip.style.top = (ev.clientY - rect.top - 10) + 'px';
  });
  canvas.addEventListener('mouseleave', () => (tip.style.display = 'none'));
}

/* 기압 스파크라인 */
function drawSpark(canvas, vals, labels) {
  const dpr = window.devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !vals.length) return;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const g = canvas.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const lo = Math.min(...vals) - 0.5, hi = Math.max(...vals) + 0.5;
  const X = (i) => 6 + (i / (vals.length - 1)) * (W - 12), Y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (H - 22);
  const gr = g.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, 'rgba(139,92,255,.35)'); gr.addColorStop(1, 'rgba(139,92,255,0)');
  g.beginPath(); g.moveTo(X(0), H - 14); vals.forEach((v, i) => g.lineTo(X(i), Y(v))); g.lineTo(X(vals.length - 1), H - 14); g.fillStyle = gr; g.fill();
  g.beginPath(); vals.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))));
  g.strokeStyle = '#a78bff'; g.lineWidth = 2; g.shadowColor = '#8b5cff'; g.shadowBlur = 10; g.stroke(); g.shadowBlur = 0;
  g.fillStyle = '#6a81a6'; g.font = '10px Orbitron, sans-serif';
  labels.forEach((l, i) => { if (i % 2 === 0) { const tw = g.measureText(l).width; g.fillText(l, Math.max(0, Math.min(W - tw, X(i) - tw / 2)), H - 2); } });
}
