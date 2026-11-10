"""환기팬 제어 로직 (특허 '환기 제어 시스템 및 그 방법' 개념 구현).

운전 모드
- AUTO   : AI 예측 기반 자동 제어
    1) 초기환기   : 출입 예정 시각 전에 미리 가동. 필요시간 = ln(C_예측/C_목표) / (Q/V)
    2) 유지환기   : 출입 중 ON(OFF+10분)/OFF 주기 반복, 목표 초과 시 즉시 ON
    3) AI 선제환기: 무인 시간에 1시간 내 예측농도가 상한(기본 600, 위험)을 넘으면 가동
- LEGACY : 기존 방식(타이머 상시 가동, 1일 20시간) — 비교용
- MANUAL : 관제/단말기에서 수동 ON/OFF
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from .sim import SiteConfig


@dataclass
class ControlParams:
    target_entry: float = 100.0      # 출입 시 목표 농도 (안전 단계)
    pre_target: float = 70.0         # 초기환기 종료 목표 (여유 포함)
    ceiling_idle: float = 600.0      # 무인 시 상한 (위험 단계 진입 방지)
    ai_margin: float = 0.9           # 예측 오차를 고려한 선제 가동 여유율 (상한의 90%)
    maint_on: int = 20               # 유지환기 ON (분)
    maint_off: int = 10              # 유지환기 OFF (분)  ※ ON = OFF + 10분
    margin_min: int = 10             # 초기환기 여유시간 (분)
    legacy_start: str = "06:00"      # 기존 타이머 가동 시작
    legacy_hours: float = 20.0


@dataclass
class Entry:
    """작업자 출입 예정 (매일 반복 또는 1회)."""
    site: str
    start: str            # "HH:MM" (daily) 또는 "YYYY-MM-DD HH:MM" (once)
    end: str
    label: str = "점검 작업"
    daily: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def occurrences(self, t0: datetime, t1: datetime):
        """[t0, t1] 구간과 겹치는 (시작, 종료) 목록."""
        res = []
        if self.daily:
            sh, sm = map(int, self.start.split(":"))
            eh, em = map(int, self.end.split(":"))
            d = (t0 - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            while d <= t1:
                s = d.replace(hour=sh, minute=sm)
                e = d.replace(hour=eh, minute=em)
                if e <= s:
                    e += timedelta(days=1)
                if e >= t0 and s <= t1:
                    res.append((s, e))
                d += timedelta(days=1)
        else:
            s = datetime.strptime(self.start, "%Y-%m-%d %H:%M")
            e = datetime.strptime(self.end, "%Y-%m-%d %H:%M")
            if e >= t0 and s <= t1:
                res.append((s, e))
        return res

    def to_dict(self):
        return asdict(self)


class FanController:
    def __init__(self, cfg: SiteConfig, params: ControlParams | None = None, mode: str = "AUTO"):
        self.cfg = cfg
        self.p = params or ControlParams()
        self.mode = mode
        self.fan = False
        self.manual_fan = False
        self.reason = "대기"
        self.phase = "idle"
        self._ai_latch = False
        self._pre_latch: tuple | None = None    # 진행 중인 초기환기 대상 (start, end)
        self._ready_notified: set = set()
        self._warn_notified: set = set()

    # ------------------------------------------------------------------
    def pre_minutes(self, c_pred: float) -> int:
        """초기환기 필요시간(분): 지수 감쇠 모델 C(t)=C0·e^(-λt)."""
        lam = self.cfg.lambda_fan + self.cfg.leak
        if c_pred <= self.p.pre_target:
            return self.p.margin_min
        t_h = math.log(c_pred / self.p.pre_target) / lam
        return int(math.ceil(t_h * 60)) + self.p.margin_min

    def _legacy_on(self, now: datetime) -> bool:
        h, m = map(int, self.p.legacy_start.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start > now:
            start -= timedelta(days=1)
        return (now - start) < timedelta(hours=self.p.legacy_hours)

    def next_occurrences(self, entries: list[Entry], now: datetime, hours: float = 12):
        occ = []
        for e in entries:
            for s, en in e.occurrences(now, now + timedelta(hours=hours)):
                occ.append((s, en, e))
        occ.sort(key=lambda x: x[0])
        return occ

    def c_at(self, when: datetime, now: datetime, c_now: float, natural_fc: list | None) -> float:
        """'환기하지 않을 때' 해당 시각의 예상 농도 (AI 예측 2시간, 그 이후는 보수적 근사)."""
        mins = (when - now).total_seconds() / 60
        if natural_fc:
            if mins <= 10:
                return natural_fc[0]
            k = min(12, int(round(mins / 10)))
            base = natural_fc[k - 1]
            if mins > 120:  # 2시간 이후: 자연 평형 농도 방향으로 근사
                eq = self.cfg.source0 / self.cfg.leak
                extra = (mins - 120) / 60
                base = eq + (base - eq) * math.exp(-self.cfg.leak * extra)
            return base
        return c_now

    # ------------------------------------------------------------------
    def decide(self, now: datetime, c_now: float, entries: list[Entry], natural_fc: list | None,
               sensor_fault: bool = False):
        """매 분 호출. (fan_on, events) 반환."""
        events = []
        occ = self.next_occurrences(entries, now - timedelta(hours=12), hours=24)
        active = [(s, e, en) for s, e, en in occ if s <= now < e]
        upcoming = [(s, e, en) for s, e, en in occ if s > now]

        if self.mode == "MANUAL":
            fan, reason, phase = self.manual_fan, "수동 제어", "manual"
        elif self.mode == "LEGACY":
            fan = self._legacy_on(now)
            reason, phase = ("기존 타이머 가동" if fan else "기존 타이머 정지"), "legacy"
        elif sensor_fault:
            fan, reason, phase = True, "센서 이상 — 안전측(Fail-safe) 연속 가동", "failsafe"
        else:
            fan, reason, phase = False, "대기 (환기 불필요)", "idle"
            if active:
                s, e, en = active[0]
                pos = int((now - s).total_seconds() // 60) % (self.p.maint_on + self.p.maint_off)
                fan = pos < self.p.maint_on
                reason = f"유지환기 {'ON' if fan else 'OFF'} ({en.label} {s:%H:%M}~{e:%H:%M})"
                phase = "maint"
                if c_now > self.p.target_entry:
                    fan, reason = True, f"유지환기 보정: 농도 {c_now:.0f} > 목표 {self.p.target_entry:.0f}"
                key = (en.id, s)
                if key not in self._warn_notified and c_now > self.p.target_entry and (now - s) < timedelta(minutes=2):
                    self._warn_notified.add(key)
                    events.append(("warn", f"[{self.cfg.name}] 출입 시작 시점 농도 {c_now:.0f} Bq/㎥ — 목표 초과, 진입 전 확인 필요"))
                self._pre_latch = None
            else:
                for s, e, en in upcoming:
                    if (s - now) > timedelta(hours=8):
                        break
                    c_pred = self.c_at(s, now, c_now, natural_fc)
                    need = self.pre_minutes(c_pred)
                    latched = self._pre_latch == (en.id, s)
                    if latched or now >= s - timedelta(minutes=need):
                        if not latched:
                            self._pre_latch = (en.id, s)
                            events.append(("info", f"[{self.cfg.name}] 초기환기 시작 — {en.label} {s:%H:%M} 진입 대비, "
                                                   f"예측 {c_pred:.0f} Bq/㎥ → 소요 {need}분"))
                            left = int((s - now).total_seconds() // 60)
                            if need - self.p.margin_min > left:
                                delay = need - self.p.margin_min - left
                                events.append(("warn", f"[{self.cfg.name}] 진입까지 {left}분 — 환기 완료까지 약 {delay}분 "
                                                       f"추가 소요 예상, '진입 가능' 알림 후 출입 권고"))
                        fan = True
                        reason = f"초기환기: {s:%H:%M} 진입 대비 (예측 {c_pred:.0f}→목표 {self.p.pre_target:.0f})"
                        phase = "pre"
                        key = (en.id, s)
                        if c_now <= self.p.target_entry and key not in self._ready_notified:
                            self._ready_notified.add(key)
                            events.append(("ok", f"[{self.cfg.name}] 환기 완료 — 현재 {c_now:.0f} Bq/㎥, "
                                                 f"{s:%H:%M} 진입 가능"))
                        break
                if phase == "idle":
                    peak = max(natural_fc[:6]) if natural_fc else c_now
                    if self._ai_latch:
                        if c_now < self.p.ceiling_idle * 0.5:
                            self._ai_latch = False
                    elif peak > self.p.ceiling_idle * self.p.ai_margin or c_now > self.p.ceiling_idle * self.p.ai_margin:
                        self._ai_latch = True
                        events.append(("warn", f"[{self.cfg.name}] AI 선제환기 — 현재 {c_now:.0f}, 1시간 내 예측 최고 {max(peak, c_now):.0f} Bq/㎥ "
                                               f"(상한 {self.p.ceiling_idle:.0f}의 {self.p.ai_margin:.0%} 초과 예상)"))
                    if self._ai_latch:
                        fan, phase = True, "ai"
                        reason = f"AI 선제환기: 위험 단계(>{self.p.ceiling_idle:.0f}) 도달 전 농도 저감 중"

        if fan != self.fan:
            events.append(("fan", f"[{self.cfg.name}] 환기팬 {'ON' if fan else 'OFF'} — {reason}"))
        self.fan, self.reason, self.phase = fan, reason, phase
        return fan, events

    def plan(self, now: datetime, entries: list[Entry], c_now: float, natural_fc: list | None) -> list[float]:
        """향후 2시간(10분×12) 환기 계획 가동비율 — '제어 반영 예측'에 사용."""
        out = []
        occ = self.next_occurrences(entries, now - timedelta(hours=12), hours=24)
        for k in range(12):
            t = now + timedelta(minutes=10 * k + 5)
            if self.mode == "MANUAL":
                out.append(1.0 if self.manual_fan else 0.0)
                continue
            if self.mode == "LEGACY":
                out.append(1.0 if self._legacy_on(t) else 0.0)
                continue
            v = 0.0
            for s, e, en in occ:
                if s <= t < e:
                    v = self.p.maint_on / (self.p.maint_on + self.p.maint_off)
                    break
                if t < s:
                    need = self.pre_minutes(self.c_at(s, now, c_now, natural_fc))
                    if t >= s - timedelta(minutes=need):
                        v = 1.0
                    break
            if v == 0.0 and self._ai_latch and k < 3:
                v = 1.0
            out.append(v)
        return out
