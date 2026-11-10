"""학습용 모의데이터 생성 및 CSV 입출력.

CSV 형식 (10분 간격, UTF-8):
    time,site,radon,temp,rh,fan,pressure,rain,out_temp
    2026-10-01 00:00,pump-a,412.3,16.8,77.2,0.0,1013.2,0.0,9.1
- radon: 실내 라돈 Bq/㎥, temp/rh: 실내 온습도, fan: 해당 10분 중 환기팬 가동비율(0~1)
- pressure: 현지기압 hPa, rain: 강수량 mm/h, out_temp: 외기온도 ℃
실측 데이터도 같은 형식으로 만들면 tools/train.py 로 재학습할 수 있다.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta

from .sim import RadonRoom, SiteConfig
from .weather import SimWeather

FIELDS = ["time", "site", "radon", "temp", "rh", "fan", "pressure", "rain", "out_temp"]
TFMT = "%Y-%m-%d %H:%M"


def generate(sites: list[SiteConfig], start: datetime, days: int, seed: int = 100) -> dict[str, list[dict]]:
    weather = SimWeather(start + timedelta(days=days / 2), days_back=days, days_fwd=days, seed=seed)
    rng = random.Random(seed)
    out: dict[str, list[dict]] = {}
    for cfg in sites:
        room = RadonRoom(cfg)
        recs = []
        fan = False
        seg_left = 0
        on_min = 0
        t = start
        for m in range(days * 1440):
            if seg_left <= 0:
                # 다양한 운전 패턴 학습을 위해 무작위 구간 (짧은 유지환기 ~ 장시간 가동)
                fan = rng.random() < 0.35
                seg_left = rng.choice([10, 20, 30, 40, 60, 90, 120, 180, 240]) if fan else rng.choice([10, 30, 60, 120, 240, 360, 600])
            if rng.random() < 1 / (1440 * 6):  # 집수정 유입 급증 (평균 6일 1회)
                room.trigger_surge(rng.uniform(0.5, 1.5))
            w = weather.at(t)
            room.step(w, fan)
            on_min += 1 if fan else 0
            seg_left -= 1
            t += timedelta(minutes=1)
            if (m + 1) % 10 == 0:
                r = room.read(t, fan)
                recs.append({
                    "t": t, "radon": r.radon, "temp": r.temp, "rh": r.rh, "fan": on_min / 10.0,
                    "pressure": round(w.pressure, 1), "rain": round(w.rain, 2), "out_temp": round(w.temp, 1),
                })
                on_min = 0
        out[cfg.id] = recs
    return out


def save_csv(data: dict[str, list[dict]], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(FIELDS)
        for site, recs in data.items():
            for r in recs:
                wr.writerow([r["t"].strftime(TFMT), site, r["radon"], r["temp"], r["rh"], r["fan"],
                             r["pressure"], r["rain"], r["out_temp"]])


def load_csv(path: str) -> dict[str, list[dict]]:
    """CSV → site별 시간순 기록. (원본 파일은 읽기 전용으로만 사용)"""
    data: dict[str, list[dict]] = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rec = {
                "t": datetime.strptime(row["time"].strip()[:16], TFMT),
                "radon": float(row["radon"]), "temp": float(row.get("temp") or 0),
                "rh": float(row.get("rh") or 0), "fan": float(row.get("fan") or 0),
                "pressure": float(row["pressure"]), "rain": float(row.get("rain") or 0),
                "out_temp": float(row.get("out_temp") or 0),
            }
            data.setdefault(row["site"].strip(), []).append(rec)
    for recs in data.values():
        recs.sort(key=lambda r: r["t"])
    return data
