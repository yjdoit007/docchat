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
| POST /admin/users/{id}/approve · /reject | 승인 / 거절 (승인된 사람의 승인 취소도 reject). 작업 기록에 남음 |
| GET /admin/members | 승인된 사용자 전체(관리자 포함) + 관리자를 누가 언제 지정했는지 |
| POST /admin/members/{id}/role | `{role: "admin" \| "viewer"}` 관리자 지정·해제. 승인된 사용자만, **마지막 관리자는 해제 불가(409)**. 작업 기록에 남음 |
| GET /admin/audit | 작업 기록(승인·거절·관리자 지정/해제·설정 변경), 최근 순 |
| GET · PUT /admin/settings | 서버 주소 `{public_url}` 조회·저장 (http(s):// 로 시작) |
| POST /docs | 문서 업로드 (title, file: PDF·HWP·HWPX·HWPML). 같은 title이면 새 버전으로. 응답에 kind(pdf/hwp/hwpx/hwpml), pages(한글은 null), chunks(조문 조각 수) |
| GET /admin/logs | 질문·답변 로그 (이름, 이메일, 출처, verified) |

## 공통(로그인 필요)
| 메서드·경로 | 설명 |
|---|---|
| GET /docs | 문서와 버전 목록 |
| GET /versions/{vid}/pages/{n} | 특정 버전의 n쪽 텍스트 |
| GET /versions/{vid}/article?label=제7조(국가 등의 재정지원) | 조문 원문(출처 펼쳐 보기용), 시작·끝 페이지 |
| GET /versions/{vid}/file | 원본 PDF 내려받기 |
| POST /ask | 질문 `{question, as_of?}` |

## /ask 응답
```json
{
  "answer": "친절한 답변",
  "verified": true,
  "sources": [
    {"doc": "문서명", "version": 3, "version_id": 7, "uploaded_at": "2026-03-01 10:00:00",
     "article": "제7조(국가 등의 재정지원)", "page": 2, "page_to": 2, "quotes": ["원문에서 그대로 잘라 온 구간"]}
  ],
  "also": [{"doc": "...", "version": 3, "version_id": 7, "article": "제4조(보험료의 지원)", "page": 1}],
  "as_of": null
}
```
- `verified`: 원문과 대조해 확인된 인용이 하나라도 있는지. false면 화면에서 답변을 흐리게 + 경고.
- `sources`: 답변이 인용한 조문. `quotes`는 서버가 원문과 대조해 확인된 것만, **원문에서 잘라 온 그대로** 담는다.
- `also`: 검색에는 걸렸지만 인용되지 않은 관련 조문(최대 3).
- `as_of`: `YYYY-MM-DD`. 그 날짜 끝까지 올라온 문서별 최신 버전 기준.

## 제한
- 업로드: PDF·한글(HWP, HWPX, HWPML), 100MB 이하. 형식은 파일 내용으로 판별(확장자 아님).
  텍스트가 없는 스캔 PDF, 암호·배포용 한글 파일은 거절.
- 한글 문서는 페이지 번호가 없어 `page`, `page_count`, `page_from/page_to`가 null이다. 원본 내려받기는 올린 파일 그대로.
- 질문 1,000자 이내.
- 업로드 시각은 한국 시간(KST)으로 저장.
