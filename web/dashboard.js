/* 관제 모니터링 (단일 현장 · 디지털 트윈) */
let SEL = null, HOURS = 12, STATE = null, LAST_EV = 0, HIST = null, MODELS = [], WX = null;
const twin = new Twin3D($('#twin'));
$('#gauge').insertAdjacentHTML('afterbegin', gaugeSVG());

function gateOf(s) {
  const fp = s.forecast_planned || [], c = s.current.radon;
  if (s.sensor_fault) return ['no', '⚠️', '진입 금지 — 센서 이상', '측정 복구 전까지 환기팬 연속 가동'];
  if (c <= 100 && (fp[2] ?? c) <= 100) return ['ok', '✅', '진입 가능', `안전 단계 유지 · 30분 후 예측 ${fmt0(fp[2])} Bq/㎥`];
  if (s.phase === 'pre') return ['wait', '🌀', '초기환기 진행 중', '100 Bq/㎥ 이하 도달 시 진입 가능 알림'];
  return ['no', '⛔', '진입 보류', `현재 ${fmt0(c)} Bq/㎥ — 출입 등록 시 AI가 자동 환기`];
}

async function refreshState() {
  try { STATE = await API.get('/api/state'); } catch (e) { return; }
  const s = STATE.sites[0]; SEL = s.id;
  $('#simTime').textContent = STATE.sim_time;
  const w = STATE.weather;
  $('#wx').innerHTML = `☁ ${STATE.live ? '기상청 실황' : '시나리오 기상'} · <b>${w.pressure}</b> hPa · ${w.temp}℃`;
  $('#modeChip').innerHTML = STATE.realtime ? '<span class="dot-live"></span> <b>LIVE</b> 실시간' : `<span class="dot-fast"></span> <b>×${STATE.speed}</b> ${STATE.live ? '실기상 가속' : '시나리오'}`;
  $('#speed').value = String(STATE.speed);
  $('#pauseBtn').textContent = STATE.paused ? '▶ 재개' : '❚❚ 일시정지';
  $('#remoteCk').checked = STATE.allow_remote_control;

  const c = s.current.radon, lv = s.level, fp = s.forecast_planned || [], fn = s.forecast_natural || [];
  const [gc, gi, gt, gs] = gateOf(s);
  twin.set({ c: s.sensor_fault ? 0 : c, fan: s.fan, level: lv.code, gate: gc, occupied: !!(s.next_entry && s.next_entry.active), phase: s.phase });

  // 게이지
  const gv = $('#gVal'); gv.className = 'val num c-' + lv.code;
  if (s.sensor_fault) gv.textContent = 'ERR'; else animNum(gv, c);
  $('#gLv').className = 'lv c-' + lv.code; $('#gLv').textContent = s.sensor_fault ? '센서 이상' : `${lv.label} 단계`;
  setGauge($('#gauge'), s.sensor_fault ? 0 : c, fp[5]);
  const g = $('#gate'); g.className = 'gate ' + gc; g.querySelector('.ic').textContent = gi; $('#gateT').textContent = gt; $('#gateS').textContent = gs;
  const mv = (id, v) => { const el = $(id); el.className = 'v num c-' + levelOf(v ?? 0).code; el.textContent = fmt0(v); };
  mv('#m60', fp[5]); $('#m60s').textContent = `무환기 ${fmt0(fn[5])}`;
  mv('#m120', fp[11]); $('#m120s').textContent = `무환기 ${fmt0(fn[11])}`;
  $('#mFan').innerHTML = s.fan ? '<span class="fan-pill on" style="font-size:20px">' + FAN_SVG + 'ON</span>' : '<span style="color:var(--muted)">OFF</span>';
  const ne = s.next_entry;
  $('#mFanS').textContent = ne ? (ne.active ? `출입 중 ~${ne.end}` : `다음 출입 ${ne.start.slice(6)}`) : '출입 일정 없음';
  $('#envLine').textContent = `${s.current.temp}℃ · ${s.current.rh}%`;

  // HUD·흐름
  $('#hudMode').textContent = { AUTO: 'AI-AUTO', LEGACY: 'LEGACY TIMER', MANUAL: 'MANUAL' }[s.mode];
  $('#hudReason').textContent = s.reason;
  $('#hudWx').innerHTML = `기압 <b>${w.pressure}</b> hPa · 강수 <b>${w.rain}</b> mm · ${w.temp}℃`;
  $('#fs1').textContent = `${STATE.weather_source} · 센서 1분 주기`;
  $('#fs2').textContent = fp.length ? `1H ${fmt0(fp[5])} · 2H ${fmt0(fp[11])} Bq/㎥` : '이력 수집 중';
  $('#fs3').textContent = s.reason;
  const steps = { data: true, ai: s.phase === 'ai' || (fn[5] ?? 0) > c * 1.05, vent: s.fan, gen: false };
  document.querySelectorAll('.step').forEach((el) => el.classList.toggle('on', !!steps[el.dataset.step]));

  // 운전 제어
  document.querySelectorAll('#modeSeg button').forEach((b) => b.classList.toggle('on', b.dataset.m === s.mode));
  $('#modeHelp').textContent = {
    AUTO: '출입 전 초기환기(지수감쇠 모델) → 출입 중 유지환기(ON 20/OFF 10분) → 무인 시 위험 예측 때만 AI 선제환기',
    LEGACY: '기존 방식: 타이머로 1일 20시간 상시 가동 — 에너지·노출 비교용',
    MANUAL: '수동: 관제 또는 현장 단말기 버튼으로 직접 ON/OFF',
  }[s.mode];
  const st = s.stats;
  $('#sRun').innerHTML = `${st.run_hours_24h}<small> / ${st.legacy_hours}h</small>`;
  $('#sRunBar').style.width = Math.min(100, (st.run_hours_24h / st.legacy_hours) * 100) + '%';
  $('#sSave').textContent = st.saving_pct + '%';
  $('#sSaveBar').style.width = Math.max(0, Math.min(100, st.saving_pct)) + '%';
  $('#sEntry').innerHTML = `${fmt0(st.entry_avg)}<small> / ${fmt0(st.entry_max)}</small>`;
  $('#reason').innerHTML = `<b>AI 제어 판단</b> · ${esc(s.reason)}`;
  $('#csvBtn').href = '/api/export.csv?site=' + SEL;
  if (STATE.last_event_id > LAST_EV) pullEvents();
}

async function refreshHistory() {
  if (!SEL) return;
  HIST = await API.get(`/api/history?site=${SEL}&hours=${HOURS}`);
  drawRadonChart($('#chart'), HIST);
  const entries = await API.get('/api/entries');
  $('#entryBody').innerHTML = entries.filter((e) => e.site === SEL).map((e) =>
    `<tr><td>${esc(e.label)}</td><td class="mono" style="font-size:12px">${esc(e.start.slice(-5))}~${esc(e.end.slice(-5))}</td><td>${e.daily ? '매일' : '1회'}</td>
     <td><button class="btn sm danger" data-del="${e.id}">삭제</button></td></tr>`).join('') || '<tr><td colspan="4" class="help">등록된 일정 없음</td></tr>';
}

async function pullEvents() {
  const evs = await API.get('/api/events?since=' + LAST_EV);
  if (!evs.length) return;
  const first = LAST_EV === 0;
  LAST_EV = evs[evs.length - 1].id;
  const box = $('#events');
  box.insertAdjacentHTML('afterbegin', evs.map((e) => `<div class="ev ${e.kind}"><span class="t">${e.t}</span><span class="m">${esc(e.msg)}</span></div>`).reverse().join(''));
  while (box.children.length > 200) box.lastChild.remove();
  $('#evCount').textContent = `${box.children.length}건`;
  if (!first) evs.filter((e) => ['ok', 'warn', 'danger'].includes(e.kind)).slice(-3).forEach((e) => toast(e.msg, e.kind));
}

async function loadModel() {
  const m = await API.get('/api/model');
  const v = m.metrics[SEL] || Object.values(m.metrics)[0] || {};
  const a = v['60min'] || {}, b = v['120min'] || {};
  $('#modelInfo').innerHTML = `AI 모델: 기상(기압·강수)·실내 측정·환기계획 융합 회귀 · 검증 오차 1시간 <b>${a.MAE_Bq}</b> / 2시간 <b>${b.MAE_Bq}</b> Bq/㎥ · 현재값 유지 방식 대비 오차 <b style="color:var(--safe)">${b.improve_pct}% 감소</b>`;
}

async function loadLLM() {
  const c = await API.get('/api/ai/models');
  MODELS = c.models;
  $('#llmModel').innerHTML = MODELS.length ? MODELS.map((m) => `<option value="${esc(m.id)}" ${m.id === c.default ? 'selected' : ''}>${esc(m.label)}</option>`).join('')
    : '<option value="">LLM 미설정 — 규칙 기반 템플릿</option>';
  $('#llmName').textContent = MODELS.length ? `연결 모델 ${MODELS.length}종` : '템플릿 모드';
  $('#aiCompare').disabled = MODELS.length < 2;
  $('#fs4').textContent = MODELS.length ? `${MODELS[0].model} 외 ${MODELS.length - 1}종 · 모델 교체형` : '템플릿 모드';
}

function flashGen() { const el = document.querySelector('.step[data-step="gen"]'); el.classList.add('on'); setTimeout(() => el.classList.remove('on'), 4000); }

async function runAI(kind) {
  const out = $('#aiOut'); out.textContent = '✨ 생성형 AI가 측정·예측·기상 데이터를 분석하고 있습니다…'; $('#aiMeta').textContent = ''; $('#cmpBox').style.display = 'none';
  flashGen();
  const t = performance.now();
  const r = await API.post('/api/ai/' + kind, { site: SEL, model: $('#llmModel').value || null });
  out.textContent = r.text || r.error;
  $('#aiMeta').textContent = `생성: ${r.engine} · ${((performance.now() - t) / 1000).toFixed(1)}초${r.error ? ' · 참고: ' + r.error : ''}`;
}

async function runCompare() {
  const a = $('#llmModel').value;
  const fam = (id) => (id.split(':')[1] || '').split('/').pop().split('-')[0];
  const b = (MODELS.find((m) => m.id !== a && fam(m.id) !== fam(a)) || MODELS.find((m) => m.id !== a) || {}).id;
  if (!b) return;
  flashGen();
  const box = $('#cmpBox'); box.style.display = 'grid';
  box.innerHTML = [a, b].map((m) => `<div><h4>${esc(m.split(':')[1])}</h4><div class="ai-out">생성 중…</div></div>`).join('');
  $('#aiOut').textContent = '같은 데이터로 두 모델의 안전 브리핑을 비교합니다 ▼'; $('#aiMeta').textContent = '';
  const r = await API.post('/api/ai/compare', { site: SEL, task: 'briefing', models: [a, b] });
  box.innerHTML = (r.results || []).map((x, i) => `<div><h4>${esc((x?.engine || [a, b][i]))} · ${x?.sec ?? '-'}초</h4><div class="ai-out">${esc(x?.text || x?.error || '-')}</div></div>`).join('');
}

async function loadWeather() {
  const w = await API.get('/api/weather'); WX = w;
  const kmaOk = w.source === 'kma';
  $('#wxSrc').innerHTML = `<span class="${kmaOk ? 'src-kma' : 'src-alt'}">● ${kmaOk ? '기상청 연동 정상' : esc(w.label || '-')}</span>`;
  if (!w.now) return;
  $('#wP').textContent = w.now.pressure + ' hPa';
  $('#wP3').innerHTML = `<span class="${w.p_trend_3h < -0.5 ? 'c-caution' : ''}">${w.p_trend_3h > 0 ? '+' : ''}${w.p_trend_3h} hPa</span>`;
  $('#wR').textContent = w.now.rain + ' mm'; $('#wR6').textContent = w.rain_6h + ' mm';
  $('#wxNext').innerHTML = (w.next || []).map((n) => `<div>${esc(n.t)}<b>${n.p}</b>${n.rain > 0 ? '☔ ' + n.rain : n.temp + '℃'}</div>`).join('');
  drawSpark($('#pSpark'), (w.next || []).map((n) => n.p), (w.next || []).map((n) => n.t));
  const names = { asos: 'ASOS 관측', ncst: '초단기실황', ultra: '초단기예보', vilage: '단기예보', metno: '기압 예보(MET Norway)', open_meteo: '기압 예보(Open-Meteo)' };
  const parts = Object.entries(w.parts || {}).map(([k, v]) => `<span style="color:var(--cyan)">▸</span> ${names[k] || k}: ${esc(v)}`).join('<br>');
  $('#wxMeta').innerHTML = `관측소 서울(${esc(w.stn)}) · 격자 ${esc((w.grid || []).join(','))} · 갱신 ${esc(w.updated || '-')}<br>${parts}` +
    ((w.errors || []).length ? `<div style="color:var(--amber);margin-top:4px">${w.errors.map(esc).join('<br>')}</div>` : '');
}

/* ---- 이벤트 ---- */
document.addEventListener('click', async (ev) => {
  const el = ev.target.closest('button, a'); if (!el) return;
  if (el.dataset.h) { HOURS = +el.dataset.h; document.querySelectorAll('#rangeSeg button').forEach((b) => b.classList.toggle('on', b === el)); refreshHistory(); }
  if (el.dataset.m) { await API.post('/api/mode', { site: SEL, mode: el.dataset.m }); refreshState(); }
  if (el.dataset.ev) { await API.post('/api/sim', { event: el.dataset.ev, site: SEL }); refreshState(); toast('시나리오 적용: ' + el.textContent.trim()); }
  if (el.dataset.del) { await API.post('/api/entries/delete', { id: el.dataset.del }); refreshHistory(); }
  if (el.dataset.quick) { await API.post('/api/entries', { site: SEL, quick_minutes: +el.dataset.quick, duration: 60, label: '긴급 출입' }); refreshHistory(); refreshState(); toast(`${el.dataset.quick}분 후 진입 등록 — AI가 초기환기를 계획합니다`, 'ok'); }
  if (el.id === 'fanOn' || el.id === 'fanOff') { const r = await API.post('/api/fan', { site: SEL, on: el.id === 'fanOn' }); if (r.error) toast(r.error, 'warn'); refreshState(); }
  if (el.id === 'pauseBtn') { await API.post('/api/sim', { paused: !STATE.paused }); refreshState(); }
  if (el.id === 'enAdd') {
    const r = await API.post('/api/entries', { site: SEL, label: $('#enLabel').value || '점검 작업', start: $('#enStart').value, end: $('#enEnd').value, daily: $('#enDaily').checked });
    if (r.error) toast(r.error, 'warn'); refreshHistory();
  }
  if (el.id === 'aiBrief') runAI('briefing');
  if (el.id === 'aiReport') runAI('report');
  if (el.id === 'aiCompare') runCompare();
  if (el.id === 'aiCopy') { navigator.clipboard?.writeText($('#aiOut').textContent); toast('복사했습니다.'); }
  if (el.id === 'liveBtn' || el.id === 'scnBtn') {
    await API.post('/api/session/reset', { mode: el.id === 'liveBtn' ? 'live' : 'scenario' });
    LAST_EV = 0; $('#events').innerHTML = ''; await refreshState(); refreshHistory();
    toast(el.id === 'liveBtn' ? '실시간 모드로 초기화했습니다 (기상청 실시간 기상·현재 시각).' : '시나리오 모드: 합성 기상으로 ×300 가속 시연합니다.');
  }
});
$('#speed').addEventListener('change', (e) => API.post('/api/sim', { speed: +e.target.value }));
$('#remoteCk').addEventListener('change', (e) => API.post('/api/sim', { remote: e.target.checked }));
window.addEventListener('resize', () => { HIST && drawRadonChart($('#chart'), HIST); WX && WX.next && drawSpark($('#pSpark'), WX.next.map((n) => n.p), WX.next.map((n) => n.t)); });
attachTooltip($('#chart'), $('#tip'));

(async () => {
  await refreshState(); await refreshHistory(); loadModel(); loadLLM(); loadWeather();
  setInterval(refreshState, 1000); setInterval(refreshHistory, 3000); setInterval(loadWeather, 60000);
})();
