# 문서 도우미 서버 (MVP)

## 실행 (Python 3.11 이상)
```bash
cd docchat-server
python -m venv .venv
source .venv/bin/activate        # 윈도우: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # 열어서 ADMIN_EMAIL, ADMIN_PASSWORD, ANTHROPIC_API_KEY 입력
uvicorn server:app --host 0.0.0.0 --port 8000
```

## 먼저 확인해 보기
브라우저에서 `http://localhost:8000/docs` 를 열면 모든 기능을 화면에서 시험할 수 있어요.

1. `POST /api/auth/login` 으로 관리자 로그인 → token 복사 → 오른쪽 위 Authorize에 붙여넣기
2. `POST /api/docs` 로 PDF 업로드 (같은 문서 이름으로 다시 올리면 v2, v3으로 쌓여요)
3. `POST /api/ask` 로 질문. `as_of` 에 `2026-03-01` 처럼 넣으면 그 날짜 기준 버전으로 답해요
4. 가입 흐름: `register` → 콘솔에 찍힌 코드로 `verify-email` → 관리자가 `admin/users` 에서 승인 → `login`

## 참고
- `.env` 에 SMTP_USER를 비워 두면 인증 코드가 서버 콘솔에만 출력돼요(테스트용).
  Gmail로 보내려면 SMTP_USER=Gmail 주소, SMTP_PASS=구글 계정의 "앱 비밀번호".
- 데이터는 `data/` 폴더에 저장돼요(DB, 원본 PDF 버전별). 이 폴더를 백업하면 돼요.
- 스캔(이미지) PDF는 글자 인식(OCR) 후 올려야 해요.
- `static/` 폴더에 웹앱 파일을 두면 같은 주소로 화면도 제공해요.
- 외부 접속(공유기 설정, HTTPS)은 다음 단계에서 정리해요.
