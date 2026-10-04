"""문서 도우미 서버 (MVP)
- 가입(이메일 인증) → 관리자 승인 → 로그인, 비밀번호 초기화
- PDF 업로드, 날짜별·버전별 보관, 페이지 단위 검색
- 질문하면 근거 페이지와 원문 인용을 함께 답변
"""
import hashlib, hmac, json, math, os, re, secrets, smtplib, sqlite3, time, urllib.error, urllib.request
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()
KST = timezone(timedelta(hours=9))
DATA = Path(os.getenv("DATA_DIR", "data"))
FILES = DATA / "files"
DB_PATH = DATA / "docchat.db"
# 답변을 만드는 모델: ollama(로컬, 무료) 또는 claude(API 키 필요)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT") or "300")
# 질문 하나에 모델에게 보내는 근거 조각(조문) 수와 조각당 글자 수 (로컬 모델은 적게 보내야 빠름)
TOP_K = int(os.getenv("TOP_K") or ("4" if LLM_PROVIDER == "ollama" else "8"))
CHUNK_CHARS = int(os.getenv("CHUNK_CHARS") or ("1500" if LLM_PROVIDER == "ollama" else "4000"))
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
        # 조문 조각이 없는 기존 버전은 저장된 페이지 텍스트로 조각을 만든다
        for (vid,) in c.execute("SELECT id FROM versions WHERE id NOT IN (SELECT DISTINCT version_id FROM chunks)").fetchall():
            texts = [r["text"] for r in c.execute("SELECT text FROM pages WHERE version_id=? ORDER BY page_no", (vid,))]
            save_chunks(c, vid, texts)
        if ADMIN_EMAIL and ADMIN_PASSWORD and not c.execute(
                "SELECT 1 FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone():
            c.execute("INSERT INTO users(name,email,pw_hash,role,status,created_at) VALUES(?,?,?,?,?,?)",
                      ("관리자", ADMIN_EMAIL, ph.hash(ADMIN_PASSWORD), "admin", "approved", now()))
            print("관리자 계정을 만들었어요:", ADMIN_EMAIL)
    if not ADMIN_EMAIL:
        print("주의: ADMIN_EMAIL / ADMIN_PASSWORD 가 설정되지 않아 관리자 계정이 없어요.")


@asynccontextmanager
async def lifespan(app):
    init()
    yield


app = FastAPI(title="문서 도우미", lifespan=lifespan)


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
    if r.rowcount == 0 or not u:
        raise err(404, "대상을 찾을 수 없어요.")
    if new == "approved":
        try:
            send_mail(u["email"], "[문서 도우미] 승인되었어요", f"{u['name']}님, 이제 로그인해서 사용할 수 있어요.")
        except HTTPException:
            pass
    return {"ok": True, "status": new}


# ---------- 문서 / 버전 ----------
@app.post("/api/docs")
def upload(title: str = Form(...), file: UploadFile = File(...), admin=Depends(require_admin)):
    title = title.strip()
    if not title:
        raise err(400, "문서 이름을 입력해 주세요.")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise err(413, "파일이 너무 커요. 100MB 이하로 올려 주세요.")
    if not data.startswith(b"%PDF"):
        raise err(400, "PDF 파일만 올릴 수 있어요.")
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
        texts = [p.get_text() for p in pdf]
    except Exception:
        raise err(400, "PDF를 읽지 못했어요. 파일이 손상됐는지 확인해 주세요.")
    if not any(t.strip() for t in texts):
        raise err(400, "텍스트가 없는 스캔 PDF예요. 글자 인식(OCR)을 거쳐서 다시 올려 주세요.")
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
        vid = c.execute("INSERT INTO versions(doc_id,version,filename,uploaded_at,uploaded_by,sha256,page_count) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (doc_id, ver, file.filename, now(), admin["id"], sha, len(texts))).lastrowid
        c.executemany("INSERT INTO pages VALUES(?,?,?)", [(vid, i + 1, t) for i, t in enumerate(texts)])
        n_chunks = save_chunks(c, vid, texts)
    (FILES / str(doc_id)).mkdir(parents=True, exist_ok=True)
    (FILES / str(doc_id) / f"v{ver}.pdf").write_bytes(data)
    return {"document": title, "version": ver, "version_id": vid, "pages": len(texts), "chunks": n_chunks}


@app.get("/api/docs")
def list_docs(u=Depends(current_user)):
    with db() as c:
        docs = c.execute("SELECT id,title FROM documents ORDER BY title").fetchall()
        out = []
        for d in docs:
            vs = c.execute("SELECT id,version,uploaded_at,page_count FROM versions WHERE doc_id=? "
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
        r = c.execute("SELECT ch.label, ch.page_from, ch.page_to, ch.text, v.version, v.uploaded_at, d.title "
                      "FROM chunks ch JOIN versions v ON v.id=ch.version_id JOIN documents d ON d.id=v.doc_id "
                      "WHERE ch.version_id=? AND ch.label=?", (vid, label)).fetchone()
    if not r:
        raise err(404, "해당 조문을 찾을 수 없어요.")
    out = dict(r)
    out["text"] = re.sub(r"(?<=[가-힣])[ \t]*\n[ \t]*(?=[가-힣])", "", r["text"])
    return out


@app.get("/api/versions/{vid}/file")
def get_file(vid: int, u=Depends(current_user)):
    with db() as c:
        r = c.execute("SELECT doc_id, version, filename FROM versions WHERE id=?", (vid,)).fetchone()
    p = r and FILES / str(r["doc_id"]) / f"v{r['version']}.pdf"
    if not r or not p.exists():
        raise err(404, "원본 파일을 찾을 수 없어요.")
    return FileResponse(p, media_type="application/pdf", filename=r["filename"])


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


def make_chunks(texts):
    """페이지 텍스트를 조문(제N조) 단위로 나눈다. 조문이 없는 문서는 페이지 단위로 둔다.
    반환: [(label, page_from, page_to, text)]"""
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


def save_chunks(c, vid, texts):
    rows = make_chunks(texts)
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
- 답변에 사용한 근거마다 소스 id와 소스 본문에 있는 문장을 한 글자도 바꾸지 말고 quote에 복사하세요(120자 이내).
- answer에는 설명만 쓰고, 인용문이나 소스 번호는 넣지 마세요. 인용은 citations에만 넣으세요.
- 법적 판단이 필요한 내용은 문서에 적힌 대로만 전달하고, 단정하기 어렵다면 그렇게 말하세요.
- 반드시 JSON만 출력하세요: {"answer": "...", "citations": [{"id": 1, "quote": "..."}]}"""
ANSWER_SCHEMA = {
    "type": "object", "required": ["answer", "citations"],
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {
            "type": "object", "required": ["id", "quote"],
            "properties": {"id": {"type": "integer"}, "quote": {"type": "string"}}}},
    },
}
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
NOTE_RE = re.compile(r"<(?:개정|신설|삭제|타법개정)[^>]*>|\[(?:전문개정|제목개정|본조신설|종전[^\]]*|[^\]]*이동)[^\]]*\]")
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


def find_quote(quote, text):
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
    span = re.sub(r"(?<=[가-힣])[ \t]*\n[ \t]*(?=[가-힣])", "", span)  # PDF 줄바꿈으로 끊긴 낱말 잇기
    return re.sub(r"\s+", " ", span).strip()


def for_model(text):
    """모델에게 보낼 원문 정리: 개정 표시를 빼고, PDF에서 글자 수 때문에 끊긴 줄을 잇는다."""
    text = NOTE_RE.sub("", text)
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out = ""
    for prev, cur in zip([""] + lines, lines):
        starts_item = re.match(r"^\s*(제\d+조|[①-⑳]|\d+(의\d+)?\.|[가-하]\.|\[|부\s*칙)", cur)
        if out and len(prev) >= 40 and not starts_item:
            out += cur.strip()  # 줄 길이가 꽉 찬 상태로 끊겼다면 이어 붙인다
        else:
            out += ("\n" if out else "") + cur.strip()
    return out


def llm(prompt):
    """SYSTEM 규칙과 질문을 모델에 보내고 응답 글자를 돌려준다. LLM_PROVIDER로 모델을 고른다."""
    if LLM_PROVIDER == "claude":
        from anthropic import Anthropic  # Claude를 쓸 때만 필요
        msg = Anthropic().messages.create(
            model=MODEL, max_tokens=1500, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}])
        return "".join(x.text for x in msg.content if x.type == "text")
    if LLM_PROVIDER == "ollama":
        # 1차: 답변 형식(스키마)을 강제해 citations를 빠뜨리지 않게 한다.
        # 2차: 로컬 모델이 가끔 생성 도중 실패(500)하므로 형식 지정 없이 다시 시도한다(parse_json이 처리).
        for fmt in (ANSWER_SCHEMA, None):
            body = {"model": OLLAMA_MODEL, "stream": False, "options": {"temperature": 0, "num_ctx": 8192},
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
            if fmt:
                body["format"] = fmt
            req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as r:
                    return json.loads(r.read())["message"]["content"]
            except urllib.error.HTTPError as e:
                if fmt is None or e.code < 500:
                    raise
                print("Ollama 오류, 형식 지정 없이 다시 시도:", e)
    raise ValueError(f"알 수 없는 LLM_PROVIDER: {LLM_PROVIDER} (ollama 또는 claude)")


@app.post("/api/ask")
def ask(b: Ask, u=Depends(current_user)):
    q = b.question.strip()
    if not q or len(q) > 1000:
        raise err(400, "질문을 1,000자 이내로 입력해 주세요.")
    if b.as_of and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", b.as_of):
        raise err(400, "날짜는 2026-03-01 형식으로 입력해 주세요.")
    with db() as c:
        vers = pick_versions(c, b.as_of)
        hits = search(c, vers, q, TOP_K) if vers else []
    if not vers:
        return {"answer": "아직 등록된 문서가 없어요. 관리자가 문서를 올리면 물어볼 수 있어요.", "sources": [], "also": []}
    if not hits:
        return {"answer": NOT_FOUND, "sources": [], "also": []}

    ctx = "\n".join(f'<source id="{i}" doc="{v["title"]}" version="v{v["version"]}" article="{ch["label"]}">\n'
                    f'{for_model(ch["text"])[:CHUNK_CHARS]}\n</source>' for i, (s, v, ch) in enumerate(hits, 1))
    try:
        raw = llm(f"{ctx}\n\n질문: {q}")
    except Exception as e:
        print(f"모델 호출 실패({LLM_PROVIDER}):", e)
        raise err(502, "답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요.")
    data = parse_json(raw) or {}
    answer = data.get("answer") or raw.strip() or NOT_FOUND
    answer = re.sub(r"</?source[^>]*>|</?citations>|\(?citations?:[^)]*\)?|\(source id:[^)]*\)|\s*\bcitations\s*$|\s*\[\d+\]",
                    "", answer).strip()

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
            found = find_quote(quote, hits[j - 1][2]["text"])
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
        c.execute("INSERT INTO logs(user_id,question,as_of,answer,sources,created_at) VALUES(?,?,?,?,?,?)",
                  (u["id"], q, b.as_of, answer, json.dumps(sources, ensure_ascii=False), now()))
    verified = any(s["quotes"] for s in sources)  # 원문과 대조해 확인된 인용이 하나라도 있는지 (화면 표시용)
    return {"answer": answer, "verified": verified, "sources": sources, "also": also, "as_of": b.as_of}


@app.get("/api/admin/logs")
def logs(limit: int = 50, admin=Depends(require_admin)):
    with db() as c:
        rows = c.execute("SELECT l.id, u.name, l.question, l.as_of, l.answer, l.sources, l.created_at "
                         "FROM logs l JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT ?",
                         (min(limit, 200),)).fetchall()
    return [dict(r) | {"sources": json.loads(r["sources"])} for r in rows]


# 웹앱 화면(static 폴더)이 있으면 같은 서버에서 제공 → 접속 주소 하나로 해결
if Path("static").is_dir():
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
