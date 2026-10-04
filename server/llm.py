"""답변 모델 연결: ollama(로컬·무료), gemini(Google·무료 등급, 외부 전송), claude(Anthropic·유료).
stream(provider, system, prompt, strict) 는 모델이 쓰는 글자 조각을 차례로 내준다(답을 쓰는 대로 화면에 보여 주기 위해).
strict=True 면 답 형식(JSON: answer + citations)을 모델에 강제한다.
"""
import json, os, re, urllib.error, urllib.request

from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT") or "300")
# 모델을 메모리에 붙잡아 두는 시간. 기본 5분이면 한동안 질문이 없을 때 다음 질문이 모델 불러오기만큼 느려진다
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE") or "60m"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
GEMINI_BASE_URL = (os.getenv("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
_gemini_thinking_off = True  # 빠른 답을 위해 '생각' 단계를 끈다. 모델이 지원하지 않으면 자동으로 해제

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

LABELS = {"gemini": "⚡ 빠름 (Gemini)", "ollama": "🔒 내부 (로컬)", "claude": "Claude"}
NOTES = {"gemini": "질문과 근거 조문이 Google로 전송돼요", "ollama": "이 서버 안에서만 처리해요 · 느려요",
         "claude": "질문과 근거 조문이 Anthropic으로 전송돼요"}
# 모델별로 한 번에 보내는 근거 조문 수 / 조문당 최대 글자 수 (로컬은 적게 보내야 빠름)
LIMITS = {"ollama": (int(os.getenv("TOP_K") or 3), int(os.getenv("CHUNK_CHARS") or 1000)),
          "gemini": (6, 3000), "claude": (8, 4000)}

ANSWER_SCHEMA = {
    "type": "object", "required": ["answer", "citations"],
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {
            "type": "object", "required": ["id", "quote"],
            "properties": {"id": {"type": "integer"}, "quote": {"type": "string"}}}},
    },
}


class ModelError(Exception):
    """모델 호출 실패. user_msg 는 화면에 보여 줄 문장, fallback 이면 다른 모델로 바꿔 시도할 만하다."""

    def __init__(self, provider, detail, user_msg, fallback=True):
        super().__init__(f"{provider}: {detail}")
        self.provider, self.user_msg, self.fallback = provider, user_msg, fallback


def available():
    """쓸 수 있는 모델: 로컬은 항상, 외부 모델은 .env에 키가 있을 때만."""
    return {"gemini": bool(GEMINI_API_KEY), "ollama": True, "claude": bool(ANTHROPIC_API_KEY)}  # 화면에 보이는 순서


def stream(provider, system, prompt, strict=True):
    if provider == "ollama":
        return _ollama(system, prompt, strict)
    if provider == "gemini":
        return _gemini(system, prompt, strict)
    if provider == "claude":
        return _claude(system, prompt)
    raise ModelError(provider, "unknown", f"알 수 없는 답변 모드예요: {provider}", fallback=False)


def _open(provider, req, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read()[:500].decode("utf-8", "replace")
        msg = {429: "무료 사용 한도에 걸렸어요", 401: "API 키가 맞지 않아요", 403: "API 키 권한이 없어요",
               404: "모델 이름을 찾을 수 없어요"}.get(e.code, f"모델 서버 오류({e.code})")
        err = ModelError(provider, f"HTTP {e.code} {detail}", msg)
        err.code, err.detail = e.code, detail
        raise err
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModelError(provider, str(e), "모델 서버에 연결하지 못했어요")


# ---------- Ollama (로컬) ----------
def _ollama(system, prompt, strict):
    body = {"model": OLLAMA_MODEL, "stream": True, "keep_alive": OLLAMA_KEEP_ALIVE,
            # num_predict: 답 길이 상한(토큰). 짧은 답 + 인용 1~2개면 충분하고, 길게 늘어지는 것을 막는다
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 450},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if strict:
        body["format"] = ANSWER_SCHEMA
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with _open("ollama", req, OLLAMA_TIMEOUT) as r:
        for line in r:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("error"):
                raise ModelError("ollama", d["error"], "로컬 모델이 답을 쓰다 멈췄어요")
            t = d.get("message", {}).get("content", "")
            if t:
                yield t
            if d.get("done"):
                return
    raise ModelError("ollama", "stream ended early", "로컬 모델이 답을 쓰다 멈췄어요")


# ---------- Gemini (Google AI Studio 무료 등급) ----------
def _gemini_schema(s):
    """Gemini는 형식표의 type 이름을 대문자로 받는다(OBJECT, STRING …)."""
    if isinstance(s, dict):
        return {k: (v.upper() if k == "type" else _gemini_schema(v)) for k, v in s.items()}
    if isinstance(s, list):
        return [_gemini_schema(x) for x in s]
    return s


def _gemini(system, prompt, strict):
    global _gemini_thinking_off
    if not GEMINI_API_KEY:
        raise ModelError("gemini", "no key", ".env에 GEMINI_API_KEY가 없어요")
    cfg = {"temperature": 0, "maxOutputTokens": 1024}
    if strict:
        cfg.update(responseMimeType="application/json", responseSchema=_gemini_schema(ANSWER_SCHEMA))
    if _gemini_thinking_off:
        cfg["thinkingConfig"] = {"thinkingBudget": 0}
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg}
    req = urllib.request.Request(f"{GEMINI_BASE_URL}/models/{GEMINI_MODEL}:streamGenerateContent?alt=sse",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY})
    try:
        r = _open("gemini", req, 120)
    except ModelError as e:
        if getattr(e, "code", 0) == 400 and "thinking" in getattr(e, "detail", "").lower() and _gemini_thinking_off:
            _gemini_thinking_off = False  # 이 모델은 '생각 끄기'를 지원하지 않음 → 끄지 않고 다시
            yield from _gemini(system, prompt, strict)
            return
        raise
    with r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            d = json.loads(line[5:])
            if d.get("promptFeedback", {}).get("blockReason"):
                raise ModelError("gemini", d["promptFeedback"]["blockReason"], "Gemini가 이 질문에 답하지 않았어요")
            for cand in d.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    if part.get("text") and not part.get("thought"):
                        yield part["text"]


# ---------- Claude (Anthropic, 유료) ----------
def _claude(system, prompt):
    if not ANTHROPIC_API_KEY:
        raise ModelError("claude", "no key", ".env에 ANTHROPIC_API_KEY가 없어요")
    from anthropic import Anthropic, APIError  # Claude를 쓸 때만 필요
    try:
        with Anthropic().messages.stream(model=CLAUDE_MODEL, max_tokens=1500, system=system,
                                         messages=[{"role": "user", "content": prompt}]) as s:
            yield from s.text_stream
    except APIError as e:
        raise ModelError("claude", str(e), "Claude 호출에 실패했어요")


# ---------- 쓰는 중인 JSON에서 answer 글자만 꺼내기 ----------
class AnswerStream:
    """모델이 {"answer": "…", "citations": …} 를 한 조각씩 쓰는 동안, answer 값의 새 글자만 돌려준다."""
    _ESC = {"n": "\n", "t": "\t", "r": "", "b": "", "f": "", "/": "/", "\\": "\\", '"': '"'}

    def __init__(self):
        self.buf, self.pos, self.done = "", None, False

    def feed(self, text):
        self.buf += text
        if self.done:
            return ""
        if self.pos is None:
            m = re.search(r'"answer"\s*:\s*"', self.buf)
            if not m:
                return ""
            self.pos = m.end()
        out, i, b = [], self.pos, self.buf
        while i < len(b):
            ch = b[i]
            if ch == '"':
                self.done = True
                i += 1
                break
            if ch == "\\":
                if i + 1 >= len(b):
                    break  # 이스케이프가 다음 조각에서 이어진다
                e = b[i + 1]
                if e == "u":
                    if i + 6 > len(b):
                        break
                    try:
                        out.append(chr(int(b[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                out.append(self._ESC.get(e, e))
                i += 2
                continue
            out.append(ch)
            i += 1
        self.pos = i
        return "".join(out)
