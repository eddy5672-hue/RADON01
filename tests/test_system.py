"""핵심 기능 검증 테스트:  python -m unittest discover -s tests -v"""
import json
import os
import sys
import threading
import unittest
from unittest import mock
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from radon.controller import ControlParams, Entry, FanController  # noqa: E402
from radon.engine import Engine  # noqa: E402
from radon.llm import LLMHub  # noqa: E402
from radon.mockdata import load_csv  # noqa: E402
from radon.predictor import RadonPredictor  # noqa: E402
from radon.sim import DEFAULT_SITES, RadonRoom  # noqa: E402
from radon.weather import SimWeather  # noqa: E402

MODEL = os.path.join(ROOT, "data", "model.json")


class TestSimulation(unittest.TestCase):
    def test_natural_equilibrium_matches_field_level(self):
        """환기 없을 때 배수펌프실 평형농도 ≈ 개선 전 실측 평균(432.4 Bq/㎥) 수준."""
        cfg = DEFAULT_SITES[0]
        w = SimWeather(datetime(2026, 11, 1))
        room = RadonRoom(cfg, c0=50)
        t = datetime(2026, 10, 20)
        vals = []
        for m in range(10 * 1440):
            room.step(w.at(t), False)
            if m > 3 * 1440:
                vals.append(room.true_c)
        avg = sum(vals) / len(vals)
        self.assertTrue(330 < avg < 560, avg)

    def test_fan_reduces_concentration_exponentially(self):
        cfg = DEFAULT_SITES[0]
        w = SimWeather(datetime(2026, 11, 1))
        room = RadonRoom(cfg, c0=430)
        t = datetime(2026, 11, 1)
        for _ in range(60):
            room.step(w.at(t), True)
        self.assertLess(room.true_c, 60)


class TestPredictor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = RadonPredictor.load(MODEL)

    def test_accuracy_better_than_persistence(self):
        for site, m in self.p.metrics.items():
            self.assertLess(m["120min"]["MAE_Bq"], m["120min"]["baseline_MAE_Bq"], site)
            self.assertLess(m["60min"]["MAPE_pct"], 20, site)

    def test_fan_plan_lowers_forecast(self):
        data = load_csv(os.path.join(ROOT, "data", "mock_radon_30days.csv"))
        hist = data["pump-a"][:400]
        fp = [hist[-1]["pressure"]] * 12
        fr = [0.0] * 12
        off = self.p.forecast("pump-a", hist, [0.0] * 12, fp, fr)
        on = self.p.forecast("pump-a", hist, [1.0] * 12, fp, fr)
        self.assertEqual(len(off), 12)
        self.assertLess(on[-1], off[-1])


class TestController(unittest.TestCase):
    def test_pre_ventilation_time(self):
        ctl = FanController(DEFAULT_SITES[0], ControlParams())
        self.assertGreater(ctl.pre_minutes(430), ctl.pre_minutes(150))
        self.assertEqual(ctl.pre_minutes(50), ControlParams().margin_min)

    def test_daily_entry_over_midnight(self):
        e = Entry("pump-a", "23:00", "01:00")
        occ = e.occurrences(datetime(2026, 11, 9, 23, 30), datetime(2026, 11, 9, 23, 40))
        self.assertEqual(occ[0][1], datetime(2026, 11, 10, 1, 0))

    def test_sensor_fault_failsafe(self):
        ctl = FanController(DEFAULT_SITES[0], ControlParams())
        fan, _ = ctl.decide(datetime(2026, 11, 9, 12), 0.0, [], None, sensor_fault=True)
        self.assertTrue(fan)


class TestEngineScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = Engine(RadonPredictor.load(MODEL), start=datetime(2026, 11, 9, 18, 0), warmup_hours=48)
        cls.e.advance(24 * 60)

    def test_safe_during_entries(self):
        """AUTO 모드: 출입시간 평균 농도가 안전 단계(100 Bq/㎥) 이하."""
        for sid, s in self.e.sites.items():
            st = self.e.stats(s)
            self.assertIsNotNone(st["entry_avg"], sid)
            self.assertLessEqual(st["entry_avg"], 100, sid)

    def test_energy_saving(self):
        """기존 20시간 가동 대비 70% 이상 절감."""
        for sid, s in self.e.sites.items():
            self.assertGreaterEqual(self.e.stats(s)["saving_pct"], 70, sid)

    def test_ready_notification(self):
        kinds = [ev["kind"] for ev in self.e.events]
        self.assertIn("ok", kinds)


class _FakeLLM(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["messages"][0]["role"] == "system"
        out = json.dumps({"choices": [{"message": {"content": f"[{body['model']}] 브리핑 생성"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestLLM(unittest.TestCase):
    def test_template_fallback_and_model_swap(self):
        e = Engine(RadonPredictor.load(MODEL), start=datetime(2026, 11, 9, 18, 0), warmup_hours=26)
        ctx = e.context_for_llm("pump-a")
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "", "OPENAI_COMPAT_BASE_URL": ""}):
            hub = LLMHub(None, discover=False)
        r = hub.generate("briefing", ctx)
        self.assertIn("진입 판단", r["text"])
        self.assertIn("템플릿", r["engine"])
        self.assertIn("환기 운영", hub.generate("report", ctx)["text"])

        srv = HTTPServer(("127.0.0.1", 0), _FakeLLM)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            env = {"GROQ_API_KEY": "", "OPENAI_COMPAT_BASE_URL": f"http://127.0.0.1:{srv.server_port}/v1",
                   "OPENAI_COMPAT_MODELS": "exaone3.5:7.8b,gemma3:4b", "OPENAI_COMPAT_NAME": "Test"}
            with mock.patch.dict(os.environ, env):
                hub = LLMHub(None, discover=False)
            ids = [m["id"] for m in hub.models()]
            self.assertEqual(len(ids), 2)
            for mid in ids:  # 모델 교체
                r = hub.generate("briefing", ctx, mid)
                self.assertIsNone(r["error"], r)
                self.assertIn(mid.split(":", 1)[1], r["text"])
            hub.providers["compat"].base_url = "http://127.0.0.1:9/v1"  # 연결 실패 → 템플릿 대체
            r = hub.generate("briefing", ctx, ids[0])
            self.assertIn("템플릿", r["engine"])
            self.assertIn("진입 판단", r["text"])
        finally:
            srv.shutdown()
            srv.server_close()


class TestSessions(unittest.TestCase):
    def test_sessions_are_independent_and_live_reference_is_shared(self):
        from radon.sessions import SessionManager
        mgr = SessionManager(RadonPredictor.load(MODEL), None, warmup_hours=26)
        a, b = mgr.get("a" * 32), mgr.get("b" * 32)
        self.assertIsNot(a, b)
        a.sim_event("rain")
        a.add_entry(Entry("pump-a", "10:00", "11:00", "테스트"))
        self.assertEqual(len(a.entries), len(b.entries) + 1)
        self.assertGreater(a.weather.at(a.sim_time).rain, b.weather.at(b.sim_time).rain - 0.001 + 5)
        self.assertIs(a.predictor, b.predictor)
        s = mgr.reset("a" * 32, "scenario")
        self.assertFalse(s.realtime)
        s.tick(1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
