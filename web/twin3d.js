/* =========================================================================
   3D 디지털 트윈 — A역 배수펌프실 (순수 Canvas 2D 원근 투영, 외부 라이브러리 없음)
   - 라돈 입자: 집수정·바닥 균열에서 방출, 농도에 비례한 개수·단계별 색
   - 환기팬 가동 시: 출입문 → 환기팬 기류, 입자 배출
   - AI 스캔 빔, 출입문 진입 표시등, 작업자 표시
   ========================================================================= */
(function () {
  const LV = { safe: [46, 245, 165], interest: [255, 216, 74], caution: [255, 143, 63], danger: [255, 61, 104] };
  const ROOM = { x0: -5, x1: 5, z0: -3.5, z1: 3.5, h: 3.6 };
  const FAN = { x: -5, y: 2.5, z: -1.0, r: 0.72 };
  const PIT = { x0: 2.0, x1: 4.4, z0: -3.3, z1: -1.3, d: -1.1, w: -0.45 };
  const DOOR = { x: 5, z0: 0.9, z1: 2.3, h: 2.3 };
  const SENSOR = { x: -1.6, y: 1.7, z: -3.5 };

  function Twin(canvas) {
    this.c = canvas; this.g = canvas.getContext('2d');
    this.state = { c: 0, fan: false, level: 'safe', gate: 'no', occupied: false, phase: 'idle' };
    this.yaw = 0.62; this.pitch = 0.47; this.dist = 17; this.auto = true; this.t = 0; this.fanAng = 0;
    this.parts = []; this.flows = []; this.sprites = {};
    this.lastInteract = 0;
    this._bind(); this._resize();
    for (let i = 0; i < 40; i++) this.flows.push(this._newFlow(Math.random()));
    window.addEventListener('resize', () => this._resize());
    requestAnimationFrame((ts) => this._loop(ts));
  }

  Twin.prototype._resize = function () {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.W = this.c.clientWidth; this.H = this.c.clientHeight;
    this.c.width = this.W * dpr; this.c.height = this.H * dpr;
    this.g.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  Twin.prototype._bind = function () {
    let drag = null;
    const down = (x, y) => { drag = { x, y, yaw: this.yaw, pitch: this.pitch }; this.auto = false; };
    const move = (x, y) => {
      if (!drag) return;
      this.yaw = drag.yaw + (x - drag.x) * 0.008;
      this.pitch = Math.max(0.12, Math.min(1.1, drag.pitch + (y - drag.y) * 0.005));
      this.lastInteract = performance.now();
    };
    const up = () => { drag = null; this.lastInteract = performance.now(); };
    this.c.addEventListener('mousedown', (e) => down(e.clientX, e.clientY));
    window.addEventListener('mousemove', (e) => move(e.clientX, e.clientY));
    window.addEventListener('mouseup', up);
    this.c.addEventListener('touchstart', (e) => { const t = e.touches[0]; down(t.clientX, t.clientY); }, { passive: true });
    this.c.addEventListener('touchmove', (e) => { const t = e.touches[0]; move(t.clientX, t.clientY); }, { passive: true });
    this.c.addEventListener('touchend', up);
    this.c.addEventListener('wheel', (e) => { e.preventDefault(); this.dist = Math.max(11, Math.min(26, this.dist + e.deltaY * 0.01)); this.lastInteract = performance.now(); }, { passive: false });
  };

  Twin.prototype.set = function (s) { Object.assign(this.state, s); };

  // ---- 투영 ---------------------------------------------------------------
  Twin.prototype.P = function (x, y, z) {
    const ca = Math.cos(this.yaw), sa = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const x1 = x * ca - z * sa, z1 = x * sa + z * ca, yy = y - 1.2;
    const y2 = yy * cp - z1 * sp, z2 = yy * sp + z1 * cp;
    const d = this.dist - z2, f = Math.min(this.W * 0.9, this.H * 1.55);
    return [this.W / 2 + (f * x1) / d, this.H * 0.56 - (f * y2) / d, d];
  };

  Twin.prototype._line = function (a, b, col, w, glow) {
    const g = this.g, p = this.P(...a), q = this.P(...b);
    g.strokeStyle = col; g.lineWidth = w || 1;
    if (glow) { g.shadowColor = col; g.shadowBlur = glow; }
    g.beginPath(); g.moveTo(p[0], p[1]); g.lineTo(q[0], q[1]); g.stroke();
    g.shadowBlur = 0;
  };

  Twin.prototype._poly = function (pts, fill, stroke, w, glow) {
    const g = this.g; g.beginPath();
    pts.forEach((pt, i) => { const p = this.P(...pt); i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]); });
    g.closePath();
    if (fill) { g.fillStyle = fill; g.fill(); }
    if (stroke) { g.strokeStyle = stroke; g.lineWidth = w || 1; if (glow) { g.shadowColor = stroke; g.shadowBlur = glow; } g.stroke(); g.shadowBlur = 0; }
  };

  Twin.prototype._circle3 = function (cx, cy, cz, r, plane, n) {
    const pts = [];
    for (let i = 0; i <= (n || 28); i++) {
      const a = (i / (n || 28)) * Math.PI * 2;
      if (plane === 'yz') pts.push([cx, cy + Math.sin(a) * r, cz + Math.cos(a) * r]);
      else pts.push([cx + Math.cos(a) * r, cy, cz + Math.sin(a) * r]);
    }
    return pts;
  };

  Twin.prototype._label = function (pos, text, sub, col) {
    const g = this.g, p = this.P(...pos);
    const lx = p[0] + 18, ly = p[1] - 22;
    g.strokeStyle = col || 'rgba(34,227,255,.8)'; g.lineWidth = 1;
    g.beginPath(); g.moveTo(p[0], p[1]); g.lineTo(lx, ly); g.lineTo(lx + 8, ly); g.stroke();
    g.fillStyle = col || '#22e3ff'; g.beginPath(); g.arc(p[0], p[1], 2.5, 0, 7); g.fill();
    g.font = '600 12px Pretendard, "Noto Sans KR", sans-serif';
    const w = Math.max(g.measureText(text).width, sub ? g.measureText(sub).width * 0.9 : 0) + 16;
    const h = sub ? 34 : 22;
    g.fillStyle = 'rgba(3,12,32,.82)'; g.strokeStyle = col || 'rgba(34,227,255,.6)';
    roundRect(g, lx + 8, ly - h / 2, w, h, 6); g.fill(); g.stroke();
    g.fillStyle = '#e8f1ff'; g.fillText(text, lx + 16, ly + (sub ? -3 : 4));
    if (sub) { g.font = '11px Pretendard, "Noto Sans KR", sans-serif'; g.fillStyle = col || '#9db4d6'; g.fillText(sub, lx + 16, ly + 12); }
  };

  function roundRect(g, x, y, w, h, r) {
    g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }

  Twin.prototype._sprite = function (rgb) {
    const k = rgb.join(',');
    if (this.sprites[k]) return this.sprites[k];
    const s = document.createElement('canvas'); s.width = s.height = 32;
    const g = s.getContext('2d'), gr = g.createRadialGradient(16, 16, 0, 16, 16, 16);
    gr.addColorStop(0, `rgba(255,255,255,1)`); gr.addColorStop(0.18, `rgba(${k},1)`); gr.addColorStop(0.5, `rgba(${k},.35)`); gr.addColorStop(1, `rgba(${k},0)`);
    g.fillStyle = gr; g.fillRect(0, 0, 32, 32);
    return (this.sprites[k] = s);
  };

  // ---- 입자 ---------------------------------------------------------------
  Twin.prototype._spawn = function () {
    const fromPit = Math.random() < 0.65;
    const p = fromPit
      ? { x: PIT.x0 + Math.random() * (PIT.x1 - PIT.x0), y: PIT.w, z: PIT.z0 + Math.random() * (PIT.z1 - PIT.z0) }
      : { x: ROOM.x0 + 0.4 + Math.random() * 9.2, y: 0.02, z: ROOM.z0 + 0.4 + Math.random() * 6.2 };
    p.vx = (Math.random() - 0.5) * 0.01; p.vy = 0.004 + Math.random() * 0.006; p.vz = (Math.random() - 0.5) * 0.01;
    p.a = 0; p.ph = Math.random() * 6.28; p.hy = 0.25 + Math.random() * Math.random() * 2.6; // 라돈은 공기보다 무거워 낮은 높이에 분포
    return p;
  };

  Twin.prototype._newFlow = function (u) {
    return { u: u === undefined ? 0 : u, z: DOOR.z0 + Math.random() * (DOOR.z1 - DOOR.z0), y: 0.4 + Math.random() * 1.8, sp: 0.004 + Math.random() * 0.004 };
  };

  Twin.prototype._updateParticles = function (dt) {
    const s = this.state;
    const target = Math.round(Math.max(18, Math.min(720, s.c * 0.9)));
    if (this.parts.length < target) for (let i = 0; i < Math.min(8, target - this.parts.length); i++) this.parts.push(this._spawn());
    if (this.parts.length > target + 20 && !s.fan) this.parts.splice(0, 3);
    const k = dt / 16.7;
    for (let i = this.parts.length - 1; i >= 0; i--) {
      const p = this.parts[i];
      p.ph += 0.02 * k;
      p.vx += (Math.random() - 0.5) * 0.0018 * k + Math.sin(p.ph) * 0.0002;
      p.vz += (Math.random() - 0.5) * 0.0018 * k;
      p.vy += (p.hy - p.y) * 0.00035 * k;
      if (s.fan) {
        const dx = FAN.x - p.x, dy = FAN.y - p.y, dz = FAN.z - p.z, d = Math.hypot(dx, dy, dz) + 0.3;
        const f = 0.0032 * k / Math.max(0.6, d * 0.35);
        p.vx += dx / d * f; p.vy += dy / d * f; p.vz += dz / d * f;
        if (d < FAN.r + 0.25) { this.parts.splice(i, 1); continue; }
      }
      p.vx *= 0.985; p.vy *= 0.985; p.vz *= 0.985;
      p.x += p.vx * k; p.y += p.vy * k; p.z += p.vz * k;
      if (p.x < ROOM.x0 + 0.1) { p.x = ROOM.x0 + 0.1; p.vx *= -0.5; }
      if (p.x > ROOM.x1 - 0.1) { p.x = ROOM.x1 - 0.1; p.vx *= -0.5; }
      if (p.z < ROOM.z0 + 0.1) { p.z = ROOM.z0 + 0.1; p.vz *= -0.5; }
      if (p.z > ROOM.z1 - 0.1) { p.z = ROOM.z1 - 0.1; p.vz *= -0.5; }
      const inPit = p.x > PIT.x0 && p.x < PIT.x1 && p.z > PIT.z0 && p.z < PIT.z1;
      const floor = inPit ? PIT.w : 0.02;
      if (p.y < floor) { p.y = floor; p.vy = Math.abs(p.vy) * 0.4; }
      if (p.y > ROOM.h - 0.1) { p.y = ROOM.h - 0.1; p.vy *= -0.5; }
      p.a = Math.min(1, p.a + 0.02 * k);
    }
  };

  // ---- 그리기 ---------------------------------------------------------------
  Twin.prototype._draw = function () {
    const g = this.g, s = this.state, t = this.t;
    g.clearRect(0, 0, this.W, this.H);
    const rgb = LV[s.level] || LV.safe, col = `rgb(${rgb})`;

    // 바닥 그리드 (외부까지 확장)
    for (let x = -9; x <= 9; x += 0.5) {
      const inside = x >= ROOM.x0 && x <= ROOM.x1;
      this._line([x, 0, -7], [x, 0, 7], inside ? 'rgba(34,227,255,.16)' : 'rgba(61,139,255,.06)', 1);
    }
    for (let z = -7; z <= 7; z += 0.5) this._line([-9, 0, z], [9, 0, z], 'rgba(61,139,255,.07)', 1);

    // 농도 안개 (바닥 히트맵)
    const c0 = this.P(0, 0.02, 0), r0 = this.P(4.5, 0.02, 0);
    const rad = Math.abs(r0[0] - c0[0]) * 1.4 + 40;
    const fog = g.createRadialGradient(c0[0], c0[1], 0, c0[0], c0[1], rad);
    const alpha = Math.min(0.42, 0.06 + s.c / 1500);
    fog.addColorStop(0, `rgba(${rgb},${alpha})`); fog.addColorStop(1, `rgba(${rgb},0)`);
    g.fillStyle = fog; g.beginPath(); g.ellipse(c0[0], c0[1], rad, rad * 0.45, 0, 0, 7); g.fill();

    // 벽면 (반투명) — 뒤·좌·우
    const wallFill = 'rgba(40,110,255,.07)';
    this._poly([[ROOM.x0, 0, ROOM.z0], [ROOM.x1, 0, ROOM.z0], [ROOM.x1, ROOM.h, ROOM.z0], [ROOM.x0, ROOM.h, ROOM.z0]], wallFill);
    this._poly([[ROOM.x0, 0, ROOM.z0], [ROOM.x0, 0, ROOM.z1], [ROOM.x0, ROOM.h, ROOM.z1], [ROOM.x0, ROOM.h, ROOM.z0]], 'rgba(40,110,255,.05)');
    this._poly([[ROOM.x1, 0, ROOM.z0], [ROOM.x1, 0, ROOM.z1], [ROOM.x1, ROOM.h, ROOM.z1], [ROOM.x1, ROOM.h, ROOM.z0]], 'rgba(40,110,255,.05)');
    // 벽 패널 라인
    for (let x = ROOM.x0 + 1; x < ROOM.x1; x += 1) this._line([x, 0, ROOM.z0], [x, ROOM.h, ROOM.z0], 'rgba(34,227,255,.07)', 1);
    for (let y = 0.9; y < ROOM.h; y += 0.9) this._line([ROOM.x0, y, ROOM.z0], [ROOM.x1, y, ROOM.z0], 'rgba(34,227,255,.07)', 1);

    // 집수정
    const P_ = PIT;
    this._poly([[P_.x0, 0, P_.z0], [P_.x1, 0, P_.z0], [P_.x1, P_.d, P_.z0], [P_.x0, P_.d, P_.z0]], 'rgba(20,60,140,.35)', 'rgba(34,227,255,.35)');
    this._poly([[P_.x0, 0, P_.z0], [P_.x0, 0, P_.z1], [P_.x0, P_.d, P_.z1], [P_.x0, P_.d, P_.z0]], 'rgba(20,60,140,.3)', 'rgba(34,227,255,.35)');
    const wv = 0.03 * Math.sin(t * 0.002);
    this._poly([[P_.x0, P_.w + wv, P_.z0], [P_.x1, P_.w - wv, P_.z0], [P_.x1, P_.w + wv, P_.z1], [P_.x0, P_.w - wv, P_.z1]], 'rgba(30,140,255,.35)', 'rgba(80,200,255,.7)', 1.2, 8);
    for (let k = 0; k < 4; k++) {  // 물결
      const z = P_.z0 + 0.3 + k * 0.45 + 0.2 * Math.sin(t * 0.0015 + k);
      this._line([P_.x0 + 0.1, P_.w, z], [P_.x1 - 0.1, P_.w, z], 'rgba(150,220,255,.35)', 1);
    }
    this._poly([[P_.x0, 0, P_.z0], [P_.x1, 0, P_.z0], [P_.x1, 0, P_.z1], [P_.x0, 0, P_.z1]], null, 'rgba(34,227,255,.9)', 1.6, 10);

    // 배수펌프 2대 (원통)
    [[0.6, -2.5], [1.5, -2.5]].forEach(([px, pz]) => {
      const bot = this._circle3(px, 0, pz, 0.32, 'xz'), top = this._circle3(px, 1.15, pz, 0.32, 'xz');
      this._poly(top, 'rgba(61,139,255,.25)', 'rgba(120,200,255,.85)', 1.2, 6);
      this._poly(bot, null, 'rgba(120,200,255,.5)', 1);
      for (let i = 0; i < bot.length - 1; i += 7) this._line(bot[i], top[i], 'rgba(120,200,255,.55)', 1);
      const mt = this._circle3(px, 1.55, pz, 0.22, 'xz');
      this._poly(mt, 'rgba(139,92,255,.3)', 'rgba(170,140,255,.9)', 1.2, 6);
      this._line([px, 1.15, pz], [px, 1.55, pz], 'rgba(170,140,255,.7)', 1);
      this._line([px + 0.32, 0.6, pz], [P_.x0 + 0.2, 0.6, pz], 'rgba(120,200,255,.6)', 2);
      this._line([P_.x0 + 0.2, 0.6, pz], [P_.x0 + 0.2, P_.w, pz], 'rgba(120,200,255,.6)', 2);
    });

    // 환기팬 (좌측 벽, YZ 평면)
    const fanOn = s.fan, fanCol = fanOn ? 'rgba(54,185,255,1)' : 'rgba(120,150,190,.7)';
    const ring = this._circle3(FAN.x, FAN.y, FAN.z, FAN.r, 'yz', 36);
    this._poly(ring, fanOn ? 'rgba(54,185,255,.18)' : 'rgba(40,60,100,.25)', fanCol, 2, fanOn ? 18 : 0);
    this._poly(this._circle3(FAN.x, FAN.y, FAN.z, FAN.r + 0.12, 'yz', 36), null, 'rgba(34,227,255,.35)', 1);
    for (let b = 0; b < 4; b++) {
      const a = this.fanAng + (b * Math.PI) / 2;
      const tip = [FAN.x, FAN.y + Math.sin(a) * FAN.r * 0.9, FAN.z + Math.cos(a) * FAN.r * 0.9];
      const side = [FAN.x, FAN.y + Math.sin(a + 0.5) * FAN.r * 0.55, FAN.z + Math.cos(a + 0.5) * FAN.r * 0.55];
      this._poly([[FAN.x, FAN.y, FAN.z], tip, side], fanOn ? 'rgba(54,185,255,.55)' : 'rgba(120,150,190,.35)', fanCol, 1);
    }
    // 덕트 (벽 밖으로)
    [[0.45, 0.45], [-0.45, 0.45], [0.45, -0.45], [-0.45, -0.45]].forEach(([dy, dz]) =>
      this._line([FAN.x, FAN.y + dy, FAN.z + dz], [FAN.x - 1.8, FAN.y + dy, FAN.z + dz], 'rgba(34,227,255,.35)', 1));
    this._poly([[FAN.x - 1.8, FAN.y - 0.45, FAN.z - 0.45], [FAN.x - 1.8, FAN.y + 0.45, FAN.z - 0.45], [FAN.x - 1.8, FAN.y + 0.45, FAN.z + 0.45], [FAN.x - 1.8, FAN.y - 0.45, FAN.z + 0.45]], null, 'rgba(34,227,255,.5)', 1);

    // 기류 (팬 가동 시 출입문 → 팬)
    if (fanOn) {
      g.globalCompositeOperation = 'lighter';
      this.flows.forEach((f) => {
        f.u += f.sp; if (f.u > 1) Object.assign(f, this._newFlow(0));
        const pt = (u) => {
          const x = DOOR.x - 0.2 + (FAN.x - DOOR.x + 0.4) * u;
          const z = f.z + (FAN.z - f.z) * u * u;
          const y = f.y + (FAN.y - f.y) * u + Math.sin(u * 3.14) * 0.5;
          return [x, y, z];
        };
        const a = this.P(...pt(f.u)), b = this.P(...pt(Math.max(0, f.u - 0.06)));
        const gr = g.createLinearGradient(a[0], a[1], b[0], b[1]);
        gr.addColorStop(0, 'rgba(120,230,255,.85)'); gr.addColorStop(1, 'rgba(120,230,255,0)');
        g.strokeStyle = gr; g.lineWidth = 2; g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke();
      });
      g.globalCompositeOperation = 'source-over';
    }

    // 라돈 센서
    const blink = (Math.floor(t / 600) % 2) === 0;
    this._poly([[SENSOR.x - 0.35, SENSOR.y - 0.25, SENSOR.z + 0.02], [SENSOR.x + 0.35, SENSOR.y - 0.25, SENSOR.z + 0.02], [SENSOR.x + 0.35, SENSOR.y + 0.25, SENSOR.z + 0.02], [SENSOR.x - 0.35, SENSOR.y + 0.25, SENSOR.z + 0.02]], 'rgba(10,30,70,.9)', 'rgba(46,245,165,.8)', 1.2, 8);
    const led = this.P(SENSOR.x + 0.22, SENSOR.y + 0.12, SENSOR.z + 0.03);
    g.fillStyle = blink ? '#2ef5a5' : '#0c4a33'; g.shadowColor = '#2ef5a5'; g.shadowBlur = blink ? 14 : 0;
    g.beginPath(); g.arc(led[0], led[1], 3, 0, 7); g.fill(); g.shadowBlur = 0;
    // 센서 신호 파동
    const sp0 = this.P(SENSOR.x, SENSOR.y, SENSOR.z + 0.05);
    for (let k = 0; k < 3; k++) {
      const rr = ((t / 25 + k * 14) % 42) + 6;
      g.strokeStyle = `rgba(46,245,165,${0.5 * (1 - rr / 48)})`; g.lineWidth = 1.2;
      g.beginPath(); g.arc(sp0[0], sp0[1], rr, -2.4, -0.7); g.stroke();
    }

    // 출입문 (우측 벽)
    const gateCol = s.gate === 'ok' ? '#2ef5a5' : s.gate === 'wait' ? '#ffd84a' : '#ff3d68';
    this._poly([[DOOR.x, 0, DOOR.z0], [DOOR.x, DOOR.h, DOOR.z0], [DOOR.x, DOOR.h, DOOR.z1], [DOOR.x, 0, DOOR.z1]], 'rgba(20,40,90,.35)', gateCol, 1.6, 12);
    const lamp = this.P(DOOR.x, DOOR.h + 0.25, (DOOR.z0 + DOOR.z1) / 2);
    g.fillStyle = gateCol; g.shadowColor = gateCol; g.shadowBlur = 22; g.beginPath(); g.arc(lamp[0], lamp[1], 5, 0, 7); g.fill(); g.shadowBlur = 0;

    // 작업자 (출입 중)
    if (s.occupied) {
      const wx = 3.4, wz = 1.6, bob = Math.sin(t * 0.004) * 0.03;
      const head = this.P(wx, 1.72 + bob, wz), neck = this.P(wx, 1.52 + bob, wz), hip = this.P(wx, 0.9, wz);
      const lf = this.P(wx - 0.18, 0, wz + 0.1), rf = this.P(wx + 0.18, 0, wz - 0.1), lh = this.P(wx - 0.35, 1.05, wz), rh = this.P(wx + 0.35, 1.1, wz);
      g.strokeStyle = '#ffd84a'; g.lineWidth = 3; g.shadowColor = '#ffd84a'; g.shadowBlur = 12; g.lineCap = 'round';
      g.beginPath(); g.moveTo(neck[0], neck[1]); g.lineTo(hip[0], hip[1]); g.moveTo(hip[0], hip[1]); g.lineTo(lf[0], lf[1]);
      g.moveTo(hip[0], hip[1]); g.lineTo(rf[0], rf[1]); g.moveTo(lh[0], lh[1]); g.lineTo(neck[0], neck[1]); g.lineTo(rh[0], rh[1]); g.stroke();
      g.beginPath(); g.arc(head[0], head[1], Math.abs(neck[1] - head[1]) * 0.75, 0, 7); g.stroke(); g.shadowBlur = 0; g.lineCap = 'butt';
    }

    // 라돈 입자 (가산 혼합 발광)
    g.globalCompositeOperation = 'lighter';
    const spr = this._sprite(rgb);
    const ps = this.parts.map((p) => [this.P(p.x, p.y, p.z), p]).sort((a, b) => b[0][2] - a[0][2]);
    for (const [q, p] of ps) {
      const size = Math.max(3, 150 / q[2]) * (0.8 + 0.3 * Math.sin(p.ph * 3));
      g.globalAlpha = p.a * 0.9;
      g.drawImage(spr, q[0] - size, q[1] - size, size * 2, size * 2);
    }
    g.globalAlpha = 1;

    // AI 스캔 빔
    const sy = ((Math.sin(t * 0.0009) + 1) / 2) * ROOM.h;
    this._poly([[ROOM.x0, sy, ROOM.z0], [ROOM.x1, sy, ROOM.z0], [ROOM.x1, sy, ROOM.z1], [ROOM.x0, sy, ROOM.z1]], 'rgba(34,227,255,.05)', 'rgba(34,227,255,.55)', 1.2, 14);
    g.globalCompositeOperation = 'source-over';

    // 방 모서리 (네온)
    const E = (a, b, al) => this._line(a, b, `rgba(34,227,255,${al})`, 1.6, 10);
    const { x0, x1, z0, z1, h } = ROOM;
    E([x0, 0, z0], [x1, 0, z0], 0.9); E([x0, 0, z0], [x0, 0, z1], 0.9); E([x1, 0, z0], [x1, 0, z1], 0.9); E([x0, 0, z1], [x1, 0, z1], 0.5);
    E([x0, h, z0], [x1, h, z0], 0.8); E([x0, h, z0], [x0, h, z1], 0.6); E([x1, h, z0], [x1, h, z1], 0.6); E([x0, h, z1], [x1, h, z1], 0.25);
    E([x0, 0, z0], [x0, h, z0], 0.9); E([x1, 0, z0], [x1, h, z0], 0.9); E([x0, 0, z1], [x0, h, z1], 0.35); E([x1, 0, z1], [x1, h, z1], 0.35);

    // 라벨 (작은 화면은 핵심만)
    if (this.W < 560) {
      this._label([FAN.x, FAN.y + FAN.r, FAN.z], fanOn ? '환기팬 가동' : '환기팬 정지', null, fanOn ? '#36b9ff' : '#9db4d6');
      this._label([DOOR.x, DOOR.h, DOOR.z1], s.gate === 'ok' ? '진입 가능' : s.gate === 'wait' ? '초기환기 중' : '진입 보류', null, gateCol);
      return;
    }
    this._label([FAN.x, FAN.y + FAN.r, FAN.z], '환기팬', fanOn ? '가동 중 · 200㎥/h' : '정지', fanOn ? '#36b9ff' : '#9db4d6');
    this._label([SENSOR.x, SENSOR.y + 0.25, SENSOR.z], '라돈 센서 RG-A01', `${Math.round(s.c)} Bq/㎥`, col);
    this._label([(P_.x0 + P_.x1) / 2, 0, P_.z1], '집수정', '지하수 라돈 방출원', '#5fb8ff');
    this._label([1.05, 1.6, -2.5], '배수펌프', null, '#b9a4ff');
    this._label([DOOR.x, DOOR.h, DOOR.z1], '출입문', s.gate === 'ok' ? '진입 가능' : s.gate === 'wait' ? '초기환기 중' : '진입 보류', gateCol);
  };

  Twin.prototype._loop = function (ts) {
    const dt = Math.min(50, ts - (this._last || ts)); this._last = ts; this.t = ts;
    if (!this.auto && ts - this.lastInteract > 5000) this.auto = true;
    if (this.auto) this.yaw += dt * 0.00009;
    this.fanAng += this.state.fan ? dt * 0.018 : dt * 0.0004;
    this._updateParticles(dt);
    if (this.W > 0) this._draw();
    requestAnimationFrame((t) => this._loop(t));
  };

  window.Twin3D = Twin;
})();
