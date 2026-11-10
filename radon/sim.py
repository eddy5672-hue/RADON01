"""가상 라돈 측정 단말기 + 밀폐공간 라돈 물리 모델.

질량수지식(1-구획 모델):
    dC/dt = S(t) - (λ_leak + λ_fan·fan + λ_Rn)·C
    S(t)  = S0 · f(기압 변화(48h 기준 대비 하강), 지하수 지표, 잡음, 이벤트)
환기팬 ON 시 환기율 λ_fan = Q/V (풍량/체적).
한 스텝(Δt) 동안 S, λ가 일정하다고 보고 해석해를 적용한다.

계수는 배수펌프실 실측(개선 전 평균 432.4 Bq/㎥, 개선 후 84.7 Bq/㎥) 수준이
재현되도록 설정했다. 실제 현장 적용 시 실측 CSV로 보정한다.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime

from .weather import Weather

LAMBDA_RN = 0.00755  # 라돈-222 붕괴상수 (1/h)


@dataclass
class SiteConfig:
    id: str
    name: str
    desc: str
    volume: float      # m³
    fan_flow: float    # m³/h
    fan_kw: float      # 환기팬 소비전력 kW
    leak: float        # 자연환기율 1/h
    source0: float     # 기준 라돈 유입률 Bq/㎥/h
    kp: float = 0.35   # 기압 민감도 (기준 대비 10hPa 강하당 증가율)
    kr: float = 0.18   # 지하수(강우) 민감도
    legacy_hours: float = 20.0  # 기존 방식 1일 가동시간
    seed: int = 1

    @property
    def lambda_fan(self) -> float:
        return self.fan_flow / self.volume

    def to_dict(self):
        d = asdict(self)
        d["lambda_fan"] = round(self.lambda_fan, 3)
        d["natural_eq"] = round(self.source0 / (self.leak + LAMBDA_RN), 1)
        return d


TRAINING_SITES = [  # 예측모델 학습용 모의 현장 (일반화를 위해 3개소 특성으로 학습)
    SiteConfig("pump-a", "A역 배수펌프실", "지하 4층 · 집수정 인접 · 밀폐공간",
               volume=80, fan_flow=200, fan_kw=2.2, leak=0.08, source0=35.0, seed=11),
    SiteConfig("pump-b", "B역 배수펌프실", "지하 3층 · 집수정 · 밀폐공간",
               volume=120, fan_flow=250, fan_kw=2.2, leak=0.10, source0=31.0, seed=22),
    SiteConfig("mech-c", "C역 기계실", "지하 2층 · 배수설비 · 반밀폐",
               volume=200, fan_flow=500, fan_kw=3.7, leak=0.12, source0=27.0, seed=33),
]
DEFAULT_SITES = [TRAINING_SITES[0]]  # 시연 대상: A역 배수펌프실


@dataclass
class Reading:
    t: datetime
    radon: float   # Bq/㎥ (측정값)
    temp: float    # ℃
    rh: float      # %
    fan: bool


class RadonRoom:
    """밀폐공간 1개소 + 그곳에 설치된 가상 측정 단말기."""

    def __init__(self, cfg: SiteConfig, c0: float | None = None):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.true_c = c0 if c0 is not None else cfg.source0 / (cfg.leak + LAMBDA_RN)
        self.gw = 0.0         # 지하수 지표 (강우의 12시간 지수평활, mm/h)
        self.noise = 0.0      # 유입률 자기상관 잡음
        self.surge = 0.0      # 지하수 유입 급증 이벤트 (배율 가산)
        self.p_base = None    # 기준 기압 (48시간 지수평활) — 기압 '하강'이 라돈 방출을 늘림
        self.temp = 17.0
        self.rh = 75.0
        self.sensor_offset = 0.0
        self.sensor_fault = False

    def step(self, w: Weather, fan_on: bool, dt_min: float = 1.0):
        c = self.cfg
        dt_h = dt_min / 60.0
        self.gw += (w.rain - self.gw) * min(1.0, dt_min / 720.0)
        self.surge *= math.exp(-dt_min / 300.0)
        self.noise = 0.995 * self.noise + self.rng.gauss(0, 0.008)
        if self.p_base is None:
            self.p_base = w.pressure
        self.p_base += (w.pressure - self.p_base) * min(1.0, dt_min / 2880.0)
        f = 1.0 + c.kp * (self.p_base - w.pressure) / 10.0 + c.kr * min(self.gw, 4.0) + self.noise + self.surge
        f = max(0.3, f)
        s = c.source0 * f
        k = c.leak + LAMBDA_RN + (c.lambda_fan if fan_on else 0.0)
        ceq = s / k
        self.true_c = ceq + (self.true_c - ceq) * math.exp(-k * dt_h)
        # 실내 온습도 (외기 영향 + 환기 시 외기 유입)
        t_target = 16.5 + 0.2 * (w.temp - 10) + (0.15 * (w.temp - 16.5) if fan_on else 0)
        self.temp += (t_target - self.temp) * min(1.0, dt_min / 90.0)
        rh_target = 76 + 4 * min(self.gw, 3.0) + (0.25 * (w.humidity - 76) if fan_on else 0)
        self.rh += (rh_target - self.rh) * min(1.0, dt_min / 120.0)

    def read(self, t: datetime, fan_on: bool) -> Reading:
        """측정 단말기 출력 (센서 잡음 포함)."""
        if self.sensor_fault:
            radon = 0.0
        else:
            radon = self.true_c * (1 + self.rng.gauss(0, 0.04)) + self.rng.gauss(0, 4) + self.sensor_offset
        return Reading(
            t=t,
            radon=round(max(0.0, radon), 1),
            temp=round(self.temp + self.rng.gauss(0, 0.1), 1),
            rh=round(min(99.0, self.rh + self.rng.gauss(0, 0.5)), 1),
            fan=fan_on,
        )

    # 시연용 이벤트
    def trigger_surge(self, factor: float = 1.5):
        self.surge += factor


def level_of(c: float) -> dict:
    """한국산업안전보건공단 권고 단계."""
    if c <= 100:
        return {"code": "safe", "label": "안전"}
    if c <= 300:
        return {"code": "interest", "label": "관심"}
    if c <= 600:
        return {"code": "caution", "label": "주의"}
    return {"code": "danger", "label": "위험"}
