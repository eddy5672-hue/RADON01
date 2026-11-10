#!/usr/bin/env python3
"""라돈 안심 AI — AI 기반 지하 밀폐공간 라돈 예측·선제환기 관리 시스템 실행기.

    python run.py                         # http://localhost:8080
    python run.py --offline               # 외부 기상 API 없이 합성 기상으로 실행
환경변수: PORT, DATA_GO_KR_KEY(공공데이터포털 기상청) 또는 KMA_AUTH_KEY(API허브), GROQ_API_KEY(LLM) — README 참조.
외부 패키지 설치가 필요 없습니다 (Python 3.9 이상 표준 라이브러리만 사용).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from radon.engine import now_kst  # noqa: E402
from radon.llm import LLMHub  # noqa: E402
from radon.mockdata import generate, load_csv, save_csv  # noqa: E402
from radon.predictor import RadonPredictor  # noqa: E402
from radon.server import serve  # noqa: E402
from radon.sessions import SessionManager  # noqa: E402
from radon.sim import TRAINING_SITES  # noqa: E402
from radon.weather import LiveWeather  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data")       # 투입 자료 (읽기 전용)
OUT_DIR = os.path.join(ROOT, "output")      # 산출물 (모델·로그)
MOCK_CSV = os.path.join(DATA_DIR, "mock_radon_30days.csv")
BUNDLED_MODEL = os.path.join(DATA_DIR, "model.json")
OUT_MODEL = os.path.join(OUT_DIR, "model.json")
CONFIG = os.path.join(ROOT, "config.json")


def load_model(retrain: bool) -> RadonPredictor:
    os.makedirs(OUT_DIR, exist_ok=True)
    if not retrain:
        for path in (OUT_MODEL, BUNDLED_MODEL):
            if os.path.exists(path):
                print(f"[모델] 불러오기: {os.path.relpath(path, ROOT)}", flush=True)
                return RadonPredictor.load(path)
    if os.path.exists(MOCK_CSV):
        data = load_csv(MOCK_CSV)
    else:
        data = generate(TRAINING_SITES, datetime(2026, 9, 1), 30)
        save_csv(data, os.path.join(OUT_DIR, "mock_radon_30days.csv"))
    p = RadonPredictor()
    p.train(data, source="모의데이터(30일·3개소, 10분 간격)")
    p.save(OUT_MODEL)
    return p


def main():
    ap = argparse.ArgumentParser(description="라돈 안심 AI")
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    ap.add_argument("--offline", action="store_true", help="외부 기상 API를 사용하지 않음")
    ap.add_argument("--retrain", action="store_true", help="저장된 모델을 무시하고 다시 학습")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    cloud = bool(os.environ.get("RENDER") or os.environ.get("PORT"))

    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)

    predictor = load_model(args.retrain)
    live = None
    if not args.offline and os.environ.get("WEATHER_MODE", "live") != "offline":
        print("[기상] 실시간 기상 수집 중...", flush=True)
        live = LiveWeather(now_kst, os.environ.get("KMA_AUTH_KEY", "").strip() or None,
                           portal_key=os.environ.get("DATA_GO_KR_KEY", "").strip() or None)
        print(f"[기상] {live.status['label']} {live.status['parts']} {live.status['errors'] or ''}", flush=True)
        live.run()
    mgr = SessionManager(predictor, live)
    remote = os.environ.get("ALLOW_REMOTE_CONTROL", str(cfg.get("allow_remote_control", True))).lower() in ("1", "true", "yes")
    mgr.allow_remote_control = remote
    llm = LLMHub(CONFIG)
    httpd = serve(mgr, llm, args.host, args.port,
                  {"features": "기압·기압변화·강수(실황/예보)·실내 라돈 추세·온습도·환기계획"})
    mgr.run()
    url = f"http://localhost:{args.port}"
    print("=" * 60)
    print(" 라돈 안심 AI — AI 라돈 예측·선제환기 관리 시스템")
    print(f"  관제 모니터링 : {url}/")
    print(f"  모바일 앱     : {url}/mobile")
    print(f"  가상 단말기   : {url}/device")
    print(f"  기상          : {live.status['label'] if live else '합성 기상(오프라인)'}")
    print(f"  LLM           : {llm.default or '미설정 (템플릿 모드)'} / 모델 {len(llm.models())}개")
    print("=" * 60, flush=True)
    if not args.no_browser and not cloud:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
