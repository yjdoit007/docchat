r"""관리자 기능 점검 (서버를 켠 상태에서 실행)
    .venv\Scripts\python.exe admin_test.py <서버 콘솔 로그 파일>
여러 관리자 지정·해제, 마지막 관리자 보호, 권한 차단, 작업 기록, 서버 설정, 질문 기록을 확인한다.
주의: 시험용 가입자가 DB에 남는다. 실제 운영 전에 data\ 를 새로 만든다.
"""
import re, sys, time
from pathlib import Path

from smoke_test import ENV, call

LOG = Path(sys.argv[1])
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))


def last_code(email, kind):
    """서버 콘솔 로그에서 해당 이메일로 보낸 가장 최근 코드를 찾는다."""
    for _ in range(20):
        text = LOG.read_text(encoding="utf-8", errors="replace")
        found = re.findall(rf"받는 사람: {re.escape(email)}\n제목: [^\n]*\n{kind} 코드: (\d{{6}})", text)
        if found:
            return found[-1]
        time.sleep(0.5)
    return None

st, r = call("POST", "/api/auth/login", body={"email": ENV["ADMIN_EMAIL"], "password": ENV["ADMIN_PASSWORD"]})
admin = r["token"]

print("\n[1] 시험 사용자 준비 (가입 → 인증 → 승인)")
email, pw = f"admin-test{int(time.time())}@docchat.test", "Test1234abc"
call("POST", "/api/auth/register", body={"name": "부관리자시험", "email": email, "password": pw})
call("POST", "/api/auth/verify-email", body={"email": email, "code": last_code(email, "인증") or ""})
pend = call("GET", "/api/admin/users?status=pending", admin)[1]
uid = next(u["id"] for u in pend if u["email"] == email)
st, r = call("POST", f"/api/admin/members/{uid}/role", admin, {"role": "admin"})
check("승인 전(대기) 사용자는 관리자로 지정 불가", st == 404, r.get("detail", ""))
call("POST", f"/api/admin/users/{uid}/approve", admin)
user = call("POST", "/api/auth/login", body={"email": email, "password": pw})[1]["token"]

print("\n[2] 관리자 지정·해제")
st, members = call("GET", "/api/admin/members", admin)
admins = [m for m in members if m["role"] == "admin"]
check("사용자·관리자 목록 조회", st == 200 and any(m["email"] == email for m in members), f"관리자 {len(admins)}명")
if len(admins) == 1:
    st, r = call("POST", f"/api/admin/members/{admins[0]['id']}/role", admin, {"role": "viewer"})
    check("마지막 관리자 해제 거절", st == 409, r.get("detail", ""))
st, r = call("POST", f"/api/admin/members/{uid}/role", user, {"role": "admin"})
check("일반 사용자는 스스로 관리자 지정 불가", st == 403, r.get("detail", ""))
st, r = call("GET", "/api/admin/users", user)
check("지정 전: 관리자 기능 차단", st == 403)
st, r = call("POST", f"/api/admin/members/{uid}/role", admin, {"role": "admin"})
check("관리자로 지정", st == 200 and r.get("role") == "admin")
st, r = call("GET", "/api/admin/users", user)
check("지정 직후 같은 로그인으로 관리자 기능 사용 (다시 로그인 불필요)", st == 200)
m = next(m for m in call("GET", "/api/admin/members", admin)[1] if m["id"] == uid)
check("누가 지정했는지 기록", bool(m["granted_by"]) and bool(m["granted_at"]), f"{m['granted_by']} / {m['granted_at']}")
st, r = call("POST", f"/api/admin/members/{uid}/role", user, {"role": "viewer"})
check("관리자가 2명 이상이면 본인 해제 가능", st == 200 and r.get("role") == "viewer")
st, r = call("GET", "/api/admin/users", user)
check("해제 직후 관리자 기능 차단", st == 403)
st, r = call("POST", f"/api/admin/members/{uid}/role", admin, {"role": "boss"})
check("잘못된 역할 거절", st == 400)

print("\n[3] 작업 기록")
st, log = call("GET", "/api/admin/audit", admin)
acts = [(a["action"], a["target_email"]) for a in log if a["target_email"] == email]
check("승인·지정·해제가 작업 기록에 남음",
      {("approve", email), ("grant_admin", email), ("revoke_admin", email)} <= set(acts), str(acts))
st, r = call("GET", "/api/admin/audit", user)
check("일반 사용자는 작업 기록 조회 불가", st == 403)

print("\n[4] 서버 설정")
st, r = call("PUT", "/api/admin/settings", admin, {"public_url": "192.168.0.10"})
check("잘못된 주소 거절", st == 400, r.get("detail", ""))
st, r = call("PUT", "/api/admin/settings", admin, {"public_url": "https://docchat.example.com/"})
check("주소 저장", st == 200 and r.get("public_url") == "https://docchat.example.com")
st, r = call("GET", "/api/admin/settings", admin)
check("저장한 주소와 변경자 조회", r.get("public_url") == "https://docchat.example.com" and bool(r.get("updated_by")), str(r))
st, r = call("PUT", "/api/admin/settings", user, {"public_url": "https://evil.example"})
check("일반 사용자는 서버 설정 변경 불가", st == 403)

print("\n[5] 질문 기록")
st, r = call("GET", "/api/admin/logs?limit=5", admin)
check("질문 기록에 이메일·인용 확인 여부 포함", st == 200 and r and "email" in r[0] and "verified" in r[0],
      f"{len(r)}건, 최근: {r[0]['question'] if r else '-'}")

print(f"\n결과: {sum(results)}/{len(results)} 통과")
