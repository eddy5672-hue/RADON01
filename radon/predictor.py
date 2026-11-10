"""AI 라돈 농도 예측 모델 (기상-실내 융합 다중회귀, 순수 Python).

- 입력: 최근 실내 측정값(라돈·온습도·환기팬 상태) + 기상(기압·강수) 실황/예보 + 환기 계획
- 출력: 10분 ~ 120분 후 라돈 농도 (10분 단위 12개 시점)
- 시점(horizon)별 · 현장별로 릿지 회귀 모델을 학습한다. (로그 농도 공간)
- 외부 라이브러리 없이 동작하도록 정규방정식을 직접 푼다.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Sequence

STEP_MIN = 10
HORIZONS = list(range(1, 13))  # ×10분 → 10~120분
MIN_HISTORY = 144              # 24시간 (10분 간격)
FEATURE_NAMES = [
    "log_c", "dlog_30m", "dlog_60m", "fan_plan", "fan_plan_x_logc", "fan_plan_last",
    "fan_now", "p_anom", "p_fcst_delta", "p_trend_3h", "rain_6h", "rain_fcst", "rain_24h", "rh",
]


def _lg(c: float) -> float:
    return math.log(max(c, 0.0) + 20.0)


def _mean(vals):
    vals = list(vals)
    return sum(vals) / len(vals) if vals else 0.0


def make_features(hist: Sequence[dict], i: int, k: int, fan_plan: Sequence[float],
                  fcst_pressure: Sequence[float], fcst_rain: Sequence[float]) -> list[float]:
    """hist[i]가 현재 시점. k: 예측 시점(×10분). fan_plan/fcst_*: 길이 ≥ k."""
    c_now = _mean(h["radon"] for h in hist[i - 2:i + 1])
    c_30 = _mean(h["radon"] for h in hist[i - 5:i - 2])
    c_60 = _mean(h["radon"] for h in hist[i - 8:i - 5])
    lc = _lg(c_now)
    f = _mean(fan_plan[:k])
    p = hist[i]["pressure"]
    return [
        lc,
        lc - _lg(c_30),
        lc - _lg(c_60),
        f,
        f * lc,
        fan_plan[k - 1],
        hist[i]["fan"],
        p - _mean(h["pressure"] for h in hist[i - 143:i + 1]),  # 24시간 평균 대비 기압 편차
        fcst_pressure[k - 1] - p,
        p - hist[i - 18]["pressure"],
        _mean(h["rain"] for h in hist[i - 35:i + 1]),
        _mean(fcst_rain[:k]),
        _mean(h["rain"] for h in hist[i - 143:i + 1]),
        hist[i]["rh"] / 100.0,
    ]


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """가우스 소거법 (부분 피벗)."""
    n = len(b)
    m = [row[:] + [b[r]] for r, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        d = m[col][col] or 1e-12
        for r in range(n):
            if r != col:
                fct = m[r][col] / d
                if fct:
                    rc = m[col]
                    row = m[r]
                    for c in range(col, n + 1):
                        row[c] -= fct * rc[c]
    return [m[r][n] / (m[r][r] or 1e-12) for r in range(n)]


def ridge_fit(X: list[list[float]], y: list[float], lam: float = 1.0):
    nf = len(X[0])
    mu = [_mean(r[j] for r in X) for j in range(nf)]
    sd = []
    for j in range(nf):
        v = _mean((r[j] - mu[j]) ** 2 for r in X)
        sd.append(math.sqrt(v) or 1.0)
    ymu = _mean(y)
    Z = [[(r[j] - mu[j]) / sd[j] for j in range(nf)] for r in X]
    A = [[0.0] * nf for _ in range(nf)]
    bv = [0.0] * nf
    for z, yy in zip(Z, y):
        yc = yy - ymu
        for p in range(nf):
            zp = z[p]
            bv[p] += zp * yc
            Ap = A[p]
            for q in range(p, nf):
                Ap[q] += zp * z[q]
    for p in range(nf):
        for q in range(p):
            A[p][q] = A[q][p]
        A[p][p] += lam
    w = _solve(A, bv)
    return {"mu": mu, "sd": sd, "ymu": ymu, "w": w}


def ridge_predict(m: dict, x: list[float]) -> float:
    return m["ymu"] + sum(wj * (xj - mj) / sj for wj, xj, mj, sj in zip(m["w"], x, m["mu"], m["sd"]))


def build_dataset(hist: list[dict], k: int, start: int, end: int):
    X, y = [], []
    for i in range(start, end):
        if i + k >= len(hist):
            break
        plan = [hist[i + j]["fan"] for j in range(1, 13) if i + j < len(hist)]
        plan += [plan[-1] if plan else 0.0] * (12 - len(plan))
        fp = [hist[min(i + j, len(hist) - 1)]["pressure"] for j in range(1, 13)]
        fr = [hist[min(i + j, len(hist) - 1)]["rain"] for j in range(1, 13)]
        X.append(make_features(hist, i, k, plan, fp, fr))
        y.append(_lg(_mean(h["radon"] for h in hist[i + k - 1:i + k + 2]) if i + k + 1 < len(hist) else hist[i + k]["radon"]))
    return X, y


class RadonPredictor:
    def __init__(self):
        self.models: dict[str, dict[int, dict]] = {}
        self.metrics: dict[str, dict] = {}
        self.trained_at: str | None = None
        self.source: str = ""

    # ------------------------------------------------------------------
    def train(self, data: dict[str, list[dict]], source: str = "", test_ratio: float = 0.2):
        """data: site_id -> 10분 간격 기록 리스트 (시간순)."""
        for site, hist in data.items():
            n = len(hist)
            if n < MIN_HISTORY + 50:
                continue
            split = int(n * (1 - test_ratio))
            self.models[site] = {}
            mets = {}
            for k in HORIZONS:
                X, y = build_dataset(hist, k, MIN_HISTORY, split - k)
                self.models[site][k] = ridge_fit(X, y, lam=2.0)
                if k in (6, 12):
                    Xt, yt = build_dataset(hist, k, split, n - k - 2)
                    errs, perr, base = [], [], []
                    for x, yy in zip(Xt, yt):
                        pred = math.exp(ridge_predict(self.models[site][k], x)) - 20
                        true = math.exp(yy) - 20
                        errs.append(abs(pred - true))
                        perr.append(abs(pred - true) / max(true, 30))
                        base.append(abs((math.exp(x[0]) - 20) - true))  # 현재값 유지(지속성) 기준
                    mae, bmae = _mean(errs), _mean(base)
                    mets[f"{k * 10}min"] = {
                        "MAE_Bq": round(mae, 1),
                        "MAPE_pct": round(100 * _mean(perr), 1),
                        "baseline_MAE_Bq": round(bmae, 1),
                        "improve_pct": round(100 * (1 - mae / bmae), 1) if bmae else 0.0,
                        "n_test": len(errs),
                    }
            mets["n_train"] = split - MIN_HISTORY
            self.metrics[site] = mets
        self.trained_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.source = source

    def has(self, site: str) -> bool:
        return site in self.models

    def forecast(self, site: str, hist: list[dict], fan_plan: Sequence[float],
                 fcst_pressure: Sequence[float], fcst_rain: Sequence[float]) -> list[float] | None:
        """10~120분 후 예측 농도 12개. 이력이 부족하면 None."""
        if site not in self.models or len(hist) < MIN_HISTORY:
            return None
        i = len(hist) - 1
        out = []
        for k in HORIZONS:
            x = make_features(hist, i, k, fan_plan, fcst_pressure, fcst_rain)
            out.append(round(max(0.0, math.exp(ridge_predict(self.models[site][k], x)) - 20), 1))
        return out

    # ------------------------------------------------------------------
    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "type": "radon-ridge-v1", "features": FEATURE_NAMES, "step_min": STEP_MIN,
                "trained_at": self.trained_at, "source": self.source, "metrics": self.metrics,
                "models": {s: {str(k): m for k, m in hm.items()} for s, hm in self.models.items()},
            }, f, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "RadonPredictor":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        p = cls()
        p.trained_at = d.get("trained_at")
        p.source = d.get("source", "")
        p.metrics = d.get("metrics", {})
        p.models = {s: {int(k): m for k, m in hm.items()} for s, hm in d["models"].items()}
        return p
