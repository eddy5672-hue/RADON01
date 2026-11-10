"""기상 데이터 제공자.

- SimWeather : 기압·강수·기온을 합성 생성하는 시뮬레이션 기상 (기본값, 네트워크 불필요)
- 실측 기상(기상청 ASOS)은 tools/fetch_kma.py 로 CSV를 내려받아 재학습에 사용한다.

모든 제공자는 at(t) -> Weather, forecast(now, t) -> Weather 인터페이스를 가진다.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Weather:
    pressure: float  # 현지기압 hPa
    rain: float      # 강수량 mm/h
    temp: float      # 외기온도 ℃
    humidity: float  # 외기 상대습도 %


class SimWeather:
    """시간 단위 합성 기상 시계열 (재현 가능하도록 seed 고정)."""

    def __init__(self, center: datetime, days_back: int = 60, days_fwd: int = 60, seed: int = 7):
        self.base = center.replace(minute=0, second=0, microsecond=0) - timedelta(days=days_back)
        n = (days_back + days_fwd) * 24
        rng = random.Random(seed)
        self.P, self.R, self.T, self.H = [], [], [], []
        ar = 0.0
        rain_left = 0
        intensity = 0.0
        for i in range(n):
            td = i / 24.0
            ar = 0.97 * ar + rng.gauss(0, 0.45)
            p = 1014 + 5.0 * math.sin(2 * math.pi * td / 5.3) + 2.5 * math.sin(2 * math.pi * td / 2.1 + 1) + ar
            r = 0.0
            if rain_left <= 0:
                prob = 0.006 + (0.035 if p < 1009 else 0.0)
                if rng.random() < prob:
                    rain_left = rng.randint(2, 10)
                    intensity = rng.uniform(0.5, 7.0)
            if rain_left > 0:
                r = max(0.0, intensity * rng.uniform(0.3, 1.5))
                rain_left -= 1
            hour = (self.base + timedelta(hours=i)).hour
            t = 9 + 5 * math.sin(2 * math.pi * (hour - 9) / 24) + 3 * math.sin(2 * math.pi * td / 9) - (2 if r > 0 else 0) + rng.gauss(0, 0.4)
            h = min(99.0, 55 + 10 * math.sin(2 * math.pi * (hour + 3) / 24) + (30 if r > 0 else 0) + rng.gauss(0, 3))
            self.P.append(p)
            self.R.append(r)
            self.T.append(t)
            self.H.append(h)
        self.extra_rain: dict[int, float] = {}
        self.extra_dp: dict[int, float] = {}

    # ------------------------------------------------------------------
    def _hidx(self, t: datetime) -> float:
        h = (t - self.base).total_seconds() / 3600.0
        return min(max(h, 0.0), len(self.P) - 1.001)

    def _interp(self, arr, h: float) -> float:
        i = int(h)
        f = h - i
        return arr[i] * (1 - f) + arr[i + 1] * f

    def at(self, t: datetime) -> Weather:
        h = self._hidx(t)
        i = int(h)
        p = self._interp(self.P, h) + self.extra_dp.get(i, 0.0)
        r = self.R[i] + self.extra_rain.get(i, 0.0)
        return Weather(pressure=p, rain=r, temp=self._interp(self.T, h), humidity=self._interp(self.H, h))

    def forecast(self, now: datetime, t: datetime) -> Weather:
        """예보값: 실제값 + 선행시간에 비례하는 오차 (단기예보 수준)."""
        w = self.at(t)
        lead_h = max(0.0, (t - now).total_seconds() / 3600.0)
        rng = random.Random(int(self._hidx(now) * 6) * 100003 + int(self._hidx(t) * 6))
        return Weather(
            pressure=w.pressure + rng.gauss(0, 0.25 * lead_h),
            rain=max(0.0, w.rain * (1 + rng.gauss(0, 0.15 * lead_h))),
            temp=w.temp + rng.gauss(0, 0.3 * lead_h),
            humidity=w.humidity,
        )

    # ---- 시연용 이벤트 ------------------------------------------------
    def add_rain(self, start: datetime, hours: int = 4, mm: float = 12.0):
        i0 = int(self._hidx(start))
        for k in range(hours):
            self.extra_rain[i0 + k] = self.extra_rain.get(i0 + k, 0.0) + mm

    def add_low_pressure(self, start: datetime, hours: int = 12, drop: float = 12.0):
        i0 = int(self._hidx(start))
        for k in range(hours):
            # 사다리꼴 형태로 기압 강하
            w = min(1.0, (k + 1) / 3.0, (hours - k) / 3.0)
            self.extra_dp[i0 + k] = self.extra_dp.get(i0 + k, 0.0) - drop * w


# ---------------------------------------------------------------------------
# 실시간 기상 (기상청 API허브 + Open-Meteo) 및 시나리오 오버레이
# ---------------------------------------------------------------------------
import copy  # noqa: E402
import threading  # noqa: E402
import time as _time  # noqa: E402

from . import kma  # noqa: E402


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


class LiveWeather:
    """기상청 관측(과거 48h) + 초단기·단기예보(~3일) + 기압예보(Open-Meteo, 관측값으로 편차 보정)를
    1시간 간격 시계열로 합성한다. 10분마다 백그라운드 갱신, 실패 시 직전 자료/대체 공급원 사용."""

    def __init__(self, now_fn, kma_key: str | None = None, refresh_sec: int = 600, fetch: bool = True,
                 portal_key: str | None = None):
        self.now_fn = now_fn
        self.key = kma_key or None            # 기상청 API허브 authKey
        self.portal_key = portal_key or None  # 공공데이터포털 serviceKey
        self.refresh_sec = refresh_sec
        self.series: dict[datetime, dict] = {}
        self._fallback = SimWeather(now_fn(), seed=7)
        self.status = {"source": "sim", "label": "합성 기상(대체)", "obs_time": None, "updated": None,
                       "errors": [], "stn": kma.DEFAULT_STN, "grid": [kma.DEFAULT_NX, kma.DEFAULT_NY],
                       "kma_key": bool(self.key), "portal_key": bool(self.portal_key), "parts": {}}
        self.last_raw: dict[str, str] = {}
        if fetch:
            self.refresh()

    # ---- 자료 수집 ----------------------------------------------------
    def refresh(self):
        now = self.now_fn()
        errors, parts = [], {}
        obs, ultra, vil, om, ncst = [], {}, {}, {}, None
        if self.portal_key:  # 공공데이터포털
            k = self.portal_key
            try:
                obs = kma.fetch_asos_datagokr(k, now)
                parts["asos"] = f"{len(obs)}시간 · 최근 {obs[-1]['t']:%m-%d %H:%M} (전날까지 제공)"
            except (kma.WeatherAPIError, ValueError, KeyError) as e:
                errors.append(f"ASOS 관측: {e}")
            try:
                ncst = kma.fetch_ncst_datagokr(k, now)
                parts["ncst"] = f"{ncst[0]:%H:%M} 실황"
            except (kma.WeatherAPIError, ValueError, KeyError) as e:
                errors.append(f"초단기실황: {e}")
            for kind, name in (("ultra", "초단기예보"), ("vilage", "단기예보")):
                try:
                    fc, base = kma.fetch_forecast_datagokr(k, now, kind)
                    if kind == "ultra":
                        ultra = fc
                    else:
                        vil = fc
                    parts[kind] = f"{len(fc)}시간 · 발표 {base:%m-%d %H:%M}"
                except (kma.WeatherAPIError, ValueError, KeyError) as e:
                    errors.append(f"{name}: {e}")
        elif self.key:
            try:
                obs, raw = kma.fetch_asos(self.key, now - timedelta(hours=50), now)
                self.last_raw["asos"] = "\n".join(raw.splitlines()[:6] + ["..."] + raw.splitlines()[-3:])
                parts["asos"] = f"{len(obs)}시간 · 최근 {obs[-1]['t']:%m-%d %H:%M}"
            except kma.WeatherAPIError as e:
                errors.append(f"ASOS 관측: {e}")
            try:
                ultra, base = kma.fetch_forecast(self.key, now, "ultra")
                parts["ultra"] = f"{len(ultra)}시간 · 발표 {base:%H:%M}"
            except (kma.WeatherAPIError, ValueError) as e:
                errors.append(f"초단기예보: {e}")
            try:
                vil, base = kma.fetch_forecast(self.key, now, "vilage")
                parts["vilage"] = f"{len(vil)}시간 · 발표 {base:%m-%d %H:%M}"
            except (kma.WeatherAPIError, ValueError) as e:
                errors.append(f"단기예보: {e}")
        # 기압(당일·예보) 보조 공급원: MET Norway → 실패 시 Open-Meteo
        try:
            om = kma.fetch_metno()
            parts["metno"] = f"{len(om)}시간 (기압 예보)"
        except kma.WeatherAPIError as e:
            try:
                om = kma.fetch_open_meteo()
                parts["open_meteo"] = f"{len(om)}시간 (기압 예보, MET Norway 대체)"
            except kma.WeatherAPIError as e2:
                errors.append(f"MET Norway: {e}")
                errors.append(f"Open-Meteo: {e2}")

        series = self._merge(now, obs, ultra, vil, om, ncst)
        if series:
            self._data = (series, (min(series), max(series)))  # 원자적 교체
            self.series = series
        src = "kma" if (obs or ncst or ultra) else ("open-meteo" if om else ("cache" if self.series else "sim"))
        label = {"kma": "기상청 관측·예보" + (" (공공데이터포털)" if self.portal_key else " (API허브)"),
                 "open-meteo": "Open-Meteo (기상청 키 미설정/장애 대체)",
                 "cache": "직전 수집 자료", "sim": "합성 기상 (외부 연결 불가)"}[src]
        last_obs = obs[-1]["t"] if obs else None
        self.status.update(source=src, label=label, errors=errors, parts=parts,
                           updated=now.strftime("%Y-%m-%d %H:%M"),
                           obs_time=last_obs.strftime("%Y-%m-%d %H:%M") if last_obs else self.status.get("obs_time"))
        return self.status

    def _merge(self, now, obs, ultra, vil, om, ncst=None) -> dict[datetime, dict]:
        """관측(기상청) > 실황 > 예보(기상청) > 수치예보(기압 등) 우선순위로 1시간 시계열 합성."""
        start, end = _hour(now) - timedelta(hours=50), _hour(now) + timedelta(hours=72)
        obs_map = {_hour(r["t"]): r for r in obs}
        # 수치예보 기압 편차 보정 (겹치는 관측 해면기압 기준, 없으면 0)
        diffs = [o["pressure"] - om[h]["pressure"] for h, o in obs_map.items()
                 if o["pressure"] is not None and h in om and kma.finite(om[h].get("pressure"))]
        bias = sum(diffs[-6:]) / len(diffs[-6:]) if diffs else 0.0
        hours, recs = [], []
        h = start
        while h <= end:
            rec, src = {}, None
            o = obs_map.get(h)
            if o:
                rec = {k: o[k] for k in ("pressure", "temp", "humidity", "rain") if o[k] is not None}
                src = "obs"
            if ncst and h == _hour(ncst[0]):  # 초단기실황(현재 시각 관측)
                for k2, v2 in ncst[1].items():
                    rec.setdefault(k2, v2)
                src = src or "nowcast"
            f = ultra.get(h) or vil.get(h) or {}
            m = om.get(h) or {}
            for k in ("temp", "humidity", "rain"):
                if k not in rec:
                    if k in f:
                        rec[k], src = f[k], src or "kma-fcst"
                    elif kma.finite(m.get(k)) and h > _hour(now) - timedelta(hours=1):
                        rec[k], src = m[k], src or "nwp"
            if "pressure" not in rec and kma.finite(m.get("pressure")):
                rec["pressure"] = m["pressure"] + bias
            rec["src"] = src or "fill"
            hours.append(h)
            recs.append(rec)
            h += timedelta(hours=1)
        # 결측 보간: 기압·기온·습도는 앞뒤 값 사이 선형 보간(양 끝은 유지), 강수는 0
        for k in ("pressure", "temp", "humidity"):
            idx = [i for i, r in enumerate(recs) if kma.finite(r.get(k))]
            if not idx:
                continue
            for i, r in enumerate(recs):
                if kma.finite(r.get(k)):
                    continue
                prev = max((j for j in idx if j < i), default=None)
                nxt = min((j for j in idx if j > i), default=None)
                if prev is None:
                    r[k] = recs[nxt][k]
                elif nxt is None:
                    r[k] = recs[prev][k]
                else:
                    w = (i - prev) / (nxt - prev)
                    r[k] = recs[prev][k] * (1 - w) + recs[nxt][k] * w
        out = {}
        for h, r in zip(hours, recs):
            r.setdefault("rain", 0.0)
            if all(kma.finite(r.get(k)) for k in ("pressure", "temp", "humidity")):
                out[h] = r
        return out

    def run(self):
        def loop():
            while True:
                _time.sleep(self.refresh_sec)
                try:
                    self.refresh()
                except Exception as e:  # noqa: BLE001  (갱신 실패해도 서비스 유지)
                    self.status["errors"] = [kma._scrub(str(e))]
        threading.Thread(target=loop, daemon=True).start()

    # ---- 조회 ---------------------------------------------------------
    def at(self, t: datetime) -> Weather:
        data = getattr(self, "_data", None)
        if not data:
            return self._fallback.at(t)
        s, (lo, hi) = data
        ht = _hour(t)
        hr = ht if t == ht else ht + timedelta(hours=1)  # 시간강수 = 해당 정시까지 1시간 누적
        rain = s[hr]["rain"] if hr in s else 0.0
        h0 = min(max(ht, lo), hi)
        h1 = min(h0 + timedelta(hours=1), hi)
        a, b = s[h0], s[h1]
        f = min(1.0, max(0.0, (t - h0).total_seconds() / 3600.0))
        return Weather(pressure=a["pressure"] * (1 - f) + b["pressure"] * f, rain=rain,
                       temp=a["temp"] * (1 - f) + b["temp"] * f, humidity=a["humidity"] * (1 - f) + b["humidity"] * f)

    def forecast(self, now: datetime, t: datetime) -> Weather:
        return self.at(t)

    def summary(self, now: datetime) -> dict:
        w = self.at(now)
        w3 = self.at(now - timedelta(hours=3))
        rain6 = sum(self.at(now + timedelta(hours=k)).rain for k in range(1, 7))
        nxt = []
        for k in range(0, 13, 2):
            ww = self.at(now + timedelta(hours=k))
            nxt.append({"t": (now + timedelta(hours=k)).strftime("%H시"), "p": round(ww.pressure, 1),
                        "rain": round(ww.rain, 1), "temp": round(ww.temp, 1)})
        return {**self.status, "now": {"pressure": round(w.pressure, 1), "temp": round(w.temp, 1),
                                       "humidity": round(w.humidity), "rain": round(w.rain, 1)},
                "p_trend_3h": round(w.pressure - w3.pressure, 1), "rain_6h": round(rain6, 1), "next": nxt}


class OverlayWeather:
    """세션별 시나리오(집중호우·저기압)를 공유 기상자료 위에 덧입힌다."""

    def __init__(self, base):
        self.base = base
        self.extra_rain: dict[datetime, float] = {}
        self.extra_dp: dict[datetime, float] = {}

    def __deepcopy__(self, memo):
        o = OverlayWeather(self.base)  # 기반 자료는 공유
        o.extra_rain = dict(self.extra_rain)
        o.extra_dp = dict(self.extra_dp)
        return o

    def _apply(self, w: Weather, t: datetime) -> Weather:
        h = _hour(t)
        if h in self.extra_rain or h in self.extra_dp:
            return Weather(pressure=w.pressure + self.extra_dp.get(h, 0.0), rain=w.rain + self.extra_rain.get(h, 0.0),
                           temp=w.temp, humidity=w.humidity)
        return w

    def at(self, t):
        return self._apply(self.base.at(t), t)

    def forecast(self, now, t):
        return self._apply(self.base.forecast(now, t), t)

    def add_rain(self, start: datetime, hours: int = 4, mm: float = 12.0):
        h0 = _hour(start)
        for k in range(hours):
            h = h0 + timedelta(hours=k)
            self.extra_rain[h] = self.extra_rain.get(h, 0.0) + mm

    def add_low_pressure(self, start: datetime, hours: int = 12, drop: float = 12.0):
        h0 = _hour(start)
        for k in range(hours + 1):
            w = min(1.0, (k + 1) / 3.0, (hours - k + 1) / 3.0)
            h = h0 + timedelta(hours=k)
            self.extra_dp[h] = self.extra_dp.get(h, 0.0) - drop * w

    @property
    def is_live(self) -> bool:
        return isinstance(self.base, LiveWeather)

    @property
    def label(self) -> str:
        if isinstance(self.base, LiveWeather):
            return self.base.status["label"]
        return "시나리오 합성 기상"
