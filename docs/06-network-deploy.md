# 06. 외부 접속 구성

## 결정 (2026-10-05)
**Tailscale Funnel**. 공유기 설정 없이 노트북이 바깥으로 연결을 맺고, Tailscale이 HTTPS 공개 주소를 준다.
- 공개 주소: **https://docchat.tail15f4b2.ts.net** (Tailscale 계정 yjdoit007, 기기 이름 docchat)
- 팀원은 앱 설치 없이 주소만으로 접속. 인증서는 Let's Encrypt, Tailscale이 자동 갱신
- 처음 계획(포트포워딩 + DuckDNS + Caddy)은 메인 공유기(LGU_5F49) 관리자 로그인이 안 돼 보류.
  `deploy\Caddyfile`, `run-caddy.ps1`, DuckDNS 자동 갱신 코드는 나중을 위해 남겨 둔다(쓰지 않으면 `.env`의 DUCKDNS_* 를 비워도 됨)

## 구성
```
[핸드폰/PC] --https://docchat.tail15f4b2.ts.net--> [Tailscale Funnel 중계] --> [노트북 tailscaled] --> [docchat 서버 127.0.0.1:8000]
```
- docchat 서버는 **127.0.0.1** 로만 연다(같은 Wi-Fi에서도 직접 접속 불가, 공개 주소로만)
- `.env`의 `API_DOCS=0` 으로 API 시험 화면(/docs)을 껐다
- Funnel 설정은 `tailscale funnel --bg 8000` 으로 저장돼 있어 Tailscale(윈도우 서비스)이 켜지면 자동으로 다시 열린다

## 확인 결과 (2026-10-05)
| 항목 | 결과 |
|---|---|
| 메인 화면 / 로그인 없는 API / /docs | 200 / 401 / 404 |
| HTTPS 인증서 | CN=docchat.tail15f4b2.ts.net, Let's Encrypt, 2027-01-03 만료(자동 갱신) |
| Gemini 스트리밍(공개 주소 경유) | 첫 글자 1.1~1.7초, 전체 2.3~2.9초, 인용 확인 |
| Funnel이 더하는 지연 | 거의 없음 (노트북 안 주소와 같음) |

## 운영
- 서버 실행: `deploy\start-server.ps1` (멈추면 10초 뒤 다시 시작, 기록은 `deploy\logs\`)
- Funnel 상태: `tailscale funnel status` / 끄기: `tailscale funnel --https=443 off`
- 노트북이 켜져 있고 절전하지 않아야 한다(전원 연결 시 절전 '안 함', 덮개 닫아도 '아무 것도 안 함')
- `data\` 폴더(DB + 원본 문서)를 정기 백업한다
- 관리자 화면 > 서버 설정에 `https://docchat.tail15f4b2.ts.net` 저장 후 팀원에게 안내

## 주의사항
- 공개 주소이므로 인터넷 누구나 로그인 화면까지는 볼 수 있다. 로그인 시도 제한·이메일 인증·관리자 승인 가입은 구현돼 있다.
- Tailscale 무료 요금제의 Funnel은 통신량 제한이 있다(문서 질문 용도로는 충분).
- 주소에 들어간 `tail15f4b2` 는 Tailscale 계정(tailnet)마다 다르다. 계정을 바꾸면 주소도 바뀐다.
