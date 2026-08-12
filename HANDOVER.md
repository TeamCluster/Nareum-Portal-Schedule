# 인수인계 문서 — 백엔드 (Nareum-Portal-Schedule)

> 대관 예약 시스템의 **Flask JSON API** 백엔드. **멀티테넌트**(기관별 DB 분리) 구조.
> 실행/엔드포인트 요약은 [README.md](README.md) 참고.

## 1. 개요 & 역할 구조
3계층으로 구성됩니다.
- **슈퍼 관리자**: 기관(place)을 추가·삭제하고 기관별 관리자 비밀번호를 관리. 슈퍼 비밀번호는 단일.
- **기관 관리자**: 자기 기관의 시설·예약을 상세 관리(승인/거절/직접추가/수정, 시설 CRUD). 기관마다 독립 비밀번호.
- **예약 신청자(공개)**: 로그인 없이 이름+연락처로 예약 신청 후 조회/취소.

## 2. 리팩토링 배경 (무엇이 바뀌었나)
원래 단일 기관(SQLAlchemy 단일 DB)이었으나, 여러 기관이 한 서비스에서 각자 운영하도록 **멀티테넌트로 전환**했습니다. 참고 프로젝트 `2026ClubLog`(슈퍼/기관 분리, 기관별 sqlite)의 설계를 그대로 계승했습니다.

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| 데이터 계층 | Flask-SQLAlchemy ORM, 단일 DB | 순수 `sqlite3`, **기관별 DB 파일 분리** |
| 기관 개념 | 없음(단일) | `super.sqlite3` 의 places + 기관별 `<slug>.sqlite3` |
| 인증 | 단일 비밀번호(`manage_logged_in`) | 슈퍼(`super_logged_in`) + 기관별(`place_admins[slug]`), werkzeug 해시 |
| URL | `/api/*`, `/api/admin/*` | `/api/super/*`, `/api/<slug>/*`, `/api/<slug>/admin/*` |
| 비번/시크릿 | `.env` 평문 | DB 저장(해시) + 첫 실행 자동 생성 |

제거됨: `models/`, `api/`(구 blueprint), Flask-SQLAlchemy 의존.
추가됨: `db.py`, `services/`, slug 기반 라우팅.

## 3. 아키텍처 & 설계 결정
- **DB-per-tenant**: 기관마다 `db/<slug>.sqlite3`. 완전 격리(한 기관 데이터가 다른 기관에 절대 노출 안 됨). 슈퍼 레지스트리는 `db/super.sqlite3`.
- **요청 내 멀티 커넥션**: `db.get_super_db()` / `db.get_place_db(slug)` 가 `flask.g.dbs` 에 커넥션을 캐싱, `teardown` 에서 일괄 close.
- **자체 부트스트랩**: `db.init_super_db()` 가 첫 실행 시 슈퍼 임시 비번(콘솔 1회 출력)·`SECRET_KEY`·기본 기관(`nareum`+시설5)을 생성. `.env` 없이 동작.
- **얇은 라우트 + 서비스 계층**: 검증/규칙은 `services/*` 에, 라우트(app.py)는 위임만. 실패는 `ApiError` raise → 전역 핸들러가 `{"error": msg}`+status.
- **slug 안전성**: `config.is_valid_slug` — 소문자 시작 2~30자, 예약어(super/api/admin/static/public/manage) 차단. URL 경로 + DB 파일명에 함께 쓰이므로 필수.

## 4. 인증/세션 (app.py 데코레이터)
- `super_required` — `session["super_logged_in"]` 확인.
- `place_required` — slug 유효+존재 확인(공개 라우트), `g.place` 에 기관정보 주입.
- `place_admin_required` — 위 + `session["place_admins"][slug]` 로그인 확인.
- 세션의 `place_admins` 는 dict 라 내부 변경 후 `session.modified=True` 필요.

## 5. 예약 도메인 규칙 (services/reservation_service.py)
기존 규칙을 raw SQL 로 이식(상수는 config.py):
- 예약창 오늘+3~+14, 운영 09~18시, 연속시간만.
- 공개: 최대 2시간, 하루 1회, 주간(월~일) 최대 2회, 인원 ≥ 1.
- 관리자 직접추가/수정: 공개 제한 없음, 시설/시간 중복만 차단.
- `ACTIVE_STATUSES=(confirmed,pending)` 만 슬롯 점유. 취소/거절은 soft delete.
- 시간은 ISO 문자열(`YYYY-MM-DDTHH:MM:SS`)로 저장 → 문자열 범위 비교로 날짜 필터.

## 6. 기관 삭제 정책 (옵션 B)
`place_service.delete_place` 는 places 행만 지우고 `db/<slug>.sqlite3` **파일은 보존**. 같은 slug 로 재추가하면 예약 데이터가 그대로 복구됩니다.

## 7. 테스트
`tests/` — 70 케이스. `DB_FOLDER` 를 임시폴더로 지정해 각 테스트마다 wipe+bootstrap. 도메인 규칙 단위 + 슈퍼/기관공개/기관관리자 통합 + **테넌트 격리** 검증 포함. `pytest.ini` 의 `-s` 는 app.py 의 stdout 재설정과 pytest 캡처 충돌 회피용.

## 8. 알려진 한계 / 향후 과제 (TODO)
- [ ] 기관/슈퍼 모두 단일 비밀번호(계정·역할 개념 없음).
- [ ] 정식 마이그레이션 도구 없음(스키마 변경은 db.py 의 `_migrate_*` 에 ALTER 추가).
- [ ] 예약 동시성 락 없음(현 트래픽 규모에선 무해).
- [ ] 이메일/문자 알림 없음.
- [ ] 시설 이미지 업로드 기능 없음(경로 문자열만 입력). `static/img/` 는 기본 기관 이미지 공용.

## 9. 프론트엔드와의 계약
- 프론트는 `src/api/types.ts` 가 계약. 응답 형태 변경 시 동기화 필요.
- 프론트는 URL 의 `:slug` 를 읽어 `/api/<slug>/...` 를 호출(`src/hooks/useOrg.ts`), 슈퍼는 `/api/super/...`.
- 개발 시 Vite 프록시로 `/api`·`/static` → :8000 (same-origin 세션 쿠키).
- macOS 는 :5000 을 AirPlay 수신기가 점유해 모든 요청에 403 을 준다. 프록시 포트를
  바꿀 일이 있어도 5000 은 피할 것.
- `vite.config.js` 가 프론트 루트에 있으면 Vite 가 `vite.config.ts` 대신 그걸 읽는다.
  (과거 `tsc -b` 산출물) — 보이면 지울 것.
