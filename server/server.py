"""문서 도우미 서버 (MVP)
- 가입(이메일 인증) → 관리자 승인 → 로그인, 비밀번호 초기화
- PDF 업로드, 날짜별·버전별 보관, 페이지 단위 검색
- 질문하면 근거 페이지와 원문 인용을 함께 답변
"""
import hashlib, hmac, json, math, os, re, secrets, smtplib, sqlite3, threading, time, urllib.error, urllib.request
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import jwt
import doctext  # PDF·한글 파일에서 글자 뽑기
import llm  # 답변 모델 연결(ollama·gemini·claude)
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()
KST = timezone(timedelta(hours=9))
DATA = Path(os.getenv("DATA_DIR", "data"))
FILES = DATA / "files"
DB_PATH = DATA / "docchat.db"
# 답변 모드 기본값(관리자 화면에서 바꿀 수 있음): ollama(로컬) / gemini / claude. 모델 연결은 llm.py
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAX_UPLOAD = 100 * 1024 * 1024

ph = PasswordHasher()
bearer = HTTPBearer(auto_error=False)
MAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PW_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")
PW_MSG = "비밀번호는 8자 이상, 영문과 숫자를 함께 써 주세요."

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, pw_hash TEXT,
  role TEXT DEFAULT 'viewer', status TEXT DEFAULT 'unverified',
  tv INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS codes(
  email TEXT, purpose TEXT, code_hash TEXT, expires REAL, attempts INTEGER DEFAULT 0,
  PRIMARY KEY(email, purpose));
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, title TEXT UNIQUE, created_at TEXT);
CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY, doc_id INTEGER, version INTEGER, filename TEXT,
  uploaded_at TEXT, uploaded_by INTEGER, sha256 TEXT, page_count INTEGER);
CREATE TABLE IF NOT EXISTS pages(version_id INTEGER, page_no INTEGER, text TEXT,
  PRIMARY KEY(version_id, page_no));
CREATE TABLE IF NOT EXISTS chunks(version_id INTEGER, idx INTEGER, label TEXT,
  page_from INTEGER, page_to INTEGER, text TEXT, PRIMARY KEY(version_id, idx));
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, actor_id INTEGER, action TEXT, target_id INTEGER, detail TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, updated_by INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS logs(
  id INTEGER PRIMARY KEY, user_id INTEGER, question TEXT, as_of TEXT,
  answer TEXT, sources TEXT, created_at TEXT);
"""


# ---------- 공통 ----------
@contextmanager
def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def now():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def err(code, msg):
    return HTTPException(code, msg)


def jwt_secret():
    s = os.getenv("JWT_SECRET")
    if s:
        return s
    f = DATA / "jwt.secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


def init():
    FILES.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript(SCHEMA)
        # 이전 DB 보강: 파일 형식(kind) 칸이 없으면 추가 (기존 버전은 모두 PDF)
        if "kind" not in {r["name"] for r in c.execute("PRAGMA table_info(versions)")}:
            c.execute("ALTER TABLE versions ADD COLUMN kind TEXT DEFAULT 'pdf'")
        # 질문 기록에 어떤 모델로 답했는지 칸 추가 (이전 기록은 비어 있음)
        if "provider" not in {r["name"] for r in c.execute("PRAGMA table_info(logs)")}:
            c.execute("ALTER TABLE logs ADD COLUMN provider TEXT")
        # 조문 조각이 없는 기존 버전은 저장된 페이지 텍스트로 조각을 만든다
        for (vid, kind) in c.execute("SELECT id, kind FROM versions WHERE id NOT IN "
                                     "(SELECT DISTINCT version_id FROM chunks)").fetchall():
            texts = [r["text"] for r in c.execute("SELECT text FROM pages WHERE version_id=? ORDER BY page_no", (vid,))]
            save_chunks(c, vid, texts, (kind or "pdf") == "pdf")
        if ADMIN_EMAIL and ADMIN_PASSWORD and not c.execute(
                "SELECT 1 FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone():
            c.execute("INSERT INTO users(name,email,pw_hash,role,status,created_at) VALUES(?,?,?,?,?,?)",
                      ("관리자", ADMIN_EMAIL, ph.hash(ADMIN_PASSWORD), "admin", "approved", now()))
            print("관리자 계정을 만들었어요:", ADMIN_EMAIL)
    if not ADMIN_EMAIL:
        print("주의: ADMIN_EMAIL / ADMIN_PASSWORD 가 설정되지 않아 관리자 계정이 없어요.")


# ---------- 무료 도메인(DuckDNS) 주소 자동 갱신 ----------
DUCKDNS_DOMAIN = os.getenv("DUCKDNS_DOMAIN", "").strip().removesuffix(".duckdns.org")
DUCKDNS_TOKEN = os.getenv("DUCKDNS_TOKEN", "").strip()
DDNS = {"domain": f"{DUCKDNS_DOMAIN}.duckdns.org" if DUCKDNS_DOMAIN else None, "last_at": None, "ok": None}


def ddns_loop(stop):
    """5분마다 DuckDNS에 '이 집의 지금 공인 IP'를 알린다(IP는 DuckDNS가 요청을 보고 알아낸다).
    가정용 공인 IP는 바뀔 수 있어서, 바뀌어도 도메인 주소로 계속 접속되게 한다."""
    url = f"https://www.duckdns.org/update?domains={DUCKDNS_DOMAIN}&token={DUCKDNS_TOKEN}&ip="
    while not stop.is_set():
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                ok = r.read().decode().strip() == "OK"
        except Exception:
            ok = False
        if ok != DDNS["ok"]:  # 상태가 바뀔 때만 기록 (토큰은 출력하지 않음)
            print(f"[DuckDNS] {DDNS['domain']} 주소 갱신 {'성공' if ok else '실패 — 도메인 이름과 토큰을 확인해 주세요'}", flush=True)
        DDNS.update(last_at=now(), ok=ok)
        stop.wait(300)


@asynccontextmanager
async def lifespan(app):
    init()
    stop = threading.Event()
    if DUCKDNS_DOMAIN and DUCKDNS_TOKEN:
        threading.Thread(target=ddns_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


# API 시험 화면(/docs)은 외부에 공개할 때 끈다 (.env의 API_DOCS=0)
_api_docs = os.getenv("API_DOCS", "1").strip() != "0"
app = FastAPI(title="문서 도우미", lifespan=lifespan,
              docs_url="/docs" if _api_docs else None, redoc_url=None,
              openapi_url="/openapi.json" if _api_docs else None)


# ---------- 메일 / 인증 코드 ----------
def send_mail(to, subject, body):
    if not SMTP_USER:  # 개발용: 메일 설정이 없으면 콘솔에 출력
        print(f"\n[개발용 메일] 받는 사람: {to}\n제목: {subject}\n{body}\n", flush=True)
        return
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = SMTP_USER, to, subject
    m.set_content(body)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(m)
    except Exception as e:
        print("메일 발송 실패:", e)
        raise err(502, "메일을 보내지 못했어요. 잠시 후 다시 시도해 주세요.")


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def issue_code(c, email, purpose):
    code = f"{secrets.randbelow(10**6):06d}"
    c.execute("REPLACE INTO codes(email,purpose,code_hash,expires,attempts) VALUES(?,?,?,?,0)",
              (email, purpose, h(code), time.time() + 600))
    return code


def check_code(c, email, purpose, code):
    r = c.execute("SELECT * FROM codes WHERE email=? AND purpose=?", (email, purpose)).fetchone()
    if not r or r["expires"] < time.time() or r["attempts"] >= 5:
        return False
    if hmac.compare_digest(r["code_hash"], h(code.strip())):
        c.execute("DELETE FROM codes WHERE email=? AND purpose=?", (email, purpose))
        return True
    c.execute("UPDATE codes SET attempts=attempts+1 WHERE email=? AND purpose=?", (email, purpose))
    return False


FAILS = defaultdict(list)


def throttle(key, record=False, limit=5, window=600):
    t = time.time()
    FAILS[key] = [x for x in FAILS[key] if t - x < window]
    if record:
        FAILS[key].append(t)
    elif len(FAILS[key]) >= limit:
        raise err(429, "시도가 너무 많아요. 10분 뒤에 다시 해 주세요.")


# ---------- 로그인 처리 ----------
class Reg(BaseModel):
    name: str
    email: str
    password: str


class EmailOnly(BaseModel):
    email: str


class Verify(BaseModel):
    email: str
    code: str


class Login(BaseModel):
    email: str
    password: str


class Reset(BaseModel):
    email: str
    code: str
    new_password: str


class Ask(BaseModel):
    question: str
    as_of: str | None = None
    mode: str | None = None  # 답변 모드(ollama/gemini/claude). 비우면 관리자가 정한 기본값


@app.post("/api/auth/register")
def register(b: Reg):
    email, name = b.email.strip().lower(), b.name.strip()
    if not name:
        raise err(400, "이름을 입력해 주세요.")
    if not MAIL_RE.match(email):
        raise err(400, "이메일 형식을 확인해 주세요.")
    if not PW_RE.match(b.password):
        raise err(400, PW_MSG)
    with db() as c:
        u = c.execute("SELECT status FROM users WHERE email=?", (email,)).fetchone()
        if u and u["status"] != "unverified":
            raise err(409, "이미 가입된 이메일이에요. 로그인하거나 비밀번호 찾기를 이용해 주세요.")
        if u:
            c.execute("UPDATE users SET name=?, pw_hash=? WHERE email=?", (name, ph.hash(b.password), email))
        else:
            c.execute("INSERT INTO users(name,email,pw_hash,created_at) VALUES(?,?,?,?)",
                      (name, email, ph.hash(b.password), now()))
        code = issue_code(c, email, "verify")
    send_mail(email, "[문서 도우미] 이메일 인증 코드", f"인증 코드: {code}\n10분 안에 입력해 주세요.")
    return {"ok": True}


@app.post("/api/auth/resend")
def resend(b: EmailOnly):
    email = b.email.strip().lower()
    code = None
    with db() as c:
        u = c.execute("SELECT status FROM users WHERE email=?", (email,)).fetchone()
        if u and u["status"] == "unverified":
            code = issue_code(c, email, "verify")
    if code:
        send_mail(email, "[문서 도우미] 이메일 인증 코드", f"인증 코드: {code}\n10분 안에 입력해 주세요.")
    return {"ok": True}


@app.post("/api/auth/verify-email")
def verify_email(b: Verify):
    email = b.email.strip().lower()
    throttle("v:" + email)
    with db() as c:
        ok = check_code(c, email, "verify", b.code)
        u = None
        if ok:
            c.execute("UPDATE users SET status='pending' WHERE email=? AND status='unverified'", (email,))
            u = c.execute("SELECT name FROM users WHERE email=?", (email,)).fetchone()
    if not ok:
        throttle("v:" + email, record=True)
        raise err(400, "코드가 맞지 않거나 만료됐어요. 코드를 다시 받아 주세요.")
    if ADMIN_EMAIL and u:
        try:
            send_mail(ADMIN_EMAIL, "[문서 도우미] 새 가입 요청",
                      f"{u['name']} ({email}) 님이 이메일 인증을 마치고 승인을 기다리고 있어요.")
        except HTTPException:
            pass
    return {"ok": True, "status": "pending"}


@app.post("/api/auth/login")
def login(b: Login):
    email = b.email.strip().lower()
    throttle("l:" + email)
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    good = False
    if u:
        try:
            ph.verify(u["pw_hash"], b.password)
            good = True
        except VerifyMismatchError:
            pass
    if not good:
        throttle("l:" + email, record=True)
        raise err(401, "이메일 또는 비밀번호가 맞지 않아요.")
    msgs = {"unverified": "이메일 인증을 먼저 끝내 주세요.",
            "pending": "관리자 승인을 기다리는 중이에요.",
            "rejected": "가입이 승인되지 않았어요. 관리자에게 문의해 주세요."}
    if u["status"] != "approved":
        raise err(403, msgs.get(u["status"], "로그인할 수 없어요."))
    token = jwt.encode({"sub": str(u["id"]), "tv": u["tv"], "exp": int(time.time()) + 12 * 3600},
                       jwt_secret(), algorithm="HS256")
    return {"token": token, "user": {"name": u["name"], "email": u["email"], "role": u["role"]}}


@app.post("/api/auth/forgot")
def forgot(b: EmailOnly):
    email = b.email.strip().lower()
    code = None
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            code = issue_code(c, email, "reset")
    if code:
        send_mail(email, "[문서 도우미] 비밀번호 재설정 코드", f"재설정 코드: {code}\n10분 안에 입력해 주세요.")
    return {"ok": True}  # 가입 여부와 상관없이 같은 응답


@app.post("/api/auth/reset")
def reset(b: Reset):
    email = b.email.strip().lower()
    throttle("r:" + email)
    if not PW_RE.match(b.new_password):
        raise err(400, PW_MSG)
    with db() as c:
        ok = check_code(c, email, "reset", b.code)
        if ok:  # tv를 올려서 기존 로그인(다른 기기)을 모두 무효화
            c.execute("UPDATE users SET pw_hash=?, tv=tv+1 WHERE email=?", (ph.hash(b.new_password), email))
    if not ok:
        throttle("r:" + email, record=True)
        raise err(400, "코드가 맞지 않거나 만료됐어요. 코드를 다시 받아 주세요.")
    return {"ok": True}


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not cred:
        raise err(401, "로그인이 필요해요.")
    try:
        p = jwt.decode(cred.credentials, jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise err(401, "로그인이 만료됐어요. 다시 로그인해 주세요.")
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (int(p["sub"]),)).fetchone()
    if not u or u["status"] != "approved" or u["tv"] != p["tv"]:
        raise err(401, "로그인이 만료됐어요. 다시 로그인해 주세요.")
    return u


def require_admin(u=Depends(current_user)):
    if u["role"] != "admin":
        raise err(403, "관리자만 할 수 있어요.")
    return u


@app.get("/api/me")
def me(u=Depends(current_user)):
    return {"name": u["name"], "email": u["email"], "role": u["role"]}


def audit(c, actor_id, action, target_id=None, detail=""):
    """관리 작업 기록(승인·거절·관리자 지정/해제·설정 변경). 지우지 않는다."""
    c.execute("INSERT INTO audit(actor_id,action,target_id,detail,created_at) VALUES(?,?,?,?,?)",
              (actor_id, action, target_id, detail, now()))


# ---------- 관리자: 가입 승인 ----------
@app.get("/api/admin/users")
def admin_users(status: str = "pending", admin=Depends(require_admin)):
    with db() as c:
        rows = c.execute("SELECT id,name,email,status,created_at FROM users "
                         "WHERE role!='admin' AND status=? ORDER BY id DESC", (status,)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/admin/users/{uid}/{action}")
def decide(uid: int, action: str, admin=Depends(require_admin)):
    if action not in ("approve", "reject"):
        raise err(404, "알 수 없는 요청이에요.")
    new = "approved" if action == "approve" else "rejected"
    with db() as c:
        r = c.execute("UPDATE users SET status=?, tv=tv+1 WHERE id=? AND role!='admin' "
                      "AND status IN ('pending','approved','rejected')", (new, uid))
        u = c.execute("SELECT name,email FROM users WHERE id=?", (uid,)).fetchone()
        if r.rowcount:
            audit(c, admin["id"], action, uid)
    if r.rowcount == 0 or not u:
        raise err(404, "대상을 찾을 수 없어요.")
    if new == "approved":
        try:
            send_mail(u["email"], "[문서 도우미] 승인되었어요", f"{u['name']}님, 이제 로그인해서 사용할 수 있어요.")
        except HTTPException:
            pass
    return {"ok": True, "status": new}


# ---------- 문서 / 버전 ----------
# ---------- 관리자: 사용자·관리자 지정 ----------
class RoleBody(BaseModel):
    role: str


@app.get("/api/admin/members")
def members(admin=Depends(require_admin)):
    """승인된 사용자 전체(관리자 포함)와, 관리자라면 마지막으로 누가 지정했는지."""
    with db() as c:
        rows = c.execute("SELECT id,name,email,role,created_at FROM users WHERE status='approved' "
                         "ORDER BY role='admin' DESC, name").fetchall()
        out = []
        for r in rows:
            g = c.execute("SELECT u.name, a.created_at FROM audit a LEFT JOIN users u ON u.id=a.actor_id "
                          "WHERE a.action='grant_admin' AND a.target_id=? ORDER BY a.id DESC LIMIT 1", (r["id"],)).fetchone()
            out.append(dict(r) | {"granted_by": g["name"] if g else None, "granted_at": g["created_at"] if g else None})
    return out


@app.post("/api/admin/members/{uid}/role")
def set_role(uid: int, b: RoleBody, admin=Depends(require_admin)):
    if b.role not in ("admin", "viewer"):
        raise err(400, "역할은 admin 또는 viewer 만 쓸 수 있어요.")
    with db() as c:
        u = c.execute("SELECT id,name,role,status FROM users WHERE id=?", (uid,)).fetchone()
        if not u or u["status"] != "approved":
            raise err(404, "승인된 사용자만 관리자로 지정하거나 해제할 수 있어요.")
        if u["role"] == b.role:
            return {"ok": True, "role": b.role}
        if b.role == "viewer":
            n = c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='approved'").fetchone()[0]
            if n <= 1:
                raise err(409, "마지막 관리자는 해제할 수 없어요. 다른 사람을 먼저 관리자로 지정해 주세요.")
        c.execute("UPDATE users SET role=? WHERE id=?", (b.role, uid))
        audit(c, admin["id"], "grant_admin" if b.role == "admin" else "revoke_admin", uid)
    return {"ok": True, "role": b.role}


@app.get("/api/admin/audit")
def audit_log(limit: int = 100, admin=Depends(require_admin)):
    with db() as c:
        rows = c.execute("SELECT a.id, a.action, a.detail, a.created_at, x.name AS actor, x.email AS actor_email, "
                         "t.name AS target, t.email AS target_email FROM audit a "
                         "LEFT JOIN users x ON x.id=a.actor_id LEFT JOIN users t ON t.id=a.target_id "
                         "ORDER BY a.id DESC LIMIT ?", (min(limit, 500),)).fetchall()
    return [dict(r) for r in rows]


# ---------- 관리자: 서버 설정 ----------
class SettingsBody(BaseModel):
    public_url: str


@app.get("/api/admin/settings")
def get_settings(admin=Depends(require_admin)):
    with db() as c:
        r = c.execute("SELECT s.value, s.updated_at, u.name FROM settings s LEFT JOIN users u ON u.id=s.updated_by "
                      "WHERE s.key='public_url'").fetchone()
    return {"public_url": r["value"] if r else "", "updated_at": r["updated_at"] if r else None,
            "updated_by": r["name"] if r else None,
            "ddns": {"domain": DDNS["domain"], "last_at": DDNS["last_at"], "ok": DDNS["ok"]},
            "llm": mode_settings() | {"labels": llm.LABELS, "notes": llm.NOTES}}


@app.put("/api/admin/settings")
def put_settings(b: SettingsBody, admin=Depends(require_admin)):
    url = b.public_url.strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
        raise err(400, "http:// 또는 https:// 로 시작하는 주소를 입력해 주세요.")
    with db() as c:
        c.execute("REPLACE INTO settings(key,value,updated_by,updated_at) VALUES('public_url',?,?,?)",
                  (url, admin["id"], now()))
        audit(c, admin["id"], "set_public_url", None, url)
    return {"ok": True, "public_url": url}


@app.post("/api/docs")
def upload(title: str = Form(...), file: UploadFile = File(...), admin=Depends(require_admin)):
    title = title.strip()
    if not title:
        raise err(400, "문서 이름을 입력해 주세요.")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise err(413, "파일이 너무 커요. 100MB 이하로 올려 주세요.")
    return add_document(title, file.filename, data, admin["id"])


def add_document(title, filename, data, admin_id):
    """문서 한 개를 새 문서 또는 새 버전으로 저장한다(화면 업로드와 관리 스크립트가 함께 쓴다)."""
    try:
        kind, texts = doctext.extract(data)
    except doctext.DocError as e:
        raise err(400, str(e))
    paged = kind == "pdf"  # 한글 문서는 고정된 페이지가 없어 페이지 번호를 쓰지 않는다
    sha = hashlib.sha256(data).hexdigest()
    with db() as c:
        d = c.execute("SELECT id FROM documents WHERE title=?", (title,)).fetchone()
        if d:
            doc_id = d["id"]
            last = c.execute("SELECT version, sha256 FROM versions WHERE doc_id=? ORDER BY version DESC LIMIT 1",
                             (doc_id,)).fetchone()
            if last and last["sha256"] == sha:
                raise err(409, f"같은 파일이 이미 최신 버전(v{last['version']})으로 있어요.")
            ver = (last["version"] if last else 0) + 1
        else:
            doc_id = c.execute("INSERT INTO documents(title,created_at) VALUES(?,?)", (title, now())).lastrowid
            ver = 1
        vid = c.execute("INSERT INTO versions(doc_id,version,filename,uploaded_at,uploaded_by,sha256,page_count,kind) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (doc_id, ver, filename, now(), admin_id, sha, len(texts) if paged else None, kind)).lastrowid
        c.executemany("INSERT INTO pages VALUES(?,?,?)", [(vid, i + 1, t) for i, t in enumerate(texts)])
        n_chunks = save_chunks(c, vid, texts, paged)
    (FILES / str(doc_id)).mkdir(parents=True, exist_ok=True)
    stored_file(doc_id, ver, kind).write_bytes(data)
    return {"document": title, "version": ver, "version_id": vid, "kind": kind,
            "pages": len(texts) if paged else None, "chunks": n_chunks}


def stored_file(doc_id, ver, kind):
    return FILES / str(doc_id) / f"v{ver}.{kind or 'pdf'}"


@app.get("/api/docs")
def list_docs(u=Depends(current_user)):
    with db() as c:
        docs = c.execute("SELECT id,title FROM documents ORDER BY title").fetchall()
        out = []
        for d in docs:
            vs = c.execute("SELECT id,version,uploaded_at,page_count,kind FROM versions WHERE doc_id=? "
                           "ORDER BY version DESC", (d["id"],)).fetchall()
            out.append({"id": d["id"], "title": d["title"], "versions": [dict(v) for v in vs]})
    return out


@app.get("/api/versions/{vid}/pages/{n}")
def get_page(vid: int, n: int, u=Depends(current_user)):
    with db() as c:
        r = c.execute("SELECT p.text, p.page_no, v.version, v.uploaded_at, v.page_count, d.title "
                      "FROM pages p JOIN versions v ON v.id=p.version_id JOIN documents d ON d.id=v.doc_id "
                      "WHERE p.version_id=? AND p.page_no=?", (vid, n)).fetchone()
    if not r:
        raise err(404, "해당 페이지를 찾을 수 없어요.")
    return dict(r)


@app.get("/api/versions/{vid}/article")
def get_article(vid: int, label: str, u=Depends(current_user)):
    """출처 펼쳐 보기용 조문 원문. PDF 줄바꿈으로 끊긴 낱말만 잇고 나머지(개정 표시 포함)는 원문 그대로."""
    with db() as c:
        r = c.execute("SELECT ch.label, ch.page_from, ch.page_to, ch.text, v.version, v.uploaded_at, v.kind, d.title "
                      "FROM chunks ch JOIN versions v ON v.id=ch.version_id JOIN documents d ON d.id=v.doc_id "
                      "WHERE ch.version_id=? AND ch.label=?", (vid, label)).fetchone()
    if not r:
        raise err(404, "해당 조문을 찾을 수 없어요.")
    out = dict(r)
    out["text"] = join_wrapped(r["text"]) if r["kind"] == "pdf" else r["text"]
    return out


def join_wrapped(text):
    """PDF에서 글자 수 때문에 줄이 바뀌며 끊긴 낱말을 잇는다(한글 문서는 줄바꿈이 문단 경계라 쓰지 않음)."""
    return re.sub(r"(?<=[가-힣])[ \t]*\n[ \t]*(?=[가-힣])", "", text)


@app.get("/api/versions/{vid}/file")
def get_file(vid: int, u=Depends(current_user)):
    with db() as c:
        r = c.execute("SELECT doc_id, version, filename, kind FROM versions WHERE id=?", (vid,)).fetchone()
    p = r and stored_file(r["doc_id"], r["version"], r["kind"])
    if not r or not p.exists():
        raise err(404, "원본 파일을 찾을 수 없어요.")
    return FileResponse(p, media_type=doctext.KINDS[r["kind"] or "pdf"][0], filename=r["filename"])


# ---------- 조문 단위 조각 ----------
ART_RE = re.compile(r"^제(\d+)조(?:의(\d+))?\s*\(([^)]{1,40})\)")
BUCHIK_RE = re.compile(r"^부\s*칙\s*(<|$)")


def strip_running_lines(texts):
    """모든 페이지 위·아래에 반복되는 머리글/바닥글(예: '법제처 1 국가법령정보센터')을 지운다."""
    if len(texts) < 2:
        return texts
    key = lambda s: re.sub(r"[\d\s]+", "", s)
    pages = [t.splitlines() for t in texts]
    edge = Counter(k for ls in pages for k in {key(l) for l in ls[:3] + ls[-2:] if key(l)})
    rep = {k for k, n in edge.items() if n >= max(2, len(pages) * 0.6)}
    return ["\n".join(l for i, l in enumerate(ls) if not ((i < 3 or i >= len(ls) - 2) and key(l) in rep))
            for ls in pages]


def make_chunks(texts, paged=True):
    """페이지 텍스트를 조문(제N조) 단위로 나눈다. 조문이 없는 문서는 PDF면 페이지 단위,
    한글 문서(paged=False, 페이지 번호 없음)면 문단을 약 800자씩 묶은 '부분 N' 단위로 둔다.
    반환: [(label, page_from, page_to, text)]"""
    if not paged:
        out = make_chunks(texts, True)
        if out and not out[0][0].startswith("p."):  # 조문 구조: 페이지 번호만 지운다
            return [(lb, None, None, t) for lb, pf, pt, t in out]
        parts, cur = [], []
        for para in "\n".join(texts).splitlines():
            cur.append(para)
            if sum(len(x) for x in cur) >= 800:
                parts.append("\n".join(cur)); cur = []
        if cur:
            parts.append("\n".join(cur))
        return [(f"부분 {i}", None, None, t.strip()) for i, t in enumerate(parts, 1) if t.strip()]
    pages = strip_running_lines(texts)
    chunks, cur, prefix, last = [], None, "", -1
    for pno, page in enumerate(pages, 1):
        for line in page.splitlines():
            s = line.strip()
            m = ART_RE.match(s)
            num = m and int(m.group(1)) * 1000 + int(m.group(2) or 0)
            if BUCHIK_RE.match(s):
                prefix, last = "부칙 ", -1
                cur = ["부칙", pno, pno, []]
                chunks.append(cur)
            elif m and num > last:  # 본문 속 '제3조(...)' 인용과 구분하려고 번호가 늘어날 때만 새 조문
                last = num
                label = f"{prefix}제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "") + f"({m.group(3)})"
                cur = [label, pno, pno, []]
                chunks.append(cur)
            elif cur is None:
                cur = ["머리말", pno, pno, []]
                chunks.append(cur)
            cur[2] = pno
            cur[3].append(line)
    out = [(lb, pf, pt, "\n".join(ls).strip()) for lb, pf, pt, ls in chunks]
    out = [x for x in out if x[3]]
    if sum(1 for x in out if ART_RE.match(x[0].removeprefix("부칙 "))) < 2:  # 조문 구조가 아니면 페이지 단위
        return [(f"p.{i}", i, i, t.strip()) for i, t in enumerate(pages, 1) if t.strip()]
    return out


def save_chunks(c, vid, texts, paged=True):
    rows = make_chunks(texts, paged)
    c.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?)",
                  [(vid, i, lb, pf, pt, t) for i, (lb, pf, pt, t) in enumerate(rows)])
    return len(rows)


# ---------- 검색 (조문 단위, 2글자 조각 기반 BM25) ----------
def grams(text):
    out = []
    for t in re.findall(r"[0-9a-z가-힣]+", text.lower()):
        if len(t) == 1:
            if t.isdigit():
                out.append(t)
        else:
            out += [t[i:i + 2] for i in range(len(t) - 1)]
    return out


_IDX = {}


def chunk_index(c, vid):
    if vid not in _IDX:
        rows = c.execute("SELECT label, page_from, page_to, text FROM chunks WHERE version_id=? ORDER BY idx",
                         (vid,)).fetchall()
        _IDX[vid] = [(dict(r), Counter(grams(r["label"] + " " + r["text"])), set(grams(r["label"]))) for r in rows]
    return _IDX[vid]


def pick_versions(c, as_of):
    cutoff = f"{as_of} 23:59:59" if as_of else None
    rows = c.execute("SELECT v.*, d.title FROM versions v JOIN documents d ON d.id=v.doc_id "
                     "ORDER BY v.doc_id, v.version DESC").fetchall()
    chosen = {}
    for r in rows:
        if r["doc_id"] in chosen or (cutoff and r["uploaded_at"] > cutoff):
            continue
        chosen[r["doc_id"]] = r
    return list(chosen.values())


def search(c, vers, q, k=6):
    """관련도 높은 조각 k개: [(점수, 버전, 조각)] — 조각은 label/page_from/page_to/text"""
    items = [(v, ch, cnt, lab) for v in vers for ch, cnt, lab in chunk_index(c, v["id"])]
    qg = set(grams(q))
    if not items or not qg:
        return []
    N = len(items)
    lens = [sum(x[2].values()) for x in items]
    avg = (sum(lens) / N) or 1
    df = {g: sum(1 for x in items if g in x[2]) for g in qg}
    out = []
    for (v, ch, cnt, lab), L in zip(items, lens):
        s = 0.0
        for g in qg:
            tf = cnt.get(g, 0)
            if tf:
                idf = math.log(1 + (N - df[g] + .5) / (df[g] + .5))
                s += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * L / avg))
                if g in lab:  # 조문 제목(예: '제6조(보험사업자)')에 질문 단어가 있으면 가산
                    s += idf * 1.5
        if s > 0:
            out.append((s, v, ch))
    out.sort(key=lambda x: -x[0])
    return out[:k]


# ---------- 질의응답 ----------
SYSTEM = """당신은 사내 문서 도우미입니다. 아래 <source> 안의 내용만 근거로 한국어(해요체)로 친절하게 답하세요.
규칙:
- 소스에 없는 내용은 추측하지 마세요. 근거가 부족하면 answer에 "제공된 문서에서 근거를 찾지 못했어요."라고 쓰고 citations는 비워 두세요.
- answer는 핵심만 2~3문장으로 짧게 쓰세요. 인용문이나 소스 번호는 answer에 넣지 마세요.
- citations에는 근거가 된 소스 id와, 소스 본문의 문장을 한 글자도 바꾸지 말고 80자 이내로 복사해 넣으세요(1~2개).
- 법적 판단이 필요한 내용은 문서에 적힌 대로만 전달하고, 단정하기 어렵다면 그렇게 말하세요.
- 반드시 JSON만 출력하세요: {"answer": "...", "citations": [{"id": 1, "quote": "..."}]}"""
NOT_FOUND = "제공된 문서에서 근거를 찾지 못했어요. 질문을 조금 다르게 써 보시거나 문서가 등록되어 있는지 확인해 주세요."


def parse_json(t):
    t = re.sub(r"^```(?:json)?|```$", "", t.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    for cand in (t, m.group(0) if m else None):
        if cand:
            try:
                return json.loads(cand)
            except ValueError:
                pass
    return None


def norm(s):
    return re.sub(r"\s+", "", s)


# 개정 표시: <개정 2024. 2. 13.>, <신설 ...>, [전문개정 ...], [제목개정 ...], [본조신설 ...] 등
def _loose(*words):  # PDF 줄바꿈이 낱말 한가운데 끼어도(예: '<개\n정 …>') 알아보도록 글자 사이 공백 허용
    return "|".join(r"\s*".join(map(re.escape, w)) for w in words)


NOTE_RE = re.compile(rf"<\s*(?:{_loose('개정', '신설', '삭제', '타법개정')})[^>]*>"
                     rf"|\[\s*(?:{_loose('전문개정', '제목개정', '본조신설')}|종전[^\]]*|[^\]]*이동)[^\]]*\]")
# 인용 대조 때 무시하는 문자(공백·따옴표·괄호 모양)와 같은 글자로 보는 문자(가운뎃점)
_SKIP = set(" \t\r\n 「」『』«»<>“”\"'‘’")
_FOLD = {"·": "ㆍ", "・": "ㆍ", "∙": "ㆍ", "‧": "ㆍ"}


def _fold(text):
    """대조용 글자열과, 각 글자가 원문 어디에서 왔는지(index)를 함께 돌려준다."""
    skip = [False] * len(text)
    for m in NOTE_RE.finditer(text):
        skip[m.start():m.end()] = [True] * (m.end() - m.start())
    out, pos = [], []
    for i, ch in enumerate(text):
        if skip[i] or ch in _SKIP:
            continue
        out.append(_FOLD.get(ch, ch))
        pos.append(i)
    return "".join(out), pos


def find_quote(quote, text, paged=True):
    """모델이 적은 인용을 원문에서 찾아 **원문 그대로의 구간**을 돌려준다. 없으면 None.
    띄어쓰기·따옴표/괄호 모양·가운뎃점·개정 표시 차이만 허용한다."""
    q, _ = _fold(quote)
    if len(q) < 8:  # 너무 짧은 인용은 증빙으로 쓰지 않음
        return None
    t, pos = _fold(text)
    i = t.find(q)
    if i < 0:
        return None
    span = text[pos[i]:pos[i + len(q) - 1] + 1]
    if paged:
        span = join_wrapped(span)
    return re.sub(r"\s+", " ", span).strip()


def is_paged(version_row):
    return (version_row["kind"] or "pdf") == "pdf"


def for_model(text, paged=True):
    """모델에게 보낼 원문 정리: 개정 표시를 빼고, PDF에서 글자 수 때문에 끊긴 줄을 잇는다(한글 문서는 잇지 않음)."""
    text = NOTE_RE.sub("", text)
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out = ""
    for prev, cur in zip([""] + lines, lines):
        starts_item = re.match(r"^\s*(제\d+조|[①-⑳]|\d+(의\d+)?\.|[가-하]\.|\[|부\s*칙)", cur)
        if paged and out and len(prev) >= 40 and not starts_item:
            out += cur.strip()  # 줄 길이가 꽉 찬 상태로 끊겼다면 이어 붙인다
        else:
            out += ("\n" if out else "") + cur.strip()
    return out


# ---------- 답변 모드 (관리자가 허용·기본값을 정하고, 사용자가 질문마다 고른다) ----------
def mode_settings():
    """{'default': 'gemini', 'allowed': ['gemini','ollama'], 'available': {...}}
    allowed 기본값은 .env에 키가 있는(쓸 수 있는) 모델 전부. 쓸 수 없는 모델은 허용돼 있어도 빠진다."""
    avail = llm.available()
    with db() as c:
        rows = dict(c.execute("SELECT key, value FROM settings WHERE key IN ('llm_default','llm_allowed')").fetchall())
    allowed = [m for m in (rows.get("llm_allowed") or ",".join(m for m, ok in avail.items() if ok)).split(",")
               if m in avail and avail[m]] or ["ollama"]
    default = rows.get("llm_default") or LLM_PROVIDER
    if default not in allowed:
        default = allowed[0]
    return {"default": default, "allowed": allowed, "available": avail}


def resolve_mode(asked):
    s = mode_settings()
    if asked and asked not in s["allowed"]:
        raise err(400, "지금은 쓸 수 없는 답변 모드예요. 화면을 새로 고쳐 주세요.")
    return asked or s["default"]


@app.get("/api/modes")
def modes(u=Depends(current_user)):
    s = mode_settings()
    return {"default": s["default"],
            "modes": [{"id": m, "label": llm.LABELS[m], "note": llm.NOTES[m]} for m in s["allowed"]]}


class ModeBody(BaseModel):
    default: str
    allowed: list[str]


@app.put("/api/admin/llm")
def put_modes(b: ModeBody, admin=Depends(require_admin)):
    avail = llm.available()
    allowed = [m for m in dict.fromkeys(b.allowed) if avail.get(m)]
    if not allowed:
        raise err(400, "답변 모드를 하나 이상 허용해 주세요. (Gemini·Claude는 .env에 키가 있어야 해요)")
    if b.default not in allowed:
        raise err(400, "기본 모드는 허용한 모드 중에서 골라 주세요.")
    with db() as c:
        for k, v in (("llm_allowed", ",".join(allowed)), ("llm_default", b.default)):
            c.execute("REPLACE INTO settings(key,value,updated_by,updated_at) VALUES(?,?,?,?)", (k, v, admin["id"], now()))
        audit(c, admin["id"], "set_llm", None, f"기본 {b.default} · 허용 {', '.join(allowed)}")
    return mode_settings()


# ---------- 질문 처리: 검색 → 모델(스트리밍) → 인용 검증 → 기록 ----------
def build_prompt(hits, chars, q):
    ctx = "\n".join(f'<source id="{i}" doc="{v["title"]}" version="v{v["version"]}" article="{ch["label"]}">\n'
                    f'{for_model(ch["text"], is_paged(v))[:chars]}\n</source>' for i, (s, v, ch) in enumerate(hits, 1))
    return f"{ctx}\n\n질문: {q}"


def attempts_for(mode):
    """실패하면 차례로 시도할 (모델, 형식강제) 목록. 외부 모델이 한도·오류로 실패하면 로컬 모델로 답한다."""
    plan = [(mode, True)]
    if mode == "ollama":
        plan.append(("ollama", False))  # 로컬 모델은 가끔 형식 강제 상태에서 멈춘다 → 형식 없이 한 번 더
    else:
        plan += [("ollama", True), ("ollama", False)]
    return plan


def answer_events(q, as_of, mode, u):
    """질문 하나를 처리하며 (사건, 내용)을 차례로 내준다: delta(답 글자) · reset(다른 모델로 다시) · done · error"""
    with db() as c:
        vers = pick_versions(c, as_of)
    if not vers:
        yield "done", {"answer": "아직 등록된 문서가 없어요. 관리자가 문서를 올리면 물어볼 수 있어요.", "sources": [], "also": []}
        return
    raw = last = None
    for n, (p, strict) in enumerate(attempts_for(mode)):
        k, chars = llm.LIMITS[p]
        with db() as c:
            hits = search(c, vers, q, k)
        if not hits:
            yield "done", {"answer": NOT_FOUND, "sources": [], "also": [], "mode": p}
            return
        if n:
            yield "reset", {"note": f"{last.user_msg} — {llm.LABELS[p]}로 다시 답하는 중이에요"}
        ext, parts = llm.AnswerStream(), []
        try:
            for t in llm.stream(p, SYSTEM, build_prompt(hits, chars, q), strict):
                parts.append(t)
                new = ext.feed(t)
                if new:
                    yield "delta", {"text": new}
            raw, used = "".join(parts), p
            break
        except llm.ModelError as e:
            print(f"모델 호출 실패: {e}", flush=True)
            last = e
            if not e.fallback:
                break
    if raw is None:
        yield "error", {"detail": f"답변을 만들지 못했어요({last.user_msg if last else '알 수 없는 오류'}). 잠시 후 다시 시도해 주세요."}
        return
    note = f"{llm.LABELS[mode]}가 응답하지 않아 {llm.LABELS[used]}로 답했어요 ({last.user_msg})" if used != mode else None
    yield "done", finish_answer(raw, hits, q, as_of, u, used, note)


def finish_answer(raw, hits, q, as_of, u, used, note):
    data = parse_json(raw) or {}
    answer = data.get("answer") or raw.strip() or NOT_FOUND
    # 답변 본문에 섞여 나온 내부 표시 정리: <source …>, (source id="1"), (citations: …), [1] 등
    answer = re.sub(r"</?source[^>]*>|</?citations>|\(?citations?:[^)]*\)?|\s*\(\s*(?:source\s*id\s*[:=][^)]*)\)"
                    r"|\s*\bcitations\s*$|\s*\[\d+\]", "", answer).strip()

    sources, cited = [], set()
    for ct in data.get("citations", []) or []:
        try:
            i = int(ct["id"])
            hits[i - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        # 인용문이 실제 원문에 있는지 검증. 모델이 번호를 잘못 달았으면 다른 근거 조각에서도 찾아본다.
        quote = str(ct.get("quote", ""))
        found = None
        for j in [i] + [k for k in range(1, len(hits) + 1) if k != i]:
            found = find_quote(quote, hits[j - 1][2]["text"], is_paged(hits[j - 1][1]))
            if found:
                i = j
                break
        s, v, ch = hits[i - 1]
        ok, quote = bool(found), found  # 화면에는 원문에서 잘라 온 구간을 그대로 보여 준다
        cited.add(i)
        ex = next((x for x in sources if x["version_id"] == v["id"] and x["article"] == ch["label"]), None)
        if not ex:
            ex = {"doc": v["title"], "version": v["version"], "version_id": v["id"], "uploaded_at": v["uploaded_at"],
                  "article": ch["label"], "page": ch["page_from"], "page_to": ch["page_to"], "quotes": []}
            sources.append(ex)
        if ok and quote not in ex["quotes"]:
            ex["quotes"].append(quote)
    also = [{"doc": v["title"], "version": v["version"], "version_id": v["id"],
             "article": ch["label"], "page": ch["page_from"]}
            for i, (s, v, ch) in enumerate(hits, 1) if i not in cited][:3]
    with db() as c:
        c.execute("INSERT INTO logs(user_id,question,as_of,answer,sources,created_at,provider) VALUES(?,?,?,?,?,?,?)",
                  (u["id"], q, as_of, answer, json.dumps(sources, ensure_ascii=False), now(), used))
    verified = any(s["quotes"] for s in sources)  # 원문과 대조해 확인된 인용이 하나라도 있는지 (화면 표시용)
    return {"answer": answer, "verified": verified, "sources": sources, "also": also, "as_of": as_of,
            "mode": used, "mode_label": llm.LABELS[used], "note": note}


def check_question(b):
    q = b.question.strip()
    if not q or len(q) > 1000:
        raise err(400, "질문을 1,000자 이내로 입력해 주세요.")
    if b.as_of and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", b.as_of):
        raise err(400, "날짜는 2026-03-01 형식으로 입력해 주세요.")
    return q, resolve_mode(b.mode)


@app.post("/api/ask")
def ask(b: Ask, u=Depends(current_user)):
    """한 번에 결과만 받는 질문(점검 스크립트용). 화면은 /api/ask/stream 을 쓴다."""
    q, mode = check_question(b)
    for kind, data in answer_events(q, b.as_of, mode, u):
        if kind == "done":
            return data
        if kind == "error":
            raise err(502, data["detail"])


@app.post("/api/ask/stream")
def ask_stream(b: Ask, u=Depends(current_user)):
    """답을 쓰는 대로 보내는 질문(Server-Sent Events). 사건: delta · reset · done · error"""
    q, mode = check_question(b)

    def events():
        for kind, data in answer_events(q, b.as_of, mode, u):
            yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/admin/logs")
def logs(limit: int = 50, admin=Depends(require_admin)):
    with db() as c:
        rows = c.execute("SELECT l.id, u.name, u.email, l.question, l.as_of, l.answer, l.sources, l.created_at, l.provider "
                         "FROM logs l JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT ?",
                         (min(limit, 200),)).fetchall()
    out = []
    for r in rows:
        src = json.loads(r["sources"] or "[]")
        out.append(dict(r) | {"sources": src, "verified": any(s.get("quotes") for s in src)})
    return out


@app.middleware("http")
async def cache_headers(request, call_next):
    """화면 파일은 매번 새 버전인지 확인하게(no-cache) → 고친 화면이 팀원 핸드폰에 바로 반영된다.
    아이콘은 하루 동안 저장해도 된다. /api 는 저장하지 않는다."""
    res = await call_next(request)
    p = request.url.path
    if p.startswith("/api/"):
        res.headers.setdefault("Cache-Control", "no-store")
    elif p.startswith("/icons/"):
        res.headers["Cache-Control"] = "public, max-age=86400"
    else:
        res.headers["Cache-Control"] = "no-cache"
    return res


# 웹앱 화면(static 폴더)이 있으면 같은 서버에서 제공 → 접속 주소 하나로 해결
if Path("static").is_dir():
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
