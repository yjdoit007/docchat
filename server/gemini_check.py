r"""Gemini 연결 확인: .env의 키로 쓸 수 있는 모델 목록과 지금 설정된 모델을 확인한다(키 값은 출력하지 않음).
    .venv\Scripts\python.exe gemini_check.py
"""
import json, time, urllib.error, urllib.request

import llm

key = llm.GEMINI_API_KEY
print(f"GEMINI_API_KEY: {'있음 (' + str(len(key)) + '자)' if key else '없음'} · GEMINI_MODEL: {llm.GEMINI_MODEL}")
if not key:
    raise SystemExit("server\\.env 의 GEMINI_API_KEY= 뒤에 키를 넣고 저장해 주세요.")

req = urllib.request.Request(f"{llm.GEMINI_BASE_URL}/models?pageSize=200", headers={"x-goog-api-key": key})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        models = json.loads(r.read()).get("models", [])
except urllib.error.HTTPError as e:
    raise SystemExit(f"모델 목록 조회 실패: HTTP {e.code} {e.read()[:300].decode('utf-8', 'replace')}")
names = [m["name"].removeprefix("models/") for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
flash = [n for n in names if "flash" in n]
print(f"답변에 쓸 수 있는 모델 {len(names)}개 중 Flash 계열: {', '.join(flash[:15])}")
for m in llm.GEMINI_MODELS:
    print(f"설정된 모델 {m}: {'목록에 있음' if m in names else '목록에 없음(건너뜀)'}")

t = time.time()
out = "".join(llm.stream("gemini", "한 문장으로 답하세요. JSON만 출력: {\"answer\": \"...\", \"citations\": []}",
                         "대한민국의 수도는?", strict=True))
print(f"짧은 시험 질문 응답 {time.time() - t:.1f}초: {out[:120]}")
