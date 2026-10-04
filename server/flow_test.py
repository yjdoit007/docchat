r"""버전·날짜·가입 흐름 점검 (서버를 켠 상태에서 실행)
    .venv\Scripts\python.exe flow_test.py <서버 콘솔 로그 파일>
메일 설정(SMTP_USER)이 비어 있을 때 서버가 콘솔에 찍는 인증 코드를 로그 파일에서 읽는다.
주의: 시험용 가입자와 v2 문서가 DB에 남는다(이전 버전·기록은 지우지 않는 설계). 실제 운영 전에 data\ 를 새로 만든다.
"""
import io, re, sys, time
from datetime import date, timedelta
from pathlib import Path

import pymupdf

from smoke_test import ENV, SAMPLES, call, title_of

LOG = Path(sys.argv[1])
OK, FAIL = "✅", "❌"
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{OK if cond else FAIL} {name}" + (f" — {detail}" if detail else ""))


def last_code(email, kind):
    """서버 콘솔 로그에서 해당 이메일로 보낸 가장 최근 코드(kind: 인증/재설정)를 찾는다."""
    for _ in range(20):
        text = LOG.read_text(encoding="utf-8", errors="replace")
        found = re.findall(rf"받는 사람: {re.escape(email)}\n제목: [^\n]*\n{kind} 코드: (\d{{6}})", text)
        if found:
            return found[-1]
        time.sleep(0.5)
    return None


st, r = call("POST", "/api/auth/login", body={"email": ENV["ADMIN_EMAIL"], "password": ENV["ADMIN_PASSWORD"]})
admin = r["token"]

# ---------- 1. 버전(v2)과 날짜 기준 ----------
print("\n[1] 버전과 날짜 기준")
src = next(p for p in SAMPLES.glob("*.pdf") if "시행규칙" in p.name)
doc = pymupdf.open(src)
doc.set_metadata({**doc.metadata, "subject": f"v2 시험본 {time.time():.0f}"})  # 내용은 같고 파일만 다르게
buf = io.BytesIO(); doc.save(buf)
st, r = call("POST", "/api/docs", admin, {"title": title_of(src)}, {"file": ("v2-" + src.name, buf.getvalue())})
check("같은 이름·다른 파일 업로드 → 새 버전 생성", st == 200 and r.get("version", 0) >= 2, str(r))
new_ver = r.get("version")

st, docs = call("GET", "/api/docs", admin)
vers = next(d["versions"] for d in docs if d["title"] == title_of(src))
check("문서 목록에 이전 버전도 남아 있음", len(vers) >= 2 and vers[-1]["version"] == 1,
      ", ".join(f"v{v['version']}" for v in vers))

q = "손해평가인 보수교육은 언제 받아요?"
st, r = call("POST", "/api/ask", admin, {"question": q})
used = {(s["doc"], s["version"]) for s in r.get("sources", []) + r.get("also", [])}
rule_used = {v for d, v in used if d == title_of(src)}
check("기본 질문은 최신 버전을 근거로 사용", rule_used == {new_ver} if rule_used else False, f"시행규칙 근거 버전: {rule_used}")

yesterday = (date.today() - timedelta(days=1)).isoformat()
st, r = call("POST", "/api/ask", admin, {"question": q, "as_of": yesterday})
check(f"as_of={yesterday} (문서 등록 전 날짜) → 등록된 문서 없음 안내", "등록된 문서가 없" in r.get("answer", ""), r.get("answer"))

st, r = call("POST", "/api/ask", admin, {"question": q, "as_of": "2026/10/04"})
check("잘못된 날짜 형식 거절", st == 400, r.get("detail", ""))

# ---------- 2. 가입 흐름 ----------
print("\n[2] 가입 → 인증 → 승인 → 로그인 → 비밀번호 초기화")
email = f"tester{int(time.time())}@docchat.test"
pw1, pw2 = "Test1234abc", "NewPass5678x"

st, r = call("POST", "/api/auth/register", body={"name": "시험사용자", "email": email, "password": "short"})
check("약한 비밀번호 거절", st == 400, r.get("detail", ""))
st, r = call("POST", "/api/auth/register", body={"name": "시험사용자", "email": email, "password": pw1})
check("가입 요청", st == 200)
st, r = call("POST", "/api/auth/login", body={"email": email, "password": pw1})
check("인증 전 로그인 거절", st == 403, r.get("detail", ""))

st, r = call("POST", "/api/auth/verify-email", body={"email": email, "code": "000000"})
check("틀린 인증 코드 거절", st == 400, r.get("detail", ""))
code = last_code(email, "인증")
check("콘솔에서 인증 코드 확인", bool(code))
st, r = call("POST", "/api/auth/verify-email", body={"email": email, "code": code or ""})
check("이메일 인증 → 승인 대기", st == 200 and r.get("status") == "pending", str(r))
st, r = call("POST", "/api/auth/login", body={"email": email, "password": pw1})
check("승인 전 로그인 거절", st == 403, r.get("detail", ""))

st, pend = call("GET", "/api/admin/users?status=pending", admin)
me = next((u for u in pend if u["email"] == email), None)
check("관리자 승인 대기 목록에 표시", bool(me))
st, r = call("POST", f"/api/admin/users/{me['id']}/approve")
check("로그인 없이 승인 기능 사용 불가", st == 401, r.get("detail", ""))
st, r = call("POST", f"/api/admin/users/{me['id']}/approve", admin)
check("관리자 승인", st == 200 and r.get("status") == "approved")

st, r = call("POST", "/api/auth/login", body={"email": email, "password": pw1})
check("승인 후 로그인", st == 200 and r.get("user", {}).get("role") == "viewer", str(r.get("user")))
user_tok = r.get("token")
st, r = call("POST", "/api/ask", user_tok, {"question": "보험기간은 얼마예요?"})
check("일반 사용자 질문 가능", st == 200 and r.get("verified") is not None, f"verified={r.get('verified')}")
st, r = call("POST", "/api/docs", user_tok, {"title": "x"}, {"file": ("x.pdf", b"%PDF-1.4")})
check("일반 사용자 문서 업로드 불가", st == 403, r.get("detail", ""))
st, r = call("GET", "/api/admin/users", user_tok)
check("일반 사용자 관리자 기능(가입 목록) 사용 불가", st == 403, r.get("detail", ""))

st, r1 = call("POST", "/api/auth/forgot", body={"email": email})
st, r2 = call("POST", "/api/auth/forgot", body={"email": "nobody@docchat.test"})
check("비밀번호 찾기: 가입 여부와 상관없이 같은 응답", r1 == r2 == {"ok": True})
code = last_code(email, "재설정")
check("콘솔에서 재설정 코드 확인", bool(code))
st, r = call("POST", "/api/auth/reset", body={"email": email, "code": code or "", "new_password": pw2})
check("새 비밀번호로 변경", st == 200)
st, r = call("GET", "/api/me", user_tok)
check("변경 전 로그인(다른 기기) 자동 만료", st == 401, r.get("detail", ""))
st, r = call("POST", "/api/auth/login", body={"email": email, "password": pw1})
check("옛 비밀번호 로그인 거절", st == 401)
st, r = call("POST", "/api/auth/login", body={"email": email, "password": pw2})
check("새 비밀번호 로그인", st == 200)

print(f"\n결과: {sum(results)}/{len(results)} 통과")
