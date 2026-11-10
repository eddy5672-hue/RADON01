"""실시간 기상 연동 파서·합성 로직 테스트 (네트워크 없이 예시 응답으로 검증)."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from radon import kma  # noqa: E402
from radon.weather import LiveWeather, OverlayWeather  # noqa: E402

NOW = datetime(2026, 11, 9, 14, 20)


def sfctm_text(start, hours):
    """API허브 kma_sfctm3.php 형식의 예시 응답 (헤더 + 고정폭 자료)."""
    lines = [
        "#START7777",
        "#--------------------------------------------------------------------------------------------------",
        "# YYMMDDHHMI STN  WD   WS GST  GST  GST     PA     PS PT    PR    TA    TD    HM    PV     RN     RN     RN     RN",
        "#        KST  ID  16  m/s  WD   WS   TM    hPa    hPa  -   hPa     C     C     %   hPa     mm    DAY    JUN    INT",
    ]
    for k in range(hours):
        t = start + timedelta(hours=k)
        ps = 1018.0 - 0.2 * k
        rn = "1.5" if k == hours - 2 else "-9.0"
        lines.append(f"{t:%Y%m%d%H%M} 108  20  2.1  -9 -9.0   -9 1008.1 {ps:6.1f}  8  -0.6  10.{k % 10}   1.2  {50 + k % 30}   6.8 {rn:>6}  -9.0  -9.0  -9.0")
    lines.append("#7777END")
    return "\n".join(lines)


def vilage_json(base, hours, cat_t="T1H", cat_r="RN1"):
    items = []
    for k in range(1, hours + 1):
        t = base + timedelta(hours=k)
        d, h = t.strftime("%Y%m%d"), t.strftime("%H00")
        items += [
            {"baseDate": base.strftime("%Y%m%d"), "baseTime": base.strftime("%H%M"), "category": cat_t, "fcstDate": d, "fcstTime": h, "fcstValue": "7", "nx": 60, "ny": 127},
            {"baseDate": base.strftime("%Y%m%d"), "baseTime": base.strftime("%H%M"), "category": "REH", "fcstDate": d, "fcstTime": h, "fcstValue": "70", "nx": 60, "ny": 127},
            {"baseDate": base.strftime("%Y%m%d"), "baseTime": base.strftime("%H%M"), "category": cat_r, "fcstDate": d, "fcstTime": h, "fcstValue": "강수없음" if k != 2 else "3.0mm", "nx": 60, "ny": 127},
        ]
    return json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                                    "body": {"dataType": "JSON", "items": {"item": items}}}}, ensure_ascii=False)


def open_meteo_json(start, hours):
    times = [(start + timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M") for k in range(hours)]
    return json.dumps({"hourly": {"time": times, "pressure_msl": [1020.0 - 0.1 * k for k in range(hours)],
                                  "temperature_2m": [8.0] * hours, "relative_humidity_2m": [60] * hours,
                                  "precipitation": [0.0] * hours}})


class TestParsers(unittest.TestCase):
    def test_sfctm_header_and_missing(self):
        rows = kma.parse_sfctm(sfctm_text(datetime(2026, 11, 9, 0), 5))
        self.assertEqual(len(rows), 5)
        self.assertAlmostEqual(rows[0]["pressure"], 1018.0)
        self.assertEqual(rows[0]["rain"], 0.0)          # -9.0 → 무강수
        self.assertAlmostEqual(rows[3]["rain"], 1.5)
        self.assertEqual(rows[0]["humidity"], 50)

    def test_sfctm_without_header_uses_default_columns(self):
        txt = "\n".join(l for l in sfctm_text(datetime(2026, 11, 9, 0), 3).splitlines() if not l.startswith("#"))
        rows = kma.parse_sfctm(txt)
        self.assertAlmostEqual(rows[1]["pressure"], 1017.8)

    def test_rain_text(self):
        self.assertEqual(kma.parse_rain_text("강수없음"), 0.0)
        self.assertEqual(kma.parse_rain_text("1mm 미만"), 0.5)
        self.assertEqual(kma.parse_rain_text("1.0mm"), 1.0)
        self.assertEqual(kma.parse_rain_text("30.0~50.0mm"), 40.0)
        self.assertEqual(kma.parse_rain_text("50.0mm 이상"), 50.0)

    def test_vilage_error_code(self):
        bad = json.dumps({"response": {"header": {"resultCode": "03", "resultMsg": "NO_DATA"}}})
        with self.assertRaises(kma.WeatherAPIError):
            kma.parse_vilage_json(bad)

    def test_base_times(self):
        self.assertEqual(kma._ultra_base(datetime(2026, 11, 9, 14, 20)), datetime(2026, 11, 9, 13, 30))
        self.assertEqual(kma._ultra_base(datetime(2026, 11, 9, 14, 50)), datetime(2026, 11, 9, 14, 30))
        self.assertEqual(kma._vilage_base(datetime(2026, 11, 9, 1, 0)), datetime(2026, 11, 8, 23, 0))
        self.assertEqual(kma._vilage_base(datetime(2026, 11, 9, 14, 20)), datetime(2026, 11, 9, 14, 0))

    def test_scrub_key(self):
        self.assertNotIn("SECRET", kma._scrub("https://x?authKey=SECRET&a=1"))


ASOS_SAMPLE = json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"}, "body": {"dataType": "JSON", "items": {"item": [
    {"tm": "2026-11-08 21:00", "stnId": "108", "ta": "5.9", "rn": "1.0", "hm": "87", "pa": "1023.3", "ps": "1034.0"},
    {"tm": "2026-11-08 22:00", "stnId": "108", "ta": "6.0", "rn": "", "hm": "87", "pa": "1023.1", "ps": "1033.8"}]},
    "pageNo": 1, "numOfRows": 2, "totalCount": 2}}}, ensure_ascii=False)  # 공공데이터포털 실제 응답 형식(2026-11-09 확인)


class TestPortal(unittest.TestCase):
    def test_asos_json(self):
        rows = kma.parse_asos_json(ASOS_SAMPLE)
        self.assertEqual(rows[0]["pressure"], 1034.0)
        self.assertEqual(rows[0]["rain"], 1.0)
        self.assertEqual(rows[1]["rain"], 0.0)

    def test_portal_errors(self):
        with self.assertRaises(kma.WeatherAPIError) as c:
            kma.parse_asos_json('{"response":{"header":{"resultCode":"99","resultMsg":"전날 자료까지 제공됩니다."}}}')
        self.assertIn("전날", str(c.exception))
        with self.assertRaises(kma.WeatherAPIError) as c:
            kma.parse_vilage_json('{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"errMsg":"SERVICE_KEY_IS_NOT_REGISTERED_ERROR","returnAuthMsg":"등록되지 않은 서비스키","returnReasonCode":"30"}}}')
        self.assertIn("등록되지 않은", str(c.exception))

    def test_ncst(self):
        txt = json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [
            {"baseDate": "20261109", "baseTime": "1800", "category": "T1H", "obsrValue": "7.1"},
            {"baseDate": "20261109", "baseTime": "1800", "category": "RN1", "obsrValue": "0"},
            {"baseDate": "20261109", "baseTime": "1800", "category": "REH", "obsrValue": "66"}]}}}})
        t, rec = kma.parse_ncst_json(txt)
        self.assertEqual(t, datetime(2026, 11, 9, 18))
        self.assertEqual(rec, {"temp": 7.1, "rain": 0.0, "humidity": 66.0})


class TestLiveWeather(unittest.TestCase):
    def _fake_get(self, url, timeout=15.0):
        if "met.no" in url:
            raise kma.WeatherAPIError("HTTP 503")
        if "kma_sfctm3" in url:
            return sfctm_text(datetime(2026, 11, 7, 12), 51).encode("euc-kr")
        if "getUltraSrtFcst" in url:
            return vilage_json(datetime(2026, 11, 9, 14), 6).encode()
        if "getVilageFcst" in url:
            return vilage_json(datetime(2026, 11, 9, 14), 60, "TMP", "PCP").encode()
        if "open-meteo" in url:
            return open_meteo_json(datetime(2026, 11, 7, 0), 24 * 5).encode()
        raise AssertionError(url)

    def test_merge_kma_obs_forecast_and_pressure_bias(self):
        with mock.patch.object(kma, "http_get", self._fake_get):
            lw = LiveWeather(lambda: NOW, "KEY")
        self.assertEqual(lw.status["source"], "kma")
        self.assertFalse(lw.status["errors"], lw.status["errors"])
        w_obs = lw.at(datetime(2026, 11, 9, 13, 0))
        self.assertLess(abs(w_obs.pressure - (1018.0 - 0.2 * 49)), 0.01)   # 관측값
        w_fc = lw.at(datetime(2026, 11, 9, 16, 0))                          # 예보 구간: 강수 3mm(초단기)
        self.assertEqual(w_fc.rain, 3.0)
        self.assertEqual(w_fc.temp, 7.0)
        # 기압 예보는 Open-Meteo에 관측 편차 보정 → 관측 마지막 값과 연속
        p_last_obs = lw.at(datetime(2026, 11, 9, 14, 0)).pressure
        p_next = lw.at(datetime(2026, 11, 9, 15, 0)).pressure
        self.assertLess(abs(p_next - p_last_obs), 1.0)

    def test_fallback_to_open_meteo_without_key(self):
        with mock.patch.object(kma, "http_get", self._fake_get):
            lw = LiveWeather(lambda: NOW, None)
        self.assertEqual(lw.status["source"], "open-meteo")
        self.assertAlmostEqual(lw.at(datetime(2026, 11, 8, 1)).pressure, 1017.5, places=1)

    def test_portal_key_path(self):
        def fake(url, timeout=15.0):
            if "met.no" in url:
                raise kma.WeatherAPIError("HTTP 503")
            if "AsosHourlyInfoService" in url:
                items = [{"tm": (datetime(2026, 11, 7, 12) + timedelta(hours=k)).strftime("%Y-%m-%d %H:%M"),
                          "ta": "5", "rn": "", "hm": "60", "ps": f"{1030 - 0.1 * k:.1f}"} for k in range(36)]
                return json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}}}}).encode()
            if "getUltraSrtNcst" in url:
                return json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [
                    {"baseDate": "20261109", "baseTime": "1300", "category": "RN1", "obsrValue": "2.5"}]}}}}).encode()
            if "VilageFcstInfoService" in url:
                return b'{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"returnAuthMsg":"SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}}'
            if "open-meteo" in url:
                return open_meteo_json(datetime(2026, 11, 7, 0), 24 * 5).encode()
            raise AssertionError(url)
        with mock.patch.object(kma, "http_get", fake):
            lw = LiveWeather(lambda: NOW, None, portal_key="K")
        self.assertEqual(lw.status["source"], "kma")
        self.assertEqual(lw.at(datetime(2026, 11, 9, 13, 0)).rain, 2.5)      # 실황 강수
        self.assertTrue(any("초단기예보" in e for e in lw.status["errors"]))   # 미신청 서비스 → 오류 표시
        # 오늘(관측 없음) 기압은 Open-Meteo + 전날 관측 기준 편차 보정
        self.assertLess(abs(lw.at(datetime(2026, 11, 8, 23)).pressure - (1030 - 0.1 * 35)), 0.01)
        self.assertLess(abs(lw.at(datetime(2026, 11, 9, 0)).pressure - lw.at(datetime(2026, 11, 8, 23)).pressure), 1.0)

    def test_metno_pressure_and_gap_interpolation(self):
        def metno_json(start, hours):
            ts = [{"time": (start + timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "data": {"instant": {"details": {"air_pressure_at_sea_level": 1029.0 + 0.1 * k, "air_temperature": 8.0,
                                                    "relative_humidity": 60.0}},
                            "next_1_hours": {"details": {"precipitation_amount": 1.0 if k == 3 else 0.0}}}} for k in range(hours)]
            return json.dumps({"properties": {"timeseries": ts}})

        def fake(url, timeout=15.0):
            if "met.no" in url:   # 현재(14시 KST = 05시 UTC)부터 예보
                return metno_json(datetime(2026, 11, 9, 5), 48).encode()
            if "AsosHourlyInfoService" in url:
                items = [{"tm": (datetime(2026, 11, 7, 12) + timedelta(hours=k)).strftime("%Y-%m-%d %H:%M"),
                          "ta": "5", "rn": "", "hm": "60", "ps": "1034.0"} for k in range(36)]
                return json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}}}}).encode()
            raise kma.WeatherAPIError("미신청")
        with mock.patch.object(kma, "http_get", fake):
            lw = LiveWeather(lambda: NOW, None, portal_key="K")
        self.assertIn("metno", lw.status["parts"])
        self.assertFalse(any("Open-Meteo" in e for e in lw.status["errors"]))
        self.assertAlmostEqual(lw.at(datetime(2026, 11, 8, 23)).pressure, 1034.0)      # 전날 관측
        self.assertAlmostEqual(lw.at(datetime(2026, 11, 9, 14)).pressure, 1029.0)      # MET Norway 현재
        mid = lw.at(datetime(2026, 11, 9, 6)).pressure                                  # 관측~예보 사이 보간
        self.assertTrue(1029.0 < mid < 1034.0, mid)
        self.assertEqual(lw.at(datetime(2026, 11, 9, 18)).rain, 1.0)                     # 17~18시 강수 1mm

    def test_all_fail_uses_synthetic(self):
        def boom(url, timeout=15.0):
            raise kma.WeatherAPIError("down")
        with mock.patch.object(kma, "http_get", boom):
            lw = LiveWeather(lambda: NOW, "KEY")
        self.assertEqual(lw.status["source"], "sim")
        self.assertGreater(lw.at(NOW).pressure, 900)

    def test_overlay_is_per_session(self):
        with mock.patch.object(kma, "http_get", self._fake_get):
            lw = LiveWeather(lambda: NOW, "KEY")
        a, b = OverlayWeather(lw), OverlayWeather(lw)
        a.add_rain(NOW, 2, 10)
        self.assertGreaterEqual(a.at(NOW).rain, 10)
        self.assertLess(b.at(NOW).rain, 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
