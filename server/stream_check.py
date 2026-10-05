r"""공개 주소로 스트리밍 확인: 첫 글자까지 시간, 조각 수, 최종 결과
    set SMOKE_BASE=https://docchat.tail15f4b2.ts.net
    .venv\Scripts\python.exe stream_check.py [gemini|ollama]
"""
import json, os, sys, time, urllib.request

from smoke_test import call

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000")
mode = sys.argv[1] if len(sys.argv) > 1 else None
tok = call("POST", "/api/auth/login", body={"email": os.environ["SMOKE_EMAIL"], "password": os.environ["SMOKE_PASSWORD"]})[1]["token"]
req = urllib.request.Request(BASE + "/api/ask/stream", method="POST",
                             data=json.dumps({"question": "보험료를 정부가 지원하나요?", "mode": mode}).encode(),
                             headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
t0, first, deltas, done, kind = time.time(), None, [], None, None
with urllib.request.urlopen(req, timeout=300) as r:
    for line in r:
        line = line.decode().strip()
        if line.startswith("event:"):
            kind = line[6:].strip()
        elif line.startswith("data:"):
            d = json.loads(line[5:])
            if kind == "delta":
                first = first or time.time() - t0
                deltas.append((round(time.time() - t0, 2), d["text"]))
            elif kind == "done":
                done = d
print(f"{BASE} · 모드 {mode}: 첫 글자 {first or 0:.1f}초, 조각 {len(deltas)}개, 전체 {time.time() - t0:.1f}초")
print("조각 도착 시각:", [t for t, _ in deltas][:12])
print(f"결과: {done.get('mode_label')} · verified={done.get('verified')} · {done.get('answer', '')[:80]}")
