r"""답변 모드·스트리밍 점검. 진짜 Gemini 키 없이, 이 스크립트가 띄우는 '가짜 Gemini'로 확인한다.
    .venv\Scripts\python.exe llm_test.py
- 가짜 Gemini(127.0.0.1:8765)와, 그것을 바라보는 시험용 docchat 서버(127.0.0.1:8001)를 잠깐 띄운다
- 시험용 서버는 같은 data\ 를 쓴다(질문 기록이 남는다)
"""
import json, os, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import llm

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))


print("[1] 쓰는 중인 JSON에서 answer 글자 꺼내기")
full = '{"answer": "보험기간은 \\"1년\\"이 기본이에요.\\n예외도 있어요 \\uD55C", "citations": [{"id": 1, "quote": "x"}]}'
for size in (1, 3, 7):
    a, got = llm.AnswerStream(), ""
    for i in range(0, len(full), size):
        got += a.feed(full[i:i + size])
    check(f"{size}글자씩 끊어 받아도 정확히 복원", got == json.loads(full)["answer"], repr(got))

# ---------- 가짜 Gemini ----------
MODE = {"kind": "ok"}
ANSWER = {"answer": "풍수해ㆍ지진재해보험의 보험기간은 1년이 기본이에요. 보험목적물에 따라 다르게 정할 수 있어요.",
          "citations": [{"id": 1, "quote": "풍수해ㆍ지진재해보험사업의 보험기간은 1년을 기본 단위로 한다."}]}
SEEN = {}


class FakeGemini(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.update(path=self.path, key=self.headers.get("x-goog-api-key"), body=body)
        if MODE["kind"] == "429":
            self.send_response(429); self.end_headers()
            self.wfile.write(b'{"error":{"code":429,"message":"Resource has been exhausted"}}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        text = json.dumps(ANSWER, ensure_ascii=False)
        for i in range(0, len(text), 12):  # 12글자씩 나눠 보낸다
            chunk = {"candidates": [{"content": {"parts": [{"text": text[i:i + 12]}], "role": "model"}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\r\n\r\n".encode())
            self.wfile.flush()
            time.sleep(0.02)


fake = ThreadingHTTPServer(("127.0.0.1", 8765), FakeGemini)
threading.Thread(target=fake.serve_forever, daemon=True).start()

env = dict(os.environ, GEMINI_API_KEY="test-key", GEMINI_BASE_URL="http://127.0.0.1:8765", GEMINI_MODEL="fake-model",
           PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8001"],
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8001"
try:
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/index.html", timeout=1); break
        except Exception:
            time.sleep(0.5)

    def call(method, path, token=None, body=None):
        h = {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {token}"} if token else {})
        req = urllib.request.Request(BASE + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def stream(token, body):
        req = urllib.request.Request(BASE + "/api/ask/stream", method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        events, t0, first = [], time.time(), None
        with urllib.request.urlopen(req, timeout=600) as r:
            kind = None
            for line in r:
                line = line.decode().strip()
                if line.startswith("event:"):
                    kind = line[6:].strip()
                elif line.startswith("data:"):
                    if kind == "delta" and first is None:
                        first = time.time() - t0
                    events.append((kind, json.loads(line[5:])))
        return events, first, time.time() - t0

    tok = call("POST", "/api/auth/login", body={"email": os.environ.get("SMOKE_EMAIL", "tester1791105360@docchat.test"),
                                                "password": os.environ.get("SMOKE_PASSWORD", "NewPass5678x")})[1]["token"]

    print("\n[2] 답변 모드 목록")
    st, m = call("GET", "/api/modes", tok)
    ids = [x["id"] for x in m.get("modes", [])]
    check("키가 있으면 Gemini와 로컬 모드를 고를 수 있음", st == 200 and set(ids) >= {"gemini", "ollama"}, f"{ids}, 기본 {m.get('default')}")
    st, r = call("POST", "/api/ask", tok, {"question": "보험기간은 얼마예요?", "mode": "claude"})
    check("허용되지 않은 모드(키 없는 Claude) 거절", st == 400, r.get("detail", ""))

    print("\n[3] Gemini 모드 + 스트리밍 (가짜 Gemini)")
    MODE["kind"] = "ok"
    ev, first, total = stream(tok, {"question": "보험기간은 얼마예요?", "mode": "gemini"})
    deltas = [d["text"] for k, d in ev if k == "delta"]
    done = next((d for k, d in ev if k == "done"), {})
    check("답이 여러 조각으로 흘러옴", len(deltas) > 3, f"{len(deltas)}조각, 첫 글자까지 {first:.1f}초, 전체 {total:.1f}초")
    check("흘러온 글자를 이으면 최종 답과 같음", "".join(deltas) == done.get("answer"))
    check("Gemini로 답하고 인용 확인", done.get("mode") == "gemini" and done.get("verified") is True,
          f"{done.get('mode_label')} · 인용 {[q for s in done.get('sources', []) for q in s['quotes']]}")
    sent = SEEN.get("body", {})
    check("Gemini 요청: 키는 헤더로, 형식(JSON) 강제, 근거 6개까지",
          SEEN.get("key") == "test-key" and "key=" not in SEEN.get("path", "") and
          sent.get("generationConfig", {}).get("responseMimeType") == "application/json" and
          sent["contents"][0]["parts"][0]["text"].count("<source ") <= 6,
          f"근거 {sent['contents'][0]['parts'][0]['text'].count('<source ')}개")

    print("\n[4] Gemini 한도 초과(429) → 로컬 모델로 자동 전환 (실제 Ollama, 시간이 걸림)")
    MODE["kind"] = "429"
    ev, first, total = stream(tok, {"question": "보험기간은 얼마예요?", "mode": "gemini"})
    resets = [d for k, d in ev if k == "reset"]
    done = next((d for k, d in ev if k == "done"), {})
    check("다른 모델로 다시 답한다는 안내", bool(resets), resets[0]["note"] if resets else "")
    check("로컬 모델로 답하고 안내 문구 포함", done.get("mode") == "ollama" and bool(done.get("note")),
          f"{done.get('note')} · 첫 글자까지 {first or 0:.0f}초, 전체 {total:.0f}초 · verified={done.get('verified')}")
finally:
    srv.terminate()
    fake.shutdown()

print(f"\n결과: {sum(results)}/{len(results)} 통과")
