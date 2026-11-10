"""실시간 기상 연동 클라이언트.

1) 기상청 API허브 (https://apihub.kma.go.kr, 환경변수 KMA_AUTH_KEY)
   - 지상관측(ASOS) 시간자료  : /api/typ01/url/kma_sfctm3.php  (기간 조회, 텍스트 표)
       해면기압(PS)·기온(TA)·습도(HM)·1시간 강수량(RN) 사용
   - 단기예보 조회서비스      : /api/typ02/openApi/VilageFcstInfoService_2.0/
       getUltraSrtFcst(초단기예보, 6시간) · getVilageFcst(단기예보, ~3일) — 강수량·기온·습도
2) Open-Meteo (https://open-meteo.com, 키 불필요)
   - 기압 예보(기상청 예보 API에는 기압 항목이 없음) 및 기상청 키가 없거나 장애 시 대체 공급원

모든 함수는 표준 라이브러리만 사용하며, 인증키는 오류 메시지에서 제거한다.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

APIHUB = os.environ.get("KMA_APIHUB_URL", "https://apihub.kma.go.kr")  # 테스트용 대체 주소 지정 가능
DATAGOKR = os.environ.get("DATAGOKR_URL", "https://apis.data.go.kr/1360000")
OPEN_METEO = os.environ.get("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
METNO = os.environ.get("METNO_URL", "https://api.met.no/weatherapi/locationforecast/2.0/compact")
UA = os.environ.get("HTTP_USER_AGENT", "radon-ai/1.0 https://radon-ai.onrender.com")  # MET Norway 이용약관: 식별 가능한 UA 필요

# 서울(종로구 송월동) 관측소·격자 — 환경변수로 변경 가능
DEFAULT_STN = os.environ.get("KMA_STN", "108")                 # 서울 ASOS
DEFAULT_NX = int(os.environ.get("KMA_NX", "60"))               # 단기예보 격자
DEFAULT_NY = int(os.environ.get("KMA_NY", "127"))
DEFAULT_LAT = float(os.environ.get("WX_LAT", "37.5714"))
DEFAULT_LON = float(os.environ.get("WX_LON", "126.9658"))

# kma_sfctm 텍스트 표의 기본 열 위치 (헤더를 못 읽을 때 사용)
SFCTM_DEFAULT_COLS = {"TM": 0, "STN": 1, "PA": 7, "PS": 8, "TA": 11, "HM": 13, "RN": 15}


class WeatherAPIError(Exception):
    pass


def _scrub(msg: str) -> str:
    return re.sub(r"((?:authKey|serviceKey)=)[^&\s]+", r"\1****", str(msg))


def http_get(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = _decode(e.read())
            m = re.search(r"returnAuthMsg\W+([^<\"]+)", body) or re.search(r"resultMsg\W+([^<\"]+)", body)
            detail = " — " + (m.group(1).strip() if m else body.strip()[:100])
        except Exception:  # noqa: BLE001
            pass
        raise WeatherAPIError(f"HTTP {e.code}{_scrub(detail)}") from None
    except (urllib.error.URLError, OSError) as e:
        raise WeatherAPIError(_scrub(f"연결 실패: {e}")) from None


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# ASOS 시간자료 (텍스트 표)
# ---------------------------------------------------------------------------
def parse_sfctm(text: str) -> list[dict]:
    """kma_sfctm2/3.php 응답 → [{t, pressure, temp, humidity, rain}] (결측은 None)."""
    cols = None
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            toks = s.lstrip("#").split()
            if toks and toks[0] == "YYMMDDHHMI" and cols is None:
                cols = {}
                for i, name in enumerate(toks):
                    cols.setdefault(name, i)  # 같은 이름이 반복되면 첫 번째(예: RN=1시간 강수)
            continue
        toks = s.split()
        if not toks or not re.fullmatch(r"\d{12}", toks[0]):
            continue
        c = cols if cols and all(k in cols for k in ("PS", "TA", "HM", "RN")) else SFCTM_DEFAULT_COLS

        def val(name, lo, hi):
            try:
                v = float(toks[c[name]])
            except (KeyError, IndexError, ValueError):
                return None
            return v if lo <= v <= hi else None  # -9, -99 등 결측 코드 제거

        ps = val("PS", 900, 1100)
        if ps is None:
            ps = val("PA", 850, 1100)
        rn = val("RN", 0, 300)
        rows.append({
            "t": datetime.strptime(toks[0], "%Y%m%d%H%M"),
            "pressure": ps,
            "temp": val("TA", -50, 50),
            "humidity": val("HM", 0, 100),
            "rain": rn if rn is not None else 0.0,  # 무강수 시 결측(-9)으로 표기됨
        })
    rows.sort(key=lambda r: r["t"])
    return rows


def fetch_asos(key: str, start: datetime, end: datetime, stn: str = DEFAULT_STN) -> tuple[list[dict], str]:
    q = urllib.parse.urlencode({"tm1": start.strftime("%Y%m%d%H00"), "tm2": end.strftime("%Y%m%d%H00"),
                                "stn": stn, "help": 0, "authKey": key})
    text = _decode(http_get(f"{APIHUB}/api/typ01/url/kma_sfctm3.php?{q}"))
    rows = parse_sfctm(text)
    if not rows:
        head = text.strip()[:200]
        raise WeatherAPIError("ASOS 응답에 자료가 없습니다: " + _scrub(head))
    return rows, text


# ---------------------------------------------------------------------------
# 단기예보 조회서비스 (JSON)
# ---------------------------------------------------------------------------
def parse_rain_text(v) -> float:
    """'강수없음', '1mm 미만', '1.0mm', '30.0~50.0mm', '50.0mm 이상' → mm."""
    s = str(v).strip()
    if not s or "없음" in s or s in ("0", "-"):
        return 0.0
    if "미만" in s:
        return 0.5
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if not nums:
        return 0.0
    return sum(nums) / len(nums) if "~" in s else nums[0]


def _check_portal_error(text: str):
    """공공데이터포털 게이트웨이 오류(XML/JSON) → 예외."""
    t = text.strip()
    if "OpenAPI_ServiceResponse" in t or t.startswith("<"):
        m = re.search(r"returnAuthMsg\W+([^<\"]+)", t) or re.search(r"errMsg\W+([^<\"]+)", t)
        raise WeatherAPIError("공공데이터포털: " + (m.group(1).strip() if m else t[:120]))


def parse_vilage_json(text: str) -> dict[datetime, dict]:
    _check_portal_error(text)
    d = json.loads(text)
    head = d.get("response", {}).get("header", {})
    if head.get("resultCode") not in ("00", 0, None):
        raise WeatherAPIError(f"예보 API 오류 {head.get('resultCode')}: {head.get('resultMsg')}")
    items = d.get("response", {}).get("body", {}).get("items", {}).get("item", [])
    out: dict[datetime, dict] = {}
    for it in items:
        t = datetime.strptime(it["fcstDate"] + it["fcstTime"], "%Y%m%d%H%M")
        cat, v = it["category"], it["fcstValue"]
        rec = out.setdefault(t, {})
        try:
            if cat in ("T1H", "TMP"):
                rec["temp"] = float(v)
            elif cat == "REH":
                rec["humidity"] = float(v)
            elif cat in ("RN1", "PCP"):
                rec["rain"] = parse_rain_text(v)
        except ValueError:
            pass
    return out


def _ultra_base(now: datetime) -> datetime:
    b = now - timedelta(minutes=45)          # 매시 30분 발표, 45분 이후 제공
    return b.replace(minute=30, second=0, microsecond=0)


def _vilage_base(now: datetime) -> datetime:
    b = now - timedelta(minutes=15)          # 02·05·08·11·14·17·20·23시 발표
    h = max([x for x in (2, 5, 8, 11, 14, 17, 20, 23) if x <= b.hour], default=None)
    if h is None:
        b -= timedelta(days=1)
        h = 23
    return b.replace(hour=h, minute=0, second=0, microsecond=0)


def fetch_forecast(key: str, now: datetime, kind: str = "ultra", nx: int = DEFAULT_NX, ny: int = DEFAULT_NY):
    base = _ultra_base(now) if kind == "ultra" else _vilage_base(now)
    op = "getUltraSrtFcst" if kind == "ultra" else "getVilageFcst"
    q = urllib.parse.urlencode({"pageNo": 1, "numOfRows": 2000, "dataType": "JSON",
                                "base_date": base.strftime("%Y%m%d"), "base_time": base.strftime("%H%M"),
                                "nx": nx, "ny": ny, "authKey": key})
    text = _decode(http_get(f"{APIHUB}/api/typ02/openApi/VilageFcstInfoService_2.0/{op}?{q}"))
    try:
        return parse_vilage_json(text), base
    except (ValueError, KeyError) as e:
        raise WeatherAPIError(f"{op} 응답 해석 실패: {_scrub(text[:200])}") from e


# ---------------------------------------------------------------------------
# Open-Meteo (키 불필요)
# ---------------------------------------------------------------------------
def parse_open_meteo(text: str) -> dict[datetime, dict]:
    d = json.loads(text)
    if d.get("error"):
        raise WeatherAPIError("Open-Meteo: " + str(d.get("reason")))
    h = d["hourly"]
    out = {}
    for i, ts in enumerate(h["time"]):
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M")
        out[t] = {
            "pressure": h.get("pressure_msl", [None] * (i + 1))[i],
            "temp": h.get("temperature_2m", [None] * (i + 1))[i],
            "humidity": h.get("relative_humidity_2m", [None] * (i + 1))[i],
            "rain": h.get("precipitation", [0] * (i + 1))[i] or 0.0,
        }
    return out


def fetch_open_meteo(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> dict[datetime, dict]:
    base = {"latitude": lat, "longitude": lon, "timezone": "Asia/Seoul", "past_days": 2, "forecast_days": 3,
            "hourly": "pressure_msl,temperature_2m,relative_humidity_2m,precipitation"}
    last = None
    for extra in ({"models": "kma_seamless"}, {}):  # 기상청 수치모델 우선, 실패 시 기본 모델
        try:
            q = urllib.parse.urlencode({**base, **extra})
            data = parse_open_meteo(_decode(http_get(f"{OPEN_METEO}?{q}")))
            ok = sum(1 for v in data.values() if v["pressure"] is not None)
            if data and ok >= 0.9 * len(data):
                return data
        except (WeatherAPIError, ValueError, KeyError) as e:
            last = e
    raise WeatherAPIError(f"Open-Meteo 조회 실패: {last}")


def finite(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


# ---------------------------------------------------------------------------
# 공공데이터포털(data.go.kr) — 환경변수 DATA_GO_KR_KEY
#   · 기상청_지상(종관, ASOS) 시간자료 조회서비스 : 전날 자료까지 제공
#   · 기상청_단기예보 조회서비스 : 초단기실황(현재)·초단기예보·단기예보
# ---------------------------------------------------------------------------
def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def parse_asos_json(text: str) -> list[dict]:
    _check_portal_error(text)
    d = json.loads(text)
    head = d.get("response", {}).get("header", {})
    if head.get("resultCode") != "00":
        raise WeatherAPIError(f"ASOS 오류 {head.get('resultCode')}: {head.get('resultMsg')}")
    items = d["response"]["body"]["items"]["item"]
    rows = []
    for it in items:
        ps = _num(it.get("ps"), 900, 1100) or _num(it.get("pa"), 850, 1100)
        rows.append({"t": datetime.strptime(it["tm"], "%Y-%m-%d %H:%M"), "pressure": ps,
                     "temp": _num(it.get("ta"), -50, 50), "humidity": _num(it.get("hm"), 0, 100),
                     "rain": _num(it.get("rn"), 0, 300) or 0.0})  # 빈 값 = 무강수
    rows.sort(key=lambda r: r["t"])
    return rows


def fetch_asos_datagokr(key: str, now: datetime, hours: int = 50, stn: str = DEFAULT_STN) -> list[dict]:
    end = (now - timedelta(days=1)).replace(hour=23)  # 전날 23시까지만 제공
    start = min(now - timedelta(hours=hours), end - timedelta(hours=23))
    q = urllib.parse.urlencode({"serviceKey": key, "pageNo": 1, "numOfRows": 200, "dataType": "JSON",
                                "dataCd": "ASOS", "dateCd": "HR", "startDt": start.strftime("%Y%m%d"),
                                "startHh": start.strftime("%H"), "endDt": end.strftime("%Y%m%d"),
                                "endHh": "23", "stnIds": stn})
    return parse_asos_json(_decode(http_get(f"{DATAGOKR}/AsosHourlyInfoService/getWthrDataList?{q}")))


def parse_ncst_json(text: str) -> tuple[datetime, dict]:
    _check_portal_error(text)
    d = json.loads(text)
    head = d.get("response", {}).get("header", {})
    if head.get("resultCode") != "00":
        raise WeatherAPIError(f"초단기실황 오류 {head.get('resultCode')}: {head.get('resultMsg')}")
    rec, t = {}, None
    for it in d["response"]["body"]["items"]["item"]:
        t = datetime.strptime(it["baseDate"] + it["baseTime"], "%Y%m%d%H%M")
        v = it.get("obsrValue")
        if it["category"] == "T1H":
            rec["temp"] = _num(v, -50, 50)
        elif it["category"] == "REH":
            rec["humidity"] = _num(v, 0, 100)
        elif it["category"] == "RN1":
            rec["rain"] = parse_rain_text(v) if not _num(v, 0, 300) else float(v)
    return t, {k: v for k, v in rec.items() if v is not None}


def fetch_ncst_datagokr(key: str, now: datetime, nx: int = DEFAULT_NX, ny: int = DEFAULT_NY):
    base = (now - timedelta(minutes=40)).replace(minute=0, second=0, microsecond=0)  # 매시 정시, 40분 이후 제공
    q = urllib.parse.urlencode({"serviceKey": key, "pageNo": 1, "numOfRows": 50, "dataType": "JSON",
                                "base_date": base.strftime("%Y%m%d"), "base_time": base.strftime("%H%M"), "nx": nx, "ny": ny})
    return parse_ncst_json(_decode(http_get(f"{DATAGOKR}/VilageFcstInfoService_2.0/getUltraSrtNcst?{q}")))


def fetch_forecast_datagokr(key: str, now: datetime, kind: str = "ultra", nx: int = DEFAULT_NX, ny: int = DEFAULT_NY):
    base = _ultra_base(now) if kind == "ultra" else _vilage_base(now)
    op = "getUltraSrtFcst" if kind == "ultra" else "getVilageFcst"
    q = urllib.parse.urlencode({"serviceKey": key, "pageNo": 1, "numOfRows": 2000, "dataType": "JSON",
                                "base_date": base.strftime("%Y%m%d"), "base_time": base.strftime("%H%M"), "nx": nx, "ny": ny})
    text = _decode(http_get(f"{DATAGOKR}/VilageFcstInfoService_2.0/{op}?{q}"))
    try:
        return parse_vilage_json(text), base
    except (ValueError, KeyError) as e:
        raise WeatherAPIError(f"{op} 응답 해석 실패: {_scrub(text[:200])}") from e


# ---------------------------------------------------------------------------
# MET Norway Locationforecast (키 불필요, 해면기압 예보 포함)
# ---------------------------------------------------------------------------
def parse_metno(text: str) -> dict[datetime, dict]:
    d = json.loads(text)
    out = {}
    for ts in d["properties"]["timeseries"]:
        t = datetime.strptime(ts["time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=9)  # UTC → KST
        det = ts["data"]["instant"]["details"]
        n1 = (ts["data"].get("next_1_hours") or {}).get("details", {})
        out[t] = {"pressure": det.get("air_pressure_at_sea_level"), "temp": det.get("air_temperature"),
                  "humidity": det.get("relative_humidity"), "rain": n1.get("precipitation_amount")}
    # next_1_hours 강수는 '이후 1시간' → 정시 누적(앞 1시간) 기준으로 한 칸 이동
    keys = sorted(out)
    for a, b in zip(keys, keys[1:]):
        if b - a == timedelta(hours=1):
            out[b]["rain_acc"] = out[a]["rain"]
    for k in keys:
        out[k]["rain"] = out[k].pop("rain_acc", None) or 0.0
    if not out:
        raise WeatherAPIError("MET Norway: 자료 없음")
    return out


def fetch_metno(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> dict[datetime, dict]:
    try:
        return parse_metno(_decode(http_get(f"{METNO}?lat={lat:.4f}&lon={lon:.4f}")))
    except (ValueError, KeyError) as e:
        raise WeatherAPIError(f"MET Norway 응답 해석 실패: {e}") from None
