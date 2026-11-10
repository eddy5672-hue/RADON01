/* 작업자용 모바일 앱 (PWA) */
let SEL = null, ST = null, LAST = 0, UNREAD = 0, TAB = 'home';
const twin = new Twin3D($('#twin'));
twin.dist = 19;
$('#gauge').insertAdjacentHTML('afterbegin', gaugeSVG());

async function tick() {
  try { ST = await API.get('/api/state'); } catch (e) { return; }
  const s = ST.sites[0]; SEL = s.id;
  $('#mTime').textContent = (ST.realtime ? '● LIVE ' : '▶ ×' + ST.speed + ' ') + ST.sim_time.slice(5);
  $('#mWx').textContent = `${ST.live ? '기상청 실황' : '시나리오'} · ${ST.weather.pressure}hPa · 강수 ${ST.weather.rain}mm`;
  const c = s.current.radon, lv = s.level, fp = s.forecast_planned || [];
  const v = $('#mNow'); v.className = 'val num c-' + lv.code;
  if (s.sensor_fault) v.textContent = 'ERR'; else animNum(v, c);
  $('#mLv').className = 'lv c-' + lv.code; $('#mLv').textContent = s.sensor_fault ? '센서 이상' : lv.label + ' 단계';
  setGauge($('#gauge'), s.sensor_fault ? 0 : c, fp[5]);
  $('#mTemp').textContent = `🌡 ${s.current.temp}℃ · 💧 ${s.current.rh}%`;
  $('#mFan').innerHTML = fanPill(s.fan);

  let cls = 'wait', ic = '⏳', t = '', sub = '';
  if (s.sensor_fault) { cls = 'no'; ic = '⚠️'; t = '진입 금지 — 센서 이상'; sub = '측정 복구 전까지 환기팬 연속 가동 중'; }
  else if (c <= 100 && (fp[2] ?? c) <= 100) { cls = 'ok'; ic = '✅'; t = '진입 가능'; sub = '안전 단계 유지 · 30분 후 예측 ' + fmt0(fp[2]) + ' Bq/㎥'; }
  else if (s.phase === 'pre') { cls = 'wait'; ic = '🌀'; t = '초기환기 진행 중'; sub = '환기 완료 시 알림을 보내드립니다'; }
  else { cls = 'no'; ic = '⛔'; t = '진입 보류'; sub = `현재 ${fmt0(c)} Bq/㎥ — 출입 등록 시 자동 환기`; }
  const g = $('#gate'); g.className = 'gate ' + cls; g.querySelector('.ic').textContent = ic; $('#gateT').textContent = t; $('#gateS').textContent = sub;
  twin.set({ c: s.sensor_fault ? 0 : c, fan: s.fan, level: lv.code, gate: cls, occupied: !!(s.next_entry && s.next_entry.active) });

  $('#fcGrid').innerHTML = [2, 5, 8, 11].map((k) => {
    const val = fp[k]; const l = val == null ? { code: 'safe' } : levelOf(val);
    return `<div>${(k + 1) * 10}분 후<b class="c-${l.code}">${fmt0(val)}</b></div>`;
  }).join('');
  const ne = s.next_entry;
  $('#mEntry').innerHTML = ne ? `<span>${esc(ne.label)}</span><b>${ne.active ? '진행 중 ~' + ne.end : ne.start.slice(6) + ' 시작'}</b>` : '<span>예정 없음</span>';
  const st = s.stats;
  $('#mStats').innerHTML = `
    <div class="mrow"><span>환기팬 가동</span><b>${st.run_hours_24h}시간 <small style="color:var(--muted)">/ 기존 ${st.legacy_hours}h</small></b></div>
    <div class="mrow"><span>전력 절감</span><b class="c-safe num">${st.saving_pct}%</b></div>
    <div class="mrow"><span>출입시간 평균/최고</span><b>${fmt0(st.entry_avg)} / ${fmt0(st.entry_max)}</b></div>
    <div class="mrow"><span>AI 제어 판단</span><span style="text-align:right;max-width:65%;color:var(--text)">${esc(s.reason)}</span></div>`;
  if (ST.last_event_id > LAST) events();
}

async function chart() {
  if (!SEL || TAB !== 'ai') return;
  const h = await API.get(`/api/history?site=${SEL}&hours=6`);
  drawRadonChart($('#mChart'), h, { compact: true });
}

async function events() {
  const evs = (await API.get('/api/events?since=' + LAST)).filter((e) => e.kind !== 'fan');
  const first = LAST === 0;
  LAST = ST.last_event_id;
  if (!evs.length) return;
  $('#mEvents').insertAdjacentHTML('afterbegin', evs.reverse().map((e) => `<div class="ev ${e.kind}"><span class="t">${e.t}</span><span class="m">${esc(e.msg)}</span></div>`).join(''));
  if (first) return;
  const imp = evs.filter((e) => ['ok', 'warn', 'danger'].includes(e.kind));
  imp.slice(0, 2).forEach((e) => {
    toast(e.msg, e.kind);
    if (window.Notification && Notification.permission === 'granted') {
      try { new Notification('라돈 안심 AI', { body: e.msg, icon: '/icon.svg' }); } catch (x) { /* 일부 브라우저 미지원 */ }
    }
    if (navigator.vibrate) navigator.vibrate(e.kind === 'danger' ? [200, 100, 200] : 120);
  });
  if (TAB !== 'alert' && imp.length) { UNREAD += imp.length; $('#dot').style.display = 'inline'; $('#dot').textContent = UNREAD; }
}

document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('button'); if (!b) return;
  if (b.dataset.tab) {
    TAB = b.dataset.tab;
    document.querySelectorAll('nav.tabs button').forEach((x) => x.classList.toggle('on', x === b));
    document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('on', x.id === 'tab-' + TAB));
    if (TAB === 'alert') { UNREAD = 0; $('#dot').style.display = 'none'; }
    if (TAB === 'home') setTimeout(() => twin._resize(), 50);
    chart();
  }
  if (b.dataset.quick) {
    await API.post('/api/entries', { site: SEL, quick_minutes: +b.dataset.quick, duration: 60, label: '모바일 출입 등록' });
    toast(`${b.dataset.quick}분 후 진입 등록 완료 — 자동 초기환기 예약`, 'ok'); tick();
  }
  if (b.id === 'mBrief') {
    $('#mBriefOut').textContent = '✨ 작성 중…';
    const r = await API.post('/api/ai/briefing', { site: SEL });
    $('#mBriefOut').textContent = r.text || r.error; $('#mBriefMeta').textContent = '생성: ' + (r.engine || '-');
  }
  if (b.id === 'notiBtn' && window.Notification) {
    const p = await Notification.requestPermission();
    toast(p === 'granted' ? '알림이 켜졌습니다.' : '브라우저에서 알림이 허용되지 않았습니다.');
  }
});
window.addEventListener('resize', chart);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
tick(); setInterval(tick, 1000); setInterval(chart, 3000);
