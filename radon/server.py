"""HTTP 서버 (Python 표준 라이브러리만 사용). 접속자별 세션(쿠키)으로 독립 시뮬레이션을 제공한다."""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .controller import Entry
from .llm import LLMHub
from .sessions import SessionManager

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PAGES = {"/": "index.html", "/mobile": "mobile.html", "/device": "device.html"}
SESSION_LLM_PER_HOUR = int(os.environ.get("LLM_SESSION_PER_HOUR", "30"))
SID_RE = re.compile(r"^[0-9a-f]{32}$")


def make_handler(mgr: SessionManager, llm: LLMHub, info: dict):
    llm_hist: dict[str, deque] = defaultdict(deque)
    llm_lock = threading.Lock()
    diag_cache = {"t": 0.0, "data": None}

    def llm_allowed(sid: str) -> bool:
        now = time.time()
        with llm_lock:
            q = llm_hist[sid]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= SESSION_LLM_PER_HOUR:
                return False
            q.append(now)
            return True

    class Handler(BaseHTTPRequestHandler):
        server_version = "RadonAI/2.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        # ---- 세션 --------------------------------------------------------
        def _sid(self) -> str:
            ck = self.headers.get("Cookie", "")
            m = re.search(r"(?:^|;\s*)sid=([0-9a-f]{32})", ck)
            if m and SID_RE.match(m.group(1)):
                self._new_sid = None
                return m.group(1)
            sid = mgr.new_sid()
            self._new_sid = sid
            return sid

        def _engine(self):
            self.sid = self._sid()
            return mgr.get(self.sid)

        # ---- 응답 헬퍼 -----------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if getattr(self, "_new_sid", None):
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                self.send_header("Set-Cookie", f"sid={self._new_sid}; Path=/; Max-Age=86400; HttpOnly; SameSite=Lax{secure}")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n or n > 100_000:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return {}

        def _site(self, eng, q, body=None):
            sid = (body or {}).get("site") or (q.get("site") or [None])[0]
            if sid not in eng.sites:
                raise KeyError("알 수 없는 현장: %s" % sid)
            return sid

        # ---- GET ---------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            p = u.path
            self._new_sid = None
            try:
                if p in PAGES:
                    self._engine()  # 첫 접속 시 세션 생성
                    return self._static(PAGES[p])
                if p == "/healthz":
                    return self._json({"ok": True, "sessions": mgr.count()})
                if p == "/api/weather":
                    ref = mgr.reference
                    return self._json(mgr.live_weather.summary(ref.sim_time) if mgr.live_weather else
                                      {"source": "sim", "label": "합성 기상"})
                if p == "/api/ai/models":
                    return self._json({"models": llm.models(), "default": llm.default, "enabled": llm.enabled})
                if p == "/api/diag":
                    return self._diag()
                if p.startswith("/static/"):
                    return self._static(p[len("/static/"):])
                if p in ("/manifest.json", "/sw.js", "/icon.svg"):
                    return self._static(p[1:])
                if not p.startswith("/api/"):
                    return self._json({"error": "not found"}, 404)
                eng = self._engine()
                if p == "/api/state":
                    return self._json(eng.state())
                if p == "/api/sites":
                    return self._json([s.cfg.to_dict() for s in eng.sites.values()])
                if p == "/api/history":
                    return self._json(eng.history(self._site(eng, q), min(168.0, float((q.get("hours") or [12])[0]))))
                if p == "/api/events":
                    return self._json(eng.events_since(int((q.get("since") or [0])[0])))
                if p == "/api/entries":
                    with eng.lock:
                        return self._json([e.to_dict() for e in eng.entries])
                if p == "/api/model":
                    return self._json({**info, "metrics": eng.predictor.metrics,
                                       "trained_at": eng.predictor.trained_at, "source": eng.predictor.source})
                if p == "/api/export.csv":
                    return self._export(eng, self._site(eng, q))
                self._json({"error": "not found"}, 404)
            except (KeyError, ValueError) as e:
                self._json({"error": str(e)}, 400)

        def _diag(self):
            # 외부 API 상태 점검 (인증키는 노출하지 않음). 과도한 호출 방지를 위해 60초 캐시
            now = time.time()
            if diag_cache["data"] is None or now - diag_cache["t"] > 60:
                lw = mgr.live_weather
                data = {"weather": lw.status if lw else None,
                        "weather_raw_sample": (lw.last_raw.get("asos") if lw else None),
                        "llm": llm.diag(), "sessions": mgr.count(),
                        "reference_time": mgr.reference.sim_time.strftime("%Y-%m-%d %H:%M")}
                diag_cache.update(t=now, data=data)
            return self._json(diag_cache["data"])

        def _static(self, rel: str):
            path = os.path.normpath(os.path.join(WEB_DIR, rel))
            if not path.startswith(WEB_DIR) or not os.path.isfile(path):
                return self._json({"error": "not found"}, 404)
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if path.endswith(".js"):
                ctype = "application/javascript"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
                ctype += "; charset=utf-8"
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype)

        def _export(self, eng, sid: str):
            h = eng.history(sid, 24 * 7)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["time", "site", "radon", "temp", "rh", "fan", "pressure", "rain"])
            for i, t in enumerate(h["t"]):
                w.writerow([t, sid, h["radon"][i], h["temp"][i], h["rh"][i], h["fan"][i], h["pressure"][i], h["rain"][i]])
            self._send(200, buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="radon_{sid}.csv"'})

        # ---- POST --------------------------------------------------------
        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            p = u.path
            b = self._body()
            self._new_sid = None
            try:
                if p == "/api/session/reset":
                    self.sid = self._sid()
                    mode = "live" if b.get("mode", "live") == "live" else "scenario"
                    mgr.reset(self.sid, mode)
                    return self._json({"ok": True, "mode": mode})
                eng = self._engine()
                if p == "/api/mode":
                    mode = b.get("mode", "AUTO").upper()
                    if mode not in ("AUTO", "MANUAL", "LEGACY"):
                        return self._json({"error": "mode"}, 400)
                    eng.set_mode(self._site(eng, q, b), mode)
                    return self._json({"ok": True})
                if p == "/api/fan":
                    src = b.get("source", "관제")
                    if src == "관제" and not eng.allow_remote_control:
                        return self._json({"error": "원격 제어가 보안 정책으로 비활성화되어 있습니다."}, 403)
                    eng.set_fan(self._site(eng, q, b), bool(b.get("on")), "현장 단말기" if src != "관제" else "관제")
                    return self._json({"ok": True})
                if p == "/api/entries":
                    sid = self._site(eng, q, b)
                    if len(eng.entries) >= 30:
                        return self._json({"error": "등록 가능한 일정 수를 초과했습니다."}, 400)
                    label = str(b.get("label") or "")[:30]
                    if b.get("quick_minutes") is not None:
                        st = eng.sim_time + timedelta(minutes=max(0, min(720, int(b["quick_minutes"]))))
                        en = st + timedelta(minutes=max(10, min(480, int(b.get("duration", 60)))))
                        e = Entry(sid, st.strftime("%Y-%m-%d %H:%M"), en.strftime("%Y-%m-%d %H:%M"),
                                  label or "긴급 출입", daily=False)
                    else:
                        e = Entry(sid, str(b["start"])[:5], str(b["end"])[:5], label or "점검 작업", daily=bool(b.get("daily", True)))
                        e.occurrences(eng.sim_time, eng.sim_time)  # 형식 검증
                    eng.add_entry(e)
                    return self._json({"ok": True, "entry": e.to_dict()})
                if p == "/api/entries/delete":
                    eng.remove_entry(str(b.get("id", "")))
                    return self._json({"ok": True})
                if p == "/api/sim":
                    with eng.lock:
                        if "speed" in b:
                            sp = max(1.0, min(900.0, float(b["speed"])))
                            eng.speed = sp
                            if sp > 1:
                                eng.realtime = False
                            elif eng.live:
                                eng.realtime = True
                        if "paused" in b:
                            eng.paused = bool(b["paused"])
                        if "remote" in b:
                            eng.allow_remote_control = bool(b["remote"])
                    if b.get("event") in ("rain", "lowpressure", "surge", "fault"):
                        eng.sim_event(b["event"], b.get("site") if b.get("site") in eng.sites else None)
                    if b.get("advance"):
                        if eng.realtime:
                            return self._json({"error": "실시간 모드에서는 시간을 앞당길 수 없습니다."}, 400)
                        eng.advance(max(0, min(720, int(b["advance"]))))
                    return self._json({"ok": True})
                if p in ("/api/ai/briefing", "/api/ai/report"):
                    task = "briefing" if p.endswith("briefing") else "report"
                    ctx = eng.context_for_llm(self._site(eng, q, b))
                    if not llm_allowed(self.sid):
                        return self._json({"text": "", "engine": "-", "error": "잠시 후 다시 시도해 주세요 (세션당 시간당 호출 한도)."}, 429)
                    return self._json(llm.generate(task, ctx, b.get("model")))
                if p == "/api/ai/compare":
                    task = b.get("task") if b.get("task") in ("briefing", "report") else "briefing"
                    ctx = eng.context_for_llm(self._site(eng, q, b))
                    models = [m for m in (b.get("models") or [])][:3]
                    res = [None] * len(models)

                    def run(i, m):
                        res[i] = llm.generate(task, ctx, m)
                    ths = []
                    for i, m in enumerate(models):
                        if not llm_allowed(self.sid):
                            res[i] = {"text": "", "engine": m, "error": "호출 한도 초과"}
                            continue
                        th = threading.Thread(target=run, args=(i, m))
                        th.start()
                        ths.append(th)
                    for th in ths:
                        th.join(90)
                    return self._json({"results": res})
                self._json({"error": "not found"}, 404)
            except (KeyError, ValueError, TypeError) as e:
                self._json({"error": str(e)}, 400)

    return Handler


def serve(mgr: SessionManager, llm: LLMHub, host: str, port: int, info: dict):
    httpd = ThreadingHTTPServer((host, port), make_handler(mgr, llm, info))
    httpd.daemon_threads = True
    return httpd
