r"""한글 문서(HWP·HWPX·HWPML) 지원 점검 (서버를 켠 상태에서 실행)
    .venv\Scripts\python.exe hwp_test.py
주의: 시행규칙 한글 파일이 새 버전으로 DB에 남는다. 실제 운영 전에 data\ 를 새로 만든다.
"""
import io, time, zipfile

import doctext
import server as S
from smoke_test import ENV, SAMPLES, call, title_of

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))


def fake_hwpx(paras, header="머리글 글자"):
    """시험용 HWPX: 머리글 + 문단 + 표 안의 문단(문단 속 문단)을 넣는다."""
    body = "".join(f'<hp:p><hp:run><hp:t>{p}</hp:t></hp:run></hp:p>' for p in paras)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><hs:sec xmlns:hs="s" xmlns:hp="p">'
           f'<hp:p><hp:run><hp:ctrl><hp:header><hp:subList><hp:p><hp:run><hp:t>{header}</hp:t></hp:run></hp:p>'
           '</hp:subList></hp:header></hp:ctrl><hp:t>표 앞 글자</hp:t><hp:tbl><hp:tr><hp:tc><hp:subList>'
           '<hp:p><hp:run><hp:t>표 칸 글자 &amp; 기호</hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl>'
           '<hp:t>표 뒤 글자</hp:t></hp:run></hp:p>' + body + '</hs:sec>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", xml)
    return buf.getvalue()


print("[1] 글자 뽑기")
rule_hwp = next(p for p in SAMPLES.glob("*.hwp") if "시행규칙" in p.name)
rule_pdf = rule_hwp.with_suffix(".pdf")
kind, texts = doctext.extract(rule_hwp.read_bytes())
hc, pc = S.make_chunks(texts, False), S.make_chunks(doctext.extract(rule_pdf.read_bytes())[1])
check("법령정보센터 한글 파일(HWPML) 인식", kind == "hwpml")
check("조문 목록이 PDF와 같음", [c[0] for c in hc] == [c[0] for c in pc], f"{len(hc)}개")
check("조문 본문이 PDF와 같음(띄어쓰기 무시)",
      all(S._fold(a[3])[0] == S._fold(b[3])[0] for a, b in zip(hc, pc)))
check("한글 문서는 페이지 번호 없음", all(c[1] is None for c in hc))
check("머리글·바닥글 글자 제외, [시행 …] 줄 포함",
      "국가법령정보센터" not in texts[0] and "[시행 2024. 5. 14.]" in texts[0])

law = ["제1조(목적) 이 규정은 시험용이다.", "제2조(정의) 이 규정에서 쓰는 말의 뜻은 다음과 같다.", "1. 가나다"]
kind, texts = doctext.extract(fake_hwpx(law))
check("HWPX 인식", kind == "hwpx")
check("HWPX: 머리글 제외, 표 안 글자와 표 앞뒤 글자 순서대로", "머리글" not in texts[0] and
      "표 앞 글자\n표 칸 글자 & 기호\n표 뒤 글자" in texts[0], repr(texts[0][:60]))
check("HWPX: 조문 단위로 나눔", [c[0] for c in S.make_chunks(texts, False)][-2:] == ["제1조(목적)", "제2조(정의)"])
plain = [f"{i}번째 문단입니다. " * 12 for i in range(1, 11)]
chunks = S.make_chunks(doctext.extract(fake_hwpx(plain))[1], False)
check("조문이 없는 한글 문서는 '부분 N' 단위", chunks[0][0] == "부분 1" and len(chunks) >= 2,
      f"{len(chunks)}개, 평균 {sum(len(c[3]) for c in chunks) // len(chunks)}자")
for bad, name in [(b"hello world", "아무 파일"), (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600, "깨진 한글 바이너리")]:
    try:
        doctext.extract(bad); ok, msg = False, "통과됨"
    except doctext.DocError as e:
        ok, msg = True, str(e)
    check(f"{name} 거절", ok, msg)

print("\n[2] 서버에 올리기")
st, r = call("POST", "/api/auth/login", body={"email": ENV["ADMIN_EMAIL"], "password": ENV["ADMIN_PASSWORD"]})
admin = r["token"]
st, r = call("POST", "/api/docs", admin, {"title": title_of(rule_pdf)}, {"file": (rule_hwp.name, rule_hwp.read_bytes())})
if st == 409:  # 이전 실행에서 이미 올렸으면 그 버전을 그대로 쓴다
    latest = next(d for d in call("GET", "/api/docs", admin)[1] if d["title"] == title_of(rule_pdf))["versions"][0]
    st, r = 200, {"kind": latest["kind"], "pages": latest["page_count"], "version_id": latest["id"], "note": "이미 올린 버전 사용"}
check("한글 파일을 기존 문서의 새 버전으로 올림", st == 200 and r.get("kind") == "hwpml" and r.get("pages") is None,
      str(r))
vid = r.get("version_id")
st, r = call("POST", "/api/docs", admin, {"title": "깨진 파일"}, {"file": ("x.hwp", b"not a document")})
check("형식이 아닌 파일 거절", st == 400, r.get("detail", ""))
st, docs = call("GET", "/api/docs", admin)
v = next(d for d in docs if d["title"] == title_of(rule_pdf))["versions"][0]
check("문서 목록에 형식 표시", v["kind"] == "hwpml" and v["page_count"] is None)

import urllib.request
req = urllib.request.Request(f"http://localhost:8000/api/versions/{vid}/file", headers={"Authorization": f"Bearer {admin}"})
with urllib.request.urlopen(req) as f:
    got = f.read()
check("원본 한글 파일을 바이트 그대로 돌려줌", got == rule_hwp.read_bytes(), f"{len(got):,} bytes")

t = time.time()
st, r = call("POST", "/api/ask", admin, {"question": "손해평가인 보수교육은 언제 받아요?"})
src = [s for s in r.get("sources", []) if s["version_id"] == vid]
check("질문: 한글 버전(최신)을 근거로 답하고 인용 확인", bool(src) and any(s["quotes"] for s in src),
      f"{time.time() - t:.0f}초 · " + ", ".join(f"{s['article']} page={s['page']} 인용{len(s['quotes'])}" for s in src))
if src:
    st, a = call("GET", f"/api/versions/{vid}/article?label={urllib.parse.quote(src[0]['article'])}", admin)
    check("조문 원문 보기: 한글 문서는 페이지 없이", st == 200 and a["page_from"] is None and a["kind"] == "hwpml")

print(f"\n결과: {sum(results)}/{len(results)} 통과")
