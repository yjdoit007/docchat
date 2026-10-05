r"""메일 설정 확인: .env의 SMTP 설정으로 메일 서버 로그인만 해 본다(메일은 보내지 않음, 비밀번호는 출력하지 않음).
    .venv\Scripts\python.exe mail_check.py
    .venv\Scripts\python.exe mail_check.py --send 받는주소@example.com   시험 메일 1통 보내기
"""
import smtplib, sys
from email.message import EmailMessage

import server as S

user, pw = S.SMTP_USER, S.SMTP_PASS
print(f"SMTP: {S.SMTP_HOST}:{S.SMTP_PORT} · 보내는 주소: {user or '(비어 있음)'} · 비밀번호: {'입력됨 (' + str(len(pw.replace(' ', ''))) + '자)' if pw else '(비어 있음)'}")
if not (user and pw):
    sys.exit("server\\.env 의 SMTP_USER, SMTP_PASS 를 채워 주세요.")
try:
    with smtplib.SMTP_SSL(S.SMTP_HOST, S.SMTP_PORT, timeout=15) as s:
        s.login(user, pw)
        print("✅ 메일 서버 로그인 성공")
        if "--send" in sys.argv:
            to = sys.argv[sys.argv.index("--send") + 1]
            m = EmailMessage()
            m["From"], m["To"], m["Subject"] = user, to, "[문서 도우미] 메일 발송 시험"
            m.set_content("문서 도우미 서버에서 보낸 시험 메일이에요. 이 메일이 보이면 인증 코드 메일도 정상으로 발송돼요.")
            s.send_message(m)
            print(f"✅ 시험 메일을 보냈어요 → {to}")
except smtplib.SMTPAuthenticationError as e:
    print("❌ 로그인 실패: 앱 비밀번호가 맞는지, SMTP_USER가 그 Gmail 주소인지 확인해 주세요.", e.smtp_code)
except Exception as e:
    print("❌ 연결 실패:", e)
