r"""사용 안내 문서로 앱 질문에 답하는지 점검 (운영 data 의 복사본에서 실행 → 운영 DB에 기록이 남지 않음)
    .venv\Scripts\python.exe guide_test.py
"""
import os, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
tmp = Path(tempfile.mkdtemp(prefix="docchat-guide-"))
shutil.copytree(HERE / "data", tmp / "data")
os.environ["DATA_DIR"] = str(tmp / "data")
os.chdir(HERE)
import server as S  # DATA_DIR 를 바꾼 뒤에 불러야 복사본을 쓴다

S.init()
with S.db() as c:
    admin = c.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()["id"]
guide = HERE.parent / "docs" / "app-guide.md"
r = S.add_document("문서 도우미 사용 안내", guide.name, guide.read_bytes(), admin)
print("안내 문서:", r)

QS = ["앱은 어떻게 설치해요? 아이폰이에요", "비밀번호를 잊어버렸어요", "빠름 모드랑 내부 모드 차이가 뭐예요?",
      "질문할 때 고객 이름 넣어도 돼요?", "정부가 보험료를 얼마나 지원하나요?"]
ok = 0
for q in QS:
    done = None
    for kind, data in S.answer_events(q, None, sys.argv[1] if len(sys.argv) > 1 else "gemini", {"id": admin}):
        if kind == "done":
            done = data
        elif kind == "error":
            print("오류:", data)
    src = [f"{s['doc']} · {s['article']}" for s in done.get("sources", []) if s["quotes"]]
    want_guide = "보험료" not in q
    good = done.get("verified") and (any("사용 안내" in s for s in src) == want_guide)
    ok += bool(good)
    print(f"\n{'✅' if good else '❌'} [질문] {q}  ({done.get('mode_label')})")
    print("   답변:", done.get("answer", "")[:150].replace("\n", " "))
    print("   근거:", src or "없음")
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n결과: {ok}/{len(QS)} (앱 질문은 사용 안내를, 법령 질문은 법령을 근거로)")
