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

## 4-1. 신청서 항목 정합 (2026-08) — 종이 서식 반영
운영 중이던 종이 「시설대관이용신청서」와 화면 항목이 어긋나 있어 맞췄습니다.

| 추가/변경 | 내용 |
|---|---|
| 컬럼 추가 | `applicant_age`(나이) · `applicant_address`(주소 또는 E-Mail) · `activity`(활동내용) |
| 이용인원 | 연령대만 → **연령대 × 남/여**. `participant_info` = `{band: {male, female, unspecified}}` |
| 필요 물품 | 하드코딩 4종 → **기관 설정 카탈로그 + 수량**. `requested_equipment` = `[{name, qty}]` |
| 안내문 | 동의 체크 한 줄 → 공지 및 준수사항 + 대관 규정 전문(기관 설정) 표시 후 동의 |
| 신규 서비스 | `services/form_settings_service.py` (place_settings 기반 신청서 설정) |
| 신규 라우트 | `GET /api/<slug>/form-config`, `GET·PUT /api/<slug>/admin/form-config` |

설계 원칙: **기관마다 다른 값(물품 목록·규정 문구)은 config 상수가 아니라 기관 DB
(place_settings)에 JSON 으로 저장**하고, config 의 `DEFAULT_*` 는 첫 생성 시 시딩용
기본값으로만 씁니다. 멀티테넌트라 특정 기관 규정을 코드에 못 박으면 안 됩니다.

레거시 호환은 **읽기 시 정규화**로 처리합니다. `parse_participants` 는 옛 `{"middle": 3}`
을 `unspecified` 버킷으로, `parse_equipment` 는 옛 `["앰프"]` 를 `[{name:"앰프", qty:1}]`
로 읽습니다(데이터 손실 없음). 스키마 마이그레이션은 `db._migrate_reservation_form_columns`
가 담당하며, `init_super_db()` 가 기존 기관마다 `init_place_db()` 를 호출해 적용합니다.

## 4-2. 대관 규칙 정합 (2026-08) — 종이 규정 반영
종이 규정 중 **강제할 것**과 **안내만 할 것**을 나눠 적용했습니다.

| 종이 규정 | 처리 |
|---|---|
| 대관 1일 전까지 취소 | **강제** — `cancel()` 이 마감 검사. 응답에 `can_cancel`·`cancel_deadline` |
| 노쇼·이용확인 미실시 6개월 제한 | **강제** — `attendance` 기록 → `penalty_until()` 이 공개 신청 차단 |
| 2시간 후 뒤 예약 없으면 1시간 연장 | **강제** — `extend()` (관리자 현장 처리) |
| 예약 가능 범위 최대 2주 | **강제** — `booking_max_days` 기본 14 (변경 없음, 안내 문구를 코드에 맞춤) |
| 이용 시간 1시간 단위 자유 선택 | **현행 유지** — '10시부터 2시간 간격' 안내 문구를 실제 동작에 맞게 수정 |
| 성인 오후 2시 이전 | **안내만** — 청소년 시설 운영 방침. 서버에서 막지 않음 |

숫자 규칙은 전부 `place_settings.booking_rules` (기관별). `booking_window()` 은
`conn`·`rules` 를 선택 인자로 받아 기관 규칙을 따르되, 인자가 없으면 config 기본값으로
동작합니다(단위 테스트/기관 밖 호출 호환).

주의: 안내 문구(`form_notice`/`form_rules`)와 실제 규칙(`booking_rules`)은 **따로**
저장됩니다. 규칙을 바꾸면 '신청서 설정' 의 문구도 함께 손봐야 합니다 — 운영 설정 화면에
그 안내를 넣어뒀습니다.

## 4-3. 동아리 단기대관 (2026-08)
동아리의 추가 대관을 신청인 정보 없이 동아리명만으로 확정하는 관리자 경로입니다.
`services/club_service.py` 가 외부 ClubLog(`CLUBLOG_API_BASE`)를 프록시하고,
`GET /api/<slug>/admin/clubs` 로 노출합니다. 프론트가 외부 도메인을 직접 부르지 않는
이유는 CORS + 인증 경계 때문입니다.

설계 원칙 — **외부 의존이 화면을 죽이지 않는다**: 조회 실패(연결 불가/404/파싱 실패)는
예외로 터뜨리지 않고 `available:false` + 사유를 담아 200 으로 돌려주며, 관리자 화면
(`components/ClubPicker.tsx`)은 항상 직접 입력 칸을 함께 제공합니다. urllib 기본
User-Agent 는 상대 WAF 가 403 으로 막아서 서비스명을 밝히는 UA 를 붙였습니다
(실제로 이 문제를 겪었으므로 UA 를 지우지 마세요).

예약 생성은 기존 `create_admin_reservation` 을 그대로 씁니다 — 별도 엔드포인트를 두지
않고, `name` 이 비고 `club` 이 있으면 동아리명을 표시명으로 채우는 한 줄만 추가했습니다.
덕분에 중복/정기활동 검사·경고 로직이 그대로 적용됩니다.

## 4-4. 정기 고정활동 유형 구분 (2026-08)
`recurring_blocks` 에 `kind`(`club`|`program`|`etc`) 를 추가했습니다. 이 표에는 동아리
정기활동만 들어가는 게 아니라 센터 프로그램·시설 점검·외부 정기대관도 함께 들어가는데,
활동명 한 칸으로만 구분하다 보니 화면에서 성격을 알 수 없었습니다.

- 등록/수정 검증은 `_parse_recurring_block()` 하나로 모아 POST·PUT 이 공유합니다.
- **활동명을 필수로 바꿨습니다**(기존엔 선택). 레거시 행은 그대로 두고 신규 입력만
  강제하므로, 제목 없는 옛 행은 현황표에서 계속 "정기활동" 으로 표시됩니다.
- 관리자 화면은 유형을 고르면 입력 UI 가 바뀝니다 — `club` 이면 `ClubPicker`(동아리
  목록 + 직접 입력), 나머지는 유형별 placeholder 를 가진 텍스트 입력.
- `PUT .../recurring-blocks/<id>` 를 새로 열었습니다(학기마다 시간·담당이 바뀜).
- day-grid/week-grid 의 block 세그먼트에 `kind` 가 실려 그리드에서 유형을 보여줍니다.

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
- [ ] **이용확인 20분 초과 자동취소는 미구현** — 현재는 담당자가 `attendance` 를 `no_show`
      로 기록하는 수동 처리다. 자동화하려면 예약 시작 시각 기준 배치/스케줄러가 필요.
- [ ] 재대관 제한은 이름+연락처 일치로만 판정한다(동명이인이 같은 번호를 쓰는 경우 없음을
      전제). 계정 개념이 생기면 사용자 단위로 옮길 것.
- [ ] 안내 문구와 실제 규칙이 별도 저장이라 관리자가 한쪽만 바꾸면 어긋날 수 있다
      (화면 안내로만 방지 중).
- [ ] 시설 이미지 업로드 기능 없음(경로 문자열만 입력). `static/img/` 는 기본 기관 이미지 공용.

## 9. 프론트엔드와의 계약
- 프론트는 `src/api/types.ts` 가 계약. 응답 형태 변경 시 동기화 필요.
- 프론트는 URL 의 `:slug` 를 읽어 `/api/<slug>/...` 를 호출(`src/hooks/useOrg.ts`), 슈퍼는 `/api/super/...`.
- 개발 시 Vite 프록시로 `/api`·`/static` → :5000 (same-origin 세션 쿠키).
