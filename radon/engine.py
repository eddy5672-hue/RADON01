"""시스템 엔진: 가상 단말기·기상·AI 예측·환기 제어를 시뮬레이션 시계에 맞춰 구동."""
from __future__ import annotations

import copy
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

from .controller import ControlParams, Entry, FanController
from .predictor import MIN_HISTORY, RadonPredictor
from .sim import DEFAULT_SITES, RadonRoom, SiteConfig, level_of
from .weather import OverlayWeather, SimWeather

KST = timezone(timedelta(hours=9))
HISTORY_KEEP = 7 * 144  # 7일 (10분 간격)


def now_kst() -> datetime:
    return datetime.now(KST).replace(tzinfo=None, second=0, microsecond=0)


class SiteRuntime:
    def __init__(self, cfg: SiteConfig, params: ControlParams):
        self.cfg = cfg
        self.room = RadonRoom(cfg)
        self.ctl = FanController(cfg, params)
        self.hist: deque = deque(maxlen=HISTORY_KEEP)   # 10분 기록
        self.minute: deque = deque(maxlen=180)          # 최근 3시간 1분 측정값
        self.slot_fan = 0
        self.slot_n = 0
        self.natural_fc: list | None = None
        self.planned_fc: list | None = None
        self.alert_rank = 0

    def current(self) -> dict:
        recent = list(self.minute)[-10:]
        if not recent:
            return {"radon": 0, "temp": 0, "rh": 0}
        return {
            "radon": round(sum(r.radon for r in recent) / len(recent), 1),  # 10분 이동평균
            "radon_raw": recent[-1].radon,
            "temp": recent[-1].temp,
            "rh": recent[-1].rh,
        }


class Engine:
    def __init__(self, predictor: RadonPredictor, sites: list[SiteConfig] | None = None,
                 speed: float = 60.0, warmup_hours: int = 48, start: datetime | None = None,
                 weather=None, realtime: bool = False):
        self.lock = threading.RLock()
        self.predictor = predictor
        self.params = ControlParams()
        self.sites = {c.id: SiteRuntime(c, self.params) for c in (sites or DEFAULT_SITES)}
        self.sim_time = (start or now_kst()) - timedelta(hours=warmup_hours)
        self.weather = weather or OverlayWeather(SimWeather(self.sim_time + timedelta(hours=warmup_hours), seed=7))
        self.realtime = realtime         # True: 실제 시각에 동기화 (실시간 모드)
        self.speed = 1.0 if realtime else speed  # 시뮬레이션 초 / 실제 초
        self.paused = False
        self.allow_remote_control = True
        self.entries: list[Entry] = [
            Entry("pump-a", "02:00", "05:00", "야간 배수펌프 점검"),
            Entry("pump-a", "14:00", "14:40", "주간 설비 순회점검"),
        ]
        self.events: deque = deque(maxlen=300)
        self._eid = 0
        self._acc = 0.0
        for _ in range(warmup_hours * 60):
            self._step_minute(log=True)

    @property
    def live(self) -> bool:
        return getattr(self.weather, "is_live", False)

    def clone(self) -> "Engine":
        """세션용 복제 (예측모델·기상 기반자료는 공유)."""
        with self.lock:
            memo = {id(self.predictor): self.predictor, id(self.lock): None}
            new = copy.deepcopy(self, memo)
        new.lock = threading.RLock()
        return new

    def tick(self, dt: float):
        """dt(실제 초)만큼 진행. 실시간 모드는 벽시계에 맞춘다."""
        if self.paused:
            return
        if self.realtime:
            n = int((now_kst() - self.sim_time).total_seconds() // 60)
        else:
            self._acc += dt * self.speed / 60.0
            n = int(self._acc)
            self._acc -= n
        if n > 0:
            with self.lock:
                for _ in range(min(n, 600)):
                    self._step_minute()

    # ------------------------------------------------------------------
    def _event(self, kind: str, msg: str, site: str | None = None):
        self._eid += 1
        self.events.append({"id": self._eid, "t": self.sim_time.strftime("%m-%d %H:%M"),
                            "kind": kind, "msg": msg, "site": site})

    def entries_for(self, site: str) -> list[Entry]:
        return [e for e in self.entries if e.site == site]

    def _weather_fcst(self):
        fp, fr = [], []
        for k in range(1, 13):
            w = self.weather.forecast(self.sim_time, self.sim_time + timedelta(minutes=10 * k))
            fp.append(w.pressure)
            fr.append(w.rain)
        return fp, fr

    def _step_minute(self, log: bool = True):
        t = self.sim_time
        w = self.weather.at(t)
        for sid, s in self.sites.items():
            c_now = s.current()["radon"] if s.minute else s.room.true_c
            fan, evs = s.ctl.decide(t, c_now, self.entries_for(sid), s.natural_fc, s.room.sensor_fault)
            if log:
                for kind, msg in evs:
                    self._event(kind, msg, sid)
            s.room.step(w, fan)
            r = s.room.read(t + timedelta(minutes=1), fan)
            s.minute.append(r)
            s.slot_fan += 1 if fan else 0
            s.slot_n += 1
            c_cur = s.current()["radon"]
            rank = 0 if c_cur <= 100 else 1 if c_cur <= 300 else 2 if c_cur <= 600 else 3
            if log and rank > s.alert_rank and rank >= 2 and not s.room.sensor_fault:
                self._event("danger" if rank == 3 else "warn",
                            f"[{s.cfg.name}] 라돈 농도 '{level_of(c_cur)['label']}' 단계 진입 ({c_cur:.0f} Bq/㎥)", sid)
                s.alert_rank = rank
            # 히스테리시스: 단계 기준의 90% 아래로 내려가야 재알림 가능
            if s.alert_rank >= 2 and c_cur < (300 if s.alert_rank == 2 else 600) * 0.9:
                s.alert_rank = 2 if c_cur > 300 else 1 if c_cur > 100 else 0
        self.sim_time = t + timedelta(minutes=1)
        if self.sim_time.minute % 10 == 0:
            self._record_slot(w)

    def _record_slot(self, w):
        fp, fr = self._weather_fcst()
        for sid, s in self.sites.items():
            cur = s.current()
            s.hist.append({
                "t": self.sim_time, "radon": None if s.room.sensor_fault else cur["radon"], "temp": cur["temp"], "rh": cur["rh"],
                "fan": s.slot_fan / max(1, s.slot_n), "pressure": round(w.pressure, 1),
                "rain": round(w.rain, 2), "out_temp": round(w.temp, 1),
            })
            s.slot_fan = s.slot_n = 0
            if s.room.sensor_fault:
                s.natural_fc = s.planned_fc = None
                continue
            hist, last_c = [], None
            for r in s.hist:  # 결측(센서 이상) 구간은 직전 값으로 보간
                if r["radon"] is None:
                    r = dict(r, radon=last_c if last_c is not None else 0.0)
                last_c = r["radon"]
                hist.append(r)
            s.natural_fc = self.predictor.forecast(sid, hist, [0.0] * 12, fp, fr)
            plan = s.ctl.plan(self.sim_time, self.entries_for(sid), cur["radon"], s.natural_fc)
            s.planned_fc = self.predictor.forecast(sid, hist, plan, fp, fr)
            s.plan = plan

    def advance(self, minutes: int):
        with self.lock:
            for _ in range(minutes):
                self._step_minute()

    # ------------------------------------------------------------------
    def stats(self, s: SiteRuntime) -> dict:
        h = list(s.hist)[-144:]
        run_h = sum(r["fan"] for r in h) / 6.0
        hours_span = len(h) / 6.0 or 1
        run_24 = run_h * 24 / hours_span
        legacy = s.cfg.legacy_hours
        occ_vals = []
        for e in self.entries_for(s.cfg.id):
            for st, en in e.occurrences(self.sim_time - timedelta(hours=24), self.sim_time):
                occ_vals += [r["radon"] for r in h if st < r["t"] <= en and r["radon"] is not None]
        return {
            "run_hours_24h": round(run_24, 1),
            "legacy_hours": legacy,
            "kwh_24h": round(run_24 * s.cfg.fan_kw, 1),
            "legacy_kwh": round(legacy * s.cfg.fan_kw, 1),
            "saving_pct": round(100 * (1 - run_24 / legacy), 1) if legacy else 0,
            "entry_avg": round(sum(occ_vals) / len(occ_vals), 1) if occ_vals else None,
            "entry_max": round(max(occ_vals), 1) if occ_vals else None,
        }

    def next_entry(self, sid: str):
        occ = self.sites[sid].ctl.next_occurrences(self.entries_for(sid), self.sim_time - timedelta(hours=6), 30)
        for st, en, e in occ:
            if en > self.sim_time:
                return {"label": e.label, "start": st.strftime("%m-%d %H:%M"), "end": en.strftime("%H:%M"),
                        "active": st <= self.sim_time,
                        "minutes_left": int((st - self.sim_time).total_seconds() // 60)}
        return None

    def site_state(self, sid: str) -> dict:
        s = self.sites[sid]
        cur = s.current()
        return {
            "id": sid, "name": s.cfg.name, "desc": s.cfg.desc,
            "current": cur, "level": level_of(cur["radon"]),
            "fan": s.ctl.fan, "mode": s.ctl.mode, "reason": s.ctl.reason, "phase": s.ctl.phase,
            "forecast_natural": s.natural_fc, "forecast_planned": s.planned_fc,
            "next_entry": self.next_entry(sid), "stats": self.stats(s),
            "sensor_fault": s.room.sensor_fault,
        }

    def state(self) -> dict:
        with self.lock:
            w = self.weather.at(self.sim_time)
            return {
                "sim_time": self.sim_time.strftime("%Y-%m-%d %H:%M"),
                "speed": self.speed, "paused": self.paused, "realtime": self.realtime, "live": self.live,
                "weather_source": getattr(self.weather, "label", "합성 기상"),
                "allow_remote_control": self.allow_remote_control,
                "weather": {"pressure": round(w.pressure, 1), "rain": round(w.rain, 1),
                            "temp": round(w.temp, 1), "humidity": round(w.humidity)},
                "sites": [self.site_state(sid) for sid in self.sites],
                "last_event_id": self._eid,
                "model": {"trained_at": self.predictor.trained_at, "source": self.predictor.source},
            }

    def history(self, sid: str, hours: float = 12) -> dict:
        with self.lock:
            s = self.sites[sid]
            n = int(hours * 6)
            h = list(s.hist)[-n:]
            occ = []
            for e in self.entries_for(sid):
                for st, en in e.occurrences(self.sim_time - timedelta(hours=hours), self.sim_time + timedelta(hours=3)):
                    occ.append([st.strftime("%Y-%m-%d %H:%M"), en.strftime("%Y-%m-%d %H:%M"), e.label])
            return {
                "t": [r["t"].strftime("%Y-%m-%d %H:%M") for r in h],
                "radon": [r["radon"] for r in h], "fan": [round(r["fan"], 2) for r in h],
                "temp": [r["temp"] for r in h], "rh": [r["rh"] for r in h],
                "pressure": [r["pressure"] for r in h], "rain": [r["rain"] for r in h],
                "forecast_natural": s.natural_fc, "forecast_planned": s.planned_fc,
                "plan": getattr(s, "plan", None), "entries": occ, "now": self.sim_time.strftime("%Y-%m-%d %H:%M"),
            }

    def events_since(self, since: int = 0, limit: int = 100) -> list:
        with self.lock:
            return [e for e in self.events if e["id"] > since][-limit:]

    # ---- 조작 ---------------------------------------------------------
    def set_mode(self, sid: str, mode: str):
        with self.lock:
            s = self.sites[sid]
            s.ctl.mode = mode
            s.ctl.manual_fan = s.ctl.fan
            self._event("info", f"[{s.cfg.name}] 운전 모드 변경 → {mode}", sid)

    def set_fan(self, sid: str, on: bool, source: str = "관제"):
        with self.lock:
            s = self.sites[sid]
            s.ctl.mode = "MANUAL"
            s.ctl.manual_fan = on
            self._event("info", f"[{s.cfg.name}] {source} 수동 조작: 환기팬 {'ON' if on else 'OFF'}", sid)

    def add_entry(self, e: Entry):
        with self.lock:
            self.entries.append(e)
            self._event("info", f"[{self.sites[e.site].cfg.name}] 출입 등록: {e.label} {e.start}~{e.end}"
                                f"{' (매일)' if e.daily else ''}", e.site)

    def remove_entry(self, eid: str):
        with self.lock:
            self.entries = [e for e in self.entries if e.id != eid]

    def sim_event(self, kind: str, sid: str | None = None):
        with self.lock:
            if kind == "rain":
                self.weather.add_rain(self.sim_time, hours=4, mm=12.0)
                self._event("info", "[시뮬레이션] 집중호우 발생 (4시간, 12mm/h) — 지하수 유입 증가 예상")
            elif kind == "lowpressure":
                self.weather.add_low_pressure(self.sim_time, hours=12, drop=12.0)
                self._event("info", "[시뮬레이션] 저기압 통과 (12시간, 최대 -12hPa)")
            elif kind == "surge" and sid:
                self.sites[sid].room.trigger_surge(3.0)
                self._event("info", f"[시뮬레이션] {self.sites[sid].cfg.name} 집수정 지하수 유입 급증", sid)
            elif kind == "fault" and sid:
                r = self.sites[sid].room
                r.sensor_fault = not r.sensor_fault
                self._event("warn", f"[시뮬레이션] {self.sites[sid].cfg.name} 센서 {'통신두절' if r.sensor_fault else '복구'}", sid)

    def context_for_llm(self, sid: str) -> dict:
        with self.lock:
            st = self.site_state(sid)
            st["sim_time"] = self.sim_time.strftime("%Y-%m-%d %H:%M")
            w = self.weather.at(self.sim_time)
            w3 = self.weather.at(self.sim_time - timedelta(hours=3))
            rain6 = sum(self.weather.forecast(self.sim_time, self.sim_time + timedelta(hours=k)).rain for k in range(1, 7))
            st["weather"] = {"출처": getattr(self.weather, "label", "합성 기상"),
                             "해면기압hPa": round(w.pressure, 1), "3시간기압변화hPa": round(w.pressure - w3.pressure, 1),
                             "현재강수mm/h": round(w.rain, 1), "향후6시간강수예보mm": round(rain6, 1), "기온": round(w.temp, 1)}
            st["recent_events"] = [e["msg"] for e in self.events if e["site"] in (sid, None)][-8:]
            # 진입 판단은 규칙 기반으로 확정해 전달 (생성형 AI가 임의로 바꾸지 않도록)
            c = st["current"]["radon"]
            f30 = (st.get("forecast_planned") or [c] * 3)[2]
            if st["sensor_fault"]:
                dec = "진입 금지 — 센서 이상(측정 불가), 환기팬 연속 가동 중"
            elif c <= 100 and f30 <= 100:
                dec = f"진입 가능 — 현재 {c:.0f}, 30분 후 예측 {f30:.0f} Bq/㎥로 안전 단계(100 이하)"
            else:
                dec = f"진입 보류 — 현재 {c:.0f} Bq/㎥({st['level']['label']} 단계). 출입 등록 시 초기환기 후 100 이하 확인 뒤 진입"
            st["entry_decision"] = dec
            st["entries"] = [e.to_dict() for e in self.entries_for(sid)]
            return st
