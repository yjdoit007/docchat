# 05. 서버 API

모든 API는 `/api` 아래. 인증이 필요한 API는 `Authorization: Bearer <token>`.
시험은 서버 실행 후 `http://localhost:8000/docs` 에서 한다.

## 인증
| 메서드·경로 | 설명 |
|---|---|
| POST /auth/register | 가입 (이름, 이메일, 비밀번호) → 인증 코드 메일 발송 |
| POST /auth/resend | 인증 코드 다시 받기 |
| POST /auth/verify-email | 인증 코드 확인 → 승인 대기 상태로 |
| POST /auth/login | 로그인 → token, user |
| POST /auth/forgot | 재설정 코드 발송 (가입 여부 비공개) |
| POST /auth/reset | 코드 + 새 비밀번호로 변경 |
| GET /me | 내 정보 |

## 관리자 전용
| 메서드·경로 | 설명 |
|---|---|
| GET /admin/users?status=pending | 상태별 가입 요청 목록 (pending/approved/rejected) |
| POST /admin/users/{id}/approve · /reject | 승인 / 거절 |
| POST /docs | PDF 업로드 (title, file). 같은 title이면 새 버전으로 |
| GET /admin/logs | 질문·답변 로그 |

## 공통(로그인 필요)
| 메서드·경로 | 설명 |
|---|---|
| GET /docs | 문서와 버전 목록 |
| GET /versions/{vid}/pages/{n} | 특정 버전의 n쪽 텍스트 |
| GET /versions/{vid}/file | 원본 PDF 내려받기 |
| POST /ask | 질문 `{question, as_of?}` |

## /ask 응답
```json
{
  "answer": "친절한 답변",
  "sources": [
    {"doc": "문서명", "version": 3, "version_id": 7, "uploaded_at": "2026-03-01 10:00:00",
     "page": 143, "quotes": ["원문에서 그대로 가져온 문장"]}
  ],
  "also": [{"doc": "...", "version": 3, "version_id": 7, "page": 142}],
  "as_of": null
}
```
- `sources`: 답변이 실제로 인용한 페이지. `quotes`는 서버가 원문과 대조해 확인된 것만 담는다.
- `also`: 검색에는 걸렸지만 인용되지 않은 관련 페이지(최대 3).
- `as_of`: `YYYY-MM-DD`. 그 날짜 끝까지 올라온 문서별 최신 버전 기준.

## 제한
- 업로드: PDF만, 100MB 이하, 텍스트가 없는 스캔 PDF는 거절(OCR 필요).
- 질문 1,000자 이내.
- 업로드 시각은 한국 시간(KST)으로 저장.
