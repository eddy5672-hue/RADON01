"""접속자별 독립 시뮬레이션 세션 관리.

온라인 공개 시연에서 여러 심사위원이 동시에 접속해도 서로의 조작(출입 등록·이벤트·배속)이
영향을 주지 않도록, 접속자마다 엔진을 복제해 독립 운영한다.
- 기준 엔진(reference): 실시간 기상으로 실제 시각에 맞춰 상시 구동 → 새 세션은 이를 복제(즉시 시작)
- 시나리오 템플릿: 합성 기상으로 가속 시연용
"""
from __future__ import annotations

import os
import threading
import time
import uuid

from .engine import Engine, now_kst
from .weather import OverlayWeather, SimWeather

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "40"))
SESSION_TTL = int(os.environ.get("SESSION_TTL_SEC", "3600"))


class SessionManager:
    def __init__(self, predictor, live_weather=None, warmup_hours: int = 48):
        self.lock = threading.Lock()
        self.predictor = predictor
        self.live_weather = live_weather
        base = live_weather if live_weather is not None else SimWeather(now_kst(), seed=7)
        self.reference = Engine(predictor, weather=OverlayWeather(base), realtime=True, warmup_hours=warmup_hours)
        self.reference.allow_remote_control = True
        self.scenario = Engine(predictor, weather=OverlayWeather(SimWeather(now_kst(), seed=11)),
                               speed=300, warmup_hours=warmup_hours)
        self.sessions: dict[str, list] = {}   # sid -> [engine, last_access]
        self._thread = None
        self.allow_remote_control = True

    def new_sid(self) -> str:
        return uuid.uuid4().hex

    def get(self, sid: str) -> Engine:
        with self.lock:
            item = self.sessions.get(sid)
            if item:
                item[1] = time.time()
                return item[0]
            if len(self.sessions) >= MAX_SESSIONS:  # 가장 오래 쓰지 않은 세션 정리
                oldest = min(self.sessions, key=lambda k: self.sessions[k][1])
                self.sessions.pop(oldest, None)
            eng = self.reference.clone()
            eng.allow_remote_control = self.allow_remote_control
            self.sessions[sid] = [eng, time.time()]
            return eng

    def reset(self, sid: str, mode: str = "live") -> Engine:
        src = self.reference if mode == "live" else self.scenario
        eng = src.clone()
        eng.allow_remote_control = self.allow_remote_control
        if mode != "live":
            eng.realtime = False
            eng.speed = 300
        with self.lock:
            self.sessions[sid] = [eng, time.time()]
        return eng

    def count(self) -> int:
        return len(self.sessions)

    # ------------------------------------------------------------------
    def run(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        last = time.time()
        last_gc = last
        while True:
            time.sleep(0.25)
            now = time.time()
            dt, last = now - last, now
            try:
                self.reference.tick(dt)
                with self.lock:
                    engines = [v[0] for v in self.sessions.values()]
                for e in engines:
                    e.tick(dt)
                if now - last_gc > 60:
                    last_gc = now
                    with self.lock:
                        for k in [k for k, v in self.sessions.items() if now - v[1] > SESSION_TTL]:
                            self.sessions.pop(k, None)
            except Exception as e:  # noqa: BLE001 — 한 세션 오류가 전체를 멈추지 않도록
                print("[세션 루프 오류]", e)
