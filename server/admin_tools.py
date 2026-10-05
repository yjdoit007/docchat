r"""관리 스크립트 (서버를 끈 상태에서 써도 되고, 켠 상태에서 써도 된다)
    .venv\Scripts\python.exe admin_tools.py init                      DB·첫 관리자 만들기(.env의 ADMIN_EMAIL/ADMIN_PASSWORD)
    .venv\Scripts\python.exe admin_tools.py add-doc "문서 이름" 파일경로  문서 올리기(같은 이름이면 새 버전)
    .venv\Scripts\python.exe admin_tools.py set-url https://주소          서버 주소 설정
    .venv\Scripts\python.exe admin_tools.py set-modes gemini gemini,ollama  답변 모드: 기본, 허용 목록
    .venv\Scripts\python.exe admin_tools.py status                    현황 보기
"""
import os, sys
from pathlib import Path

os.chdir(Path(__file__).parent)  # .env·data 위치 기준
import server as S


def admin_id():
    with S.db() as c:
        r = c.execute("SELECT id FROM users WHERE role='admin' AND status='approved' ORDER BY id LIMIT 1").fetchone()
    if not r:
        sys.exit("관리자 계정이 없어요. 먼저 init 을 실행하세요(.env의 ADMIN_EMAIL/ADMIN_PASSWORD 필요).")
    return r["id"]


def set_setting(key, value):
    with S.db() as c:
        c.execute("REPLACE INTO settings(key,value,updated_by,updated_at) VALUES(?,?,?,?)", (key, value, admin_id(), S.now()))
        S.audit(c, admin_id(), "set_public_url" if key == "public_url" else "set_llm", None, f"{key}={value} (관리 스크립트)")


cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "status"), sys.argv[2:]
S.init()
if cmd == "init":
    with S.db() as c:
        print("준비 완료. 관리자:", [r["email"] for r in c.execute("SELECT email FROM users WHERE role='admin'")])
elif cmd == "add-doc":
    title, path = args[0], Path(args[1])
    try:
        r = S.add_document(title, path.name, path.read_bytes(), admin_id())
        print("올렸어요:", r)
    except S.HTTPException as e:
        print("올리지 못했어요:", e.detail)
elif cmd == "set-url":
    set_setting("public_url", args[0].rstrip("/"))
    print("서버 주소:", args[0])
elif cmd == "set-modes":
    default, allowed = args[0], args[1]
    set_setting("llm_allowed", allowed)
    set_setting("llm_default", default)
    print("답변 모드:", S.mode_settings())
else:
    with S.db() as c:
        print("사용자:", [tuple(r) for r in c.execute("SELECT email, role, status FROM users")])
        print("문서:", [tuple(r) for r in c.execute("SELECT d.title, v.version, v.kind, v.page_count FROM versions v JOIN documents d ON d.id=v.doc_id")])
        print("설정:", [tuple(r) for r in c.execute("SELECT key, value FROM settings")])
        print("질문 기록:", c.execute("SELECT COUNT(*) FROM logs").fetchone()[0])
