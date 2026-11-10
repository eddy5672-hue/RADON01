"""생성형 AI 연동 (모델 비종속 · 다중 공급자).

OpenAI 호환 Chat Completions API(`/chat/completions`)를 쓰는 모든 모델에 연결된다.
환경변수로 공급자를 등록하며, 화면에서 모델을 바꿔 같은 데이터로 결과를 비교할 수 있다.

  GROQ_API_KEY               Groq (GPT-OSS 120B/20B, Llama 3.x 등)       https://console.groq.com
  OPENAI_COMPAT_BASE_URL     추가 공급자 (예: Upstage Solar https://api.upstage.ai/v1,
  OPENAI_COMPAT_API_KEY        Ollama http://localhost:11434/v1, 행정안전부 AI 공통기반 게이트웨이 등)
  OPENAI_COMPAT_MODELS       쉼표로 구분한 모델명 (예: solar-pro2)
  OPENAI_COMPAT_NAME         표시 이름 (예: Upstage)
  LLM_DEFAULT_MODEL          기본 모델 (예: groq:openai/gpt-oss-120b)

LLM이 없거나 호출이 실패하면 규칙 기반 템플릿으로 자동 대체되어 기본 기능은 항상 동작한다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque

GROQ_BASE = "https://api.groq.com/openai/v1"
GROQ_FALLBACK_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
GROQ_PREFERRED = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
NON_CHAT = re.compile(r"whisper|guard|orpheus|tts|playai|safeguard|compound|distil|embed", re.I)

SYSTEM_PROMPT = (
    "당신은 서울 지하철 지하 밀폐공간(배수펌프실 등)의 라돈 안전관리 보조 AI입니다. "
    "제공된 JSON 데이터(측정값, AI 예측값, 환기 상태, 출입 일정, 기상 관측·예보)만 근거로 답하고, "
    "데이터에 없는 수치는 만들지 마십시오. 라돈 단계 기준(한국산업안전보건공단 권고): "
    "100 Bq/㎥ 이하 안전, 100 초과 관심, 300 초과 주의, 600 초과 위험. "
    "forecast_planned는 환기 계획 반영 예측, forecast_natural은 환기하지 않을 때 예측이며 10분 간격 12개(2시간) 값입니다. "
    "진입 가능 여부는 반드시 데이터의 entry_decision 값을 그대로 쓰고 절대 바꾸지 마십시오(100 초과 시 진입 가능이라고 쓰면 안 됨). "
    "기압이 기준보다 낮아지거나 하강할 때, 강수로 지하수가 늘 때 라돈 방출이 증가하며, 기압 상승·무강수는 증가 요인이 아닙니다. "
    "농도 단위는 Bq/㎥로 표기하고, 데이터의 필드명(entry_decision, forecast_planned 등)은 출력에 쓰지 마십시오. "
    "반드시 한국어로, 공문서체의 간결한 개조식으로 작성하고 마크다운 표는 쓰지 마십시오."
)

TASKS = {
    "briefing": "아래 데이터를 바탕으로 작업자 출입 전 '안전 브리핑'을 작성하십시오. "
                "형식: ① 현재 상태(농도·단계·환기팬) ② AI 예측(1시간·2시간 후) ③ 기상 영향(기압 변화·강수 예보가 라돈에 미치는 영향) "
                "④ 진입 가능 여부와 근거 ⑤ 작업 중 유의사항 3가지. 12줄 이내.",
    "report": "아래 데이터를 바탕으로 '일일 환기 운영 일지'를 작성하십시오. "
              "형식: □ 개요 □ 측정 결과(현재·출입시간 평균/최고) □ 환기 운영(가동시간·전력량, 기존 방식 대비 절감률) "
              "□ 기상 현황 □ 특이사항(이벤트) □ 조치 및 건의. 16줄 이내.",
}


def _label(model: str) -> str:
    m = model.lower()
    fam = ("GPT-OSS" if "gpt-oss" in m else "Llama" if "llama" in m else "Gemma" if "gemma" in m
           else "EXAONE" if "exaone" in m else "Solar" if "solar" in m else "Qwen" if "qwen" in m
           else "HyperCLOVA X" if "hcx" in m or "clova" in m else model)
    common = fam in ("GPT-OSS", "Llama", "Gemma", "EXAONE", "Solar", "HyperCLOVA X")
    return f"{model}" + (" · 공통기반 지원 계열" if common else "")


class Provider:
    def __init__(self, pid: str, name: str, base_url: str, api_key: str, models: list[str], discover: bool = False):
        self.id, self.name, self.base_url, self.api_key = pid, name, base_url.rstrip("/"), api_key
        self.models = models
        if discover:
            self.discover()

    def discover(self):
        try:
            req = urllib.request.Request(self.base_url + "/models", headers=self._headers())
            with urllib.request.urlopen(req, timeout=10) as r:
                ids = [m["id"] for m in json.loads(r.read().decode())["data"] if m.get("active", True)]
            ids = [i for i in ids if not NON_CHAT.search(i)]
            if ids:
                pref = [m for m in GROQ_PREFERRED if m in ids]
                self.models = pref + sorted(i for i in ids if i not in pref)
        except Exception:  # noqa: BLE001 — 목록 조회 실패 시 기본 목록 사용
            pass

    def _headers(self):
        h = {"Content-Type": "application/json", "User-Agent": "radon-ai/1.0"}
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        return h

    def chat(self, model: str, messages: list[dict], max_tokens: int = 1500, timeout: float = 60) -> str:
        body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"
        for attempt in range(2):
            req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                         headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    d = json.loads(r.read().decode("utf-8"))
                return (d["choices"][0]["message"].get("content") or "").strip()
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code == 400 and attempt == 0 and "reasoning_effort" in body:
                    body.pop("reasoning_effort")
                    continue
                raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        raise RuntimeError("응답 없음")


class LLMHub:
    def __init__(self, config_path: str | None = None, discover: bool = True):
        self.providers: dict[str, Provider] = {}
        self.lock = threading.Lock()
        self.calls: deque = deque()
        self.daily_limit = int(os.environ.get("LLM_DAILY_LIMIT", "800"))
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if key:
            self.providers["groq"] = Provider("groq", "Groq", GROQ_BASE, key, list(GROQ_FALLBACK_MODELS), discover)
        base = os.environ.get("OPENAI_COMPAT_BASE_URL", "").strip()
        if base:
            models = [m.strip() for m in os.environ.get("OPENAI_COMPAT_MODELS", "").split(",") if m.strip()]
            self.providers["compat"] = Provider("compat", os.environ.get("OPENAI_COMPAT_NAME", "OpenAI 호환"), base,
                                                os.environ.get("OPENAI_COMPAT_API_KEY", ""), models, discover and not models)
        if config_path and os.path.exists(config_path):  # 로컬 실행용 config.json
            try:
                with open(config_path, encoding="utf-8") as f:
                    c = json.load(f).get("llm", {})
                if c.get("provider") == "openai" and c.get("base_url") and c.get("model"):
                    self.providers["local"] = Provider("local", "로컬 설정", c["base_url"], c.get("api_key", ""), [c["model"]])
            except (OSError, ValueError):
                pass
        self.default = os.environ.get("LLM_DEFAULT_MODEL", "")
        if not self.default or self.default.split(":", 1)[0] not in self.providers:
            ms = self.models()
            self.default = ms[0]["id"] if ms else ""

    def models(self) -> list[dict]:
        out = []
        for p in self.providers.values():
            for m in p.models:
                out.append({"id": f"{p.id}:{m}", "provider": p.name, "model": m, "label": f"{p.name} · {_label(m)}"})
        return out

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def _resolve(self, model_id: str | None):
        mid = model_id or self.default
        if ":" not in mid:
            return None, None
        pid, model = mid.split(":", 1)
        p = self.providers.get(pid)
        if not p or model not in p.models:
            return None, None
        return p, model

    def _quota_ok(self) -> bool:
        now = time.time()
        with self.lock:
            while self.calls and now - self.calls[0] > 86400:
                self.calls.popleft()
            if len(self.calls) >= self.daily_limit:
                return False
            self.calls.append(now)
            return True

    def generate(self, task: str, context: dict, model_id: str | None = None) -> dict:
        t0 = time.time()
        p, model = self._resolve(model_id)
        if p is None:
            return {"text": template(task, context), "engine": "템플릿 (LLM 미설정)", "model": None,
                    "error": None, "sec": 0.0}
        if not self._quota_ok():
            return {"text": template(task, context), "engine": "템플릿 (일일 호출 한도 초과)", "model": None,
                    "error": "daily limit", "sec": 0.0}
        try:
            text = p.chat(model, [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": TASKS[task] + "\n\n[데이터]\n" + json.dumps(context, ensure_ascii=False, default=str)},
            ])
            if not text:
                raise RuntimeError("빈 응답")
            return {"text": clean_md(text), "engine": f"{p.name} · {model}", "model": f"{p.id}:{model}",
                    "error": None, "sec": round(time.time() - t0, 1)}
        except Exception as e:  # noqa: BLE001
            return {"text": template(task, context), "engine": f"템플릿 ({p.name} · {model} 호출 실패)",
                    "model": f"{p.id}:{model}", "error": str(e)[:300], "sec": round(time.time() - t0, 1)}

    def diag(self) -> dict:
        return {"providers": [{"id": p.id, "name": p.name, "base_url": p.base_url, "has_key": bool(p.api_key),
                               "models": p.models} for p in self.providers.values()],
                "default": self.default, "calls_24h": len(self.calls), "daily_limit": self.daily_limit}


def clean_md(t: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)  # 추론 모델의 사고 과정 제거
    t = re.sub(r"Bq\s*/\s*(?:\ufffd+\s*³?|m³|m\^3|m3)", "Bq/㎥", t)       # 단위 깨짐 보정
    t = re.sub(r"Bq\s*·\s*m⁻³", "Bq/㎥", t)
    t = re.sub(r"\s*\((?:entry_decision|forecast_\w+)[^)]*\)", "", t)          # 필드명 노출 제거
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    return t.strip()


# ---------------------------------------------------------------------------
def _fmt(v, unit=" Bq/㎥"):
    return "-" if v is None else f"{v:.0f}{unit}"


def template(task: str, c: dict) -> str:
    cur = c["current"]["radon"]
    fc = c.get("forecast_natural") or []
    fp = c.get("forecast_planned") or []
    st = c["stats"]
    ne = c.get("next_entry")
    w = c.get("weather", {})
    f60 = fp[5] if len(fp) > 5 else None
    f120 = fp[11] if len(fp) > 11 else None
    n60 = fc[5] if len(fc) > 5 else None
    wline = (f"기압 {w.get('해면기압hPa', '-')}hPa(3시간 {w.get('3시간기압변화hPa', 0):+}), "
             f"향후 6시간 강수 {w.get('향후6시간강수예보mm', 0)}mm [{w.get('출처', '-')}]")
    if task == "briefing":
        ok = cur <= 100 and (f60 is None or f60 <= 100)
        lines = [
            f"■ {c['name']} 작업 전 안전 브리핑 ({c['sim_time']})",
            f"① 현재 상태: 라돈 {_fmt(cur)} ({c['level']['label']}), 환기팬 {'가동' if c['fan'] else '정지'} — {c['reason']}",
            f"② AI 예측: 환기 계획 반영 시 1시간 후 {_fmt(f60)}, 2시간 후 {_fmt(f120)} / 무환기 시 1시간 후 {_fmt(n60)}",
            f"③ 기상: {wline}",
            f"④ 진입 판단: {c.get('entry_decision') or ('진입 가능 (안전 단계 유지 예상)' if ok else '진입 보류 — 환기 후 100 Bq/㎥ 이하 확인 필요')}",
        ]
        if ne:
            lines.append(f"   · 출입 일정: {ne['label']} {ne['start']}~{ne['end']}")
        lines += [
            "⑤ 유의사항",
            "   · 출입 전 모바일 앱에서 실시간 농도와 '진입 가능' 알림을 확인할 것",
            "   · 작업 중 환기팬 정지·농도 상승 알림 시 즉시 작업 중단 후 퇴실",
            "   · 집수정 인근 장시간 체류를 피하고 2인 1조로 작업",
        ]
        return "\n".join(lines)
    evs = c.get("recent_events") or []
    lines = [
        f"■ {c['name']} 일일 환기 운영 일지 ({c['sim_time']} 기준, 최근 24시간)",
        "□ 개요",
        f"   · 대상: {c['name']} ({c['desc']}), 운전 모드: {c['mode']}",
        "□ 측정 결과",
        f"   · 현재 라돈 {_fmt(cur)} ({c['level']['label']}), 온도 {c['current']['temp']}℃, 습도 {c['current']['rh']}%",
        f"   · 출입시간 평균 {_fmt(st.get('entry_avg'))}, 최고 {_fmt(st.get('entry_max'))}",
        "□ 환기 운영",
        f"   · 가동시간 {st['run_hours_24h']}시간 / 기존 방식 {st['legacy_hours']}시간",
        f"   · 전력량 {st['kwh_24h']} kWh / 기존 {st['legacy_kwh']} kWh → {st['saving_pct']}% 절감",
        "□ 기상 현황",
        f"   · {wline}",
        "□ 특이사항",
    ]
    lines += [f"   · {e}" for e in evs[-4:]] or ["   · 없음"]
    lines += [
        "□ 조치 및 건의",
        "   · 출입시간 농도 안전 단계 유지 여부 지속 모니터링" if (st.get('entry_max') or 0) <= 100
        else "   · 출입시간 중 안전 기준 초과 발생 — 초기환기 시간 상향 검토 필요",
    ]
    return "\n".join(lines)
