r"""서버 동작 점검 스크립트 (서버를 켠 상태에서 실행)
    .venv\Scripts\python.exe smoke_test.py           # 업로드 + 질문 전체
    .venv\Scripts\python.exe smoke_test.py --ask-only  # 질문만
관리자 계정은 .env의 ADMIN_EMAIL / ADMIN_PASSWORD를 사용한다. 시험 문서는 ..\samples\*.pdf
"""
import json, os, re, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

from dotenv import dotenv_values

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8000")
HERE = Path(__file__).parent
SAMPLES = HERE.parent / "samples"
ENV = dotenv_values(HERE / ".env")
QUESTIONS = [
    ("풍수해보험의 보험목적물은 뭐예요?", None),
    ("정부가 보험료를 얼마나 지원하나요?", None),
    ("보험사업자는 누가 될 수 있어요?", None),
    ("자동차 사고 렌터카 비용도 보상되나요?", None),  # 문서에 없는 내용 → 거절해야 정상
    ("보험기간은 얼마예요?", None),
    ("손해평가인 자격이 정지되는 경우는?", None),
]


def call(method, path, token=None, body=None, files=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if files:
        b = uuid.uuid4().hex
        parts = []
        for k, v in (body or {}).items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (name, content) in files.items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                         f'Content-Type: application/pdf\r\n\r\n'.encode() + content + b"\r\n")
        data = b"".join(parts) + f"--{b}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def title_of(pdf):
    return re.sub(r"\(\d{8}\)$", "", pdf.stem)  # "풍수해ㆍ지진재해보험법(20260201)" → 문서 이름


def main():
    ask_only = "--ask-only" in sys.argv
    # 질문만 할 때는 일반 사용자로도 된다: 환경변수 SMOKE_EMAIL / SMOKE_PASSWORD 로 계정을 바꿀 수 있다
    email = os.environ.get("SMOKE_EMAIL") or ENV["ADMIN_EMAIL"]
    password = os.environ.get("SMOKE_PASSWORD") or ENV["ADMIN_PASSWORD"]
    st, r = call("POST", "/api/auth/login", body={"email": email, "password": password})
    if st != 200:
        sys.exit(f"[로그인 실패] {st} {r.get('detail')} — .env의 관리자 비밀번호를 바꿨다면 SMOKE_EMAIL/SMOKE_PASSWORD를 지정하세요")
    print(f"[로그인] {st} {r.get('user') or r}")
    token = r["token"]

    if not ask_only:
        pdfs = sorted(SAMPLES.glob("*.pdf"))
        for p in pdfs:
            st, r = call("POST", "/api/docs", token, {"title": title_of(p)}, {"file": (p.name, p.read_bytes())})
            print(f"[업로드] {st} {r}")
        p = pdfs[0]
        st, r = call("POST", "/api/docs", token, {"title": title_of(p)}, {"file": (p.name, p.read_bytes())})
        print(f"[같은 파일 재업로드 → 409 거절이 정상] {st} {r}")
        st, r = call("GET", "/api/docs", token)
        for d in r:
            print(f"[문서 목록] {d['title']}: " + ", ".join(f"v{v['version']}({v['page_count']}쪽)" for v in d["versions"]))

    for q, as_of in QUESTIONS:
        t = time.time()
        st, r = call("POST", "/api/ask", token, {"question": q, "as_of": as_of, "mode": os.environ.get("SMOKE_MODE") or None})
        print(f"\n[질문] {q}  ({st}, {time.time() - t:.1f}초, {r.get('mode_label', '')})" + (f" ※ {r['note']}" if r.get("note") else ""))
        print(f"  답변{'' if r.get('verified') else ' (확인된 인용 없음)'}: {r.get('answer')}")
        for s in r.get("sources", []):
            mark = "확인된 인용 " + str(len(s["quotes"])) + "개" if s["quotes"] else "확인된 인용 없음"
            print(f"  출처: {s['doc']} v{s['version']} {s.get('article', '')} p.{s['page']} — {mark}")
            for qt in s["quotes"]:
                print(f"    「{qt}」")
        for a in r.get("also", []):
            print(f"  관련: {a['doc']} v{a['version']} {a.get('article', '')} p.{a['page']}")


if __name__ == "__main__":
    main()
