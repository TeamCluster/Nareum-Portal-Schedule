# Nareum-Portal-Schedule (Backend API)

대관 예약 시스템의 **Flask JSON API** 백엔드입니다. **멀티테넌트**(여러 기관이 한 서비스에서 각자 운영) 구조로, 슈퍼 관리자가 기관을 추가/관리하고 기관별 관리자가 자기 기관의 시설·예약을 관리합니다. 프론트엔드는 별도 저장소(`Nareum-Portal-Schedule-FE`, React + Vite)에서 이 API를 소비합니다.

## 스택
- Flask (app factory + 함수형 라우트 등록)
- 순수 `sqlite3` (ORM 없음) — 기관별 DB 파일 분리
- Werkzeug 비밀번호 해시(pbkdf2), 세션 쿠키 기반 인증
- Flask-Cors (credentials 허용)

## 실행
```bash
pip install -r requirements.txt
python app.py          # http://127.0.0.1:5000
```
- 첫 실행 시 `db/super.sqlite3` 를 만들고 **슈퍼 임시 비밀번호를 콘솔에 1회 출력**하며, `SECRET_KEY` 를 자동 생성해 DB 에 저장합니다.
- 기관이 하나도 없으면 **기본 기관 `nareum`**(시설 5종)을 시딩합니다(관리자 임시 비번 `manage123`).
- 환경변수로 초기값을 지정할 수 있습니다: `SUPER_PASSWORD`, `DEFAULT_PLACE_PASSWORD`, `SECRET_KEY`, `CORS_ORIGINS`, `DB_FOLDER`, `BOOKING_MIN_DAYS/MAX_DAYS` 등.

## 데이터 구조 (멀티테넌트)
```
db/super.sqlite3      places(기관: slug,full_name,short_name,pw_hash,주소/전화/이메일)
                      super_admin(슈퍼 비번 해시, 단일 row) · app_settings(secret_key 등)
db/<slug>.sqlite3     facilities · reservations · operating_hours · closures
                      recurring_blocks · place_settings(신청서 설정 포함)
                        ← 기관마다 별도 파일로 완전 격리
```

## 코드 구조
```
app.py                create_app() + 3개 데코레이터 + 라우트 등록
config.py             경로·slug 검증·예약 규칙 상수·CORS·기본 기관 시드값
db.py                 super/기관 스키마 · g.dbs 커넥션 캐싱 · 부트스트랩 · 시딩
services/
  super_service.py    슈퍼 비번 검증/변경
  place_service.py    기관 CRUD · 기관 비번 검증/변경
  facility_service.py 시설 CRUD/조회 (기관 DB)
  form_settings_service.py  신청서 설정 — 필요 물품 목록·공지·대관규정 (기관 DB)
  club_service.py     동아리 목록 — 외부 ClubLog API 프록시(캐시·실패 시 graceful)
  reservation_service.py  예약 도메인 규칙 전체 (기관 DB)
  errors.py           ApiError (라우트 전역 핸들러가 {"error":..}+status 로 변환)
```

## URL 구조
```
/api/super/...          슈퍼 관리자 (기관 CRUD·비번관리)
/api/<slug>/...         기관 공개 (예약 신청/조회)
/api/<slug>/admin/...   기관 관리자 (예약 승인/관리·시설 관리)
```

### 주요 엔드포인트
| 영역 | 메서드·경로 |
|---|---|
| 슈퍼 인증 | `POST /api/super/login·logout`, `GET /api/super/session`, `POST /api/super/password` |
| 슈퍼 기관 | `GET/POST /api/super/places`, `PUT/DELETE /api/super/places/<slug>`, `POST /api/super/places/<slug>/password` |
| 기관 인증 | `POST /api/<slug>/admin/login·logout`, `GET /api/<slug>/admin/session` |
| 기관 공개 | `GET /info·facilities·availability·form-config`, `GET .../booked-times`, `POST /reservations`, `GET /reservations/<access_id>`, `POST /reservations/lookup`, `POST /reservations/<id>/cancel` |
| 기관 관리자 | `clubs·dashboard·requests·reservations(목록/생성)·reservations/<id>(GET/PUT)·calendar-events·booked-times·approve·reject·reservations/<id>/attendance·reservations/<id>/extend`, 시설: `GET/POST/PUT/DELETE .../admin/facilities`, 설정: `GET/PUT .../admin/form-config·booking-rules` |

## 세션/인증
- `super_logged_in: bool` — 슈퍼 로그인 상태
- `place_admins: {slug: bool}` — 기관별 독립 로그인 상태 (한 세션이 여러 기관에 로그인 가능)
- 데코레이터: `super_required`, `place_required`(slug 유효+존재), `place_admin_required`

## 예약 비즈니스 규칙
운영 09:00~18:00(요일별 조정), **1시간 단위 연속 선택**. 공개 신청은 최대 2시간,
하루 1회, 주간(월~일) 최대 2회, 참가 인원 ≥ 1, 활동내용 필수. 취소/거절은 soft delete.

숫자 규칙은 **기관별 설정**(`place_settings.booking_rules`)이며 관리자 '운영 설정'
화면에서 바꿉니다. 기본값은 `config.DEFAULT_BOOKING_RULES`, 허용 범위는
`config.BOOKING_RULE_LIMITS`.

| 키 | 기본 | 의미 |
|---|---|---|
| `booking_min_days` | 3 | 이용 N일 전까지 신청 (당일·임박 신청 차단) |
| `booking_max_days` | 14 | 예약일 기준 최대 N일 뒤까지 (2주) |
| `cancel_deadline_days` | 1 | 신청자가 **직접** 취소 가능한 기한. 이후엔 관리자만 처리 |
| `penalty_months` | 6 | 노쇼·이용확인 미실시 신청인의 재대관 제한 기간 |
| `extension_hours` | 1 | 현장 연장 가능 시간 |

### 이용 결과 & 재대관 제한 (종이 규정 15·16)
`reservations.attendance` — `''`(미처리) / `attended` / `no_show` / `unverified`.
관리자가 대시보드·예약 수정 화면에서 기록합니다. `no_show`·`unverified` 가 있으면
**마지막 위반 이용일 + `penalty_months`** 전까지 같은 이름+연락처의 공개 신청이 막힙니다
(`penalty_until()`). 기록을 `''` 로 되돌리면 제한도 즉시 풀립니다.

### 취소 마감
`cancel()`(신청자 경로)만 마감을 강제합니다. 관리자는 마감 이후에도 예약 수정에서 상태를
바꿔 취소할 수 있습니다. 프론트가 규칙을 중복 구현하지 않도록 응답에 `can_cancel` 과
`cancel_deadline` 을 함께 내려줍니다.

### 정기 고정활동 (recurring_blocks)
매주 같은 시간에 고정으로 잡히는 일정 — 동아리 정기활동뿐 아니라 **센터 프로그램, 시설
점검, 외부 정기대관**도 같은 표에 들어갑니다. `kind` 로 구분합니다.

| kind | 표시 | 활동명에 들어가는 값 |
|---|---|---|
| `club` | 동아리 | 동아리명 (ClubLog 목록에서 선택 또는 직접 입력) |
| `program` | 프로그램 | 프로그램명 |
| `etc` | 기타 | 점검·외부 정기대관 등 자유 입력 (레거시 행의 기본값) |

- **활동명은 필수**입니다. 현황표에 그대로 표시되어 비어 있으면 무슨 일정인지 알 수 없습니다.
  유형에 따라 오류 문구가 달라집니다("동아리명을 입력해주세요" / "프로그램명을 …").
- `GET/POST/PUT/DELETE /api/<slug>/admin/recurring-blocks[/<id>]` — 학기마다 시간이
  바뀌는 일이 잦아 수정(PUT)도 지원합니다.
- day-grid/week-grid 의 `block` 세그먼트에 `kind` 가 함께 내려가 화면에서 유형을 표시합니다.

### 동아리 단기대관 (관리자 직접 추가)
동아리가 정기활동 외에 추가로 쓰는 일정을 신청인 정보 없이 빠르게 확정하는 경로입니다.
`POST /admin/reservations` 에 `club` 만 주고 `name` 을 비우면 **동아리명이 표시명으로**
쓰입니다(현황표·목록에 빈칸이 뜨지 않도록). 중복·정기활동 검사는 그대로 적용됩니다.

동아리 목록은 외부 ClubLog 를 백엔드가 대신 호출합니다 — 프론트에서 직접 부르면 CORS 에
막히고 인증 경계도 흐려지기 때문입니다.
```
GET /api/<slug>/admin/clubs[?refresh=1]
    → {clubs:[{name, category}], available, cached, error}
```
- 응답을 `CLUBLOG_CACHE_TTL`(기본 300초) 동안 프로세스 내 캐싱, `?refresh=1` 로 무시.
- **외부 조회 실패는 200 + `available:false`** 로 내려 화면이 '직접 입력'으로 계속
  진행하게 합니다. 관리자 화면은 항상 직접 입력이 가능합니다.
- 환경변수: `CLUBLOG_API_BASE`, `CLUBLOG_TIMEOUT`, `CLUBLOG_CACHE_TTL`.
- 매주 반복되는 정기활동은 이 경로가 아니라 `recurring_blocks`(운영 설정)에 등록합니다.

### 현장 연장
`POST /admin/reservations/<id>/extend` — 확정 예약에 한해, 운영 종료 시각·뒤이은 예약·
정기 고정활동에 걸리지 않으면 `end_time` 을 `extension_hours` 만큼 늘립니다.

> **명시용(코드로 강제하지 않음)**: "성인은 오후 2시 이전까지만 대관" 은 청소년 시설
> 운영 방침에 대한 현장 안내이므로 안내문에만 표시하고 서버에서 막지 않습니다.

## 신청서 항목 (종이 「시설대관이용신청서」 기준)
`reservations` 는 종이 서식의 칸과 1:1 로 대응합니다.

| 종이 서식 | 컬럼 / 필드 |
|---|---|
| 이름 (나이) | `applicant_name`, `applicant_age` |
| Tel | `applicant_contact` |
| 주소 또는 E-Mail | `applicant_address` |
| 학교/소속 · 동아리 | `applicant_school`, `applicant_club` |
| 활동내용 | `activity` (공개 신청 필수) |
| 이용인원 (연령 × 남/여) | `participant_info` = `{연령대: {male, female, unspecified}}` |
| 필요 물품 (마이크 N대) | `requested_equipment` = `[{name, qty}]` |

- `unspecified` 는 성별 구분 도입 전 데이터를 손실 없이 읽기 위한 레거시 버킷입니다
  (신규 신청은 `male`/`female` 만 채움). 읽기 시 `parse_participants` 가 항상 정규화합니다.
- `requested_equipment` 는 옛 `["앰프", ...]` 문자열 배열도 수량 1 로 읽어들입니다.
- 레거시 DB 는 앱 시작 시 `init_super_db()` 가 기관마다 `init_place_db()` 를 호출해
  컬럼을 자동 추가합니다(멱등).

## 신청서 설정 (기관별, place_settings)
필요 물품 목록·공지·대관규정은 기관마다 다르므로 **코드 상수가 아니라 기관 DB** 에 저장하고
기관 관리자가 화면에서 수정합니다. 기본값은 `config.DEFAULT_EQUIPMENT_CATALOG` /
`DEFAULT_FORM_NOTICE` / `DEFAULT_FORM_RULES` 로 첫 생성 시 시딩됩니다.

```
GET  /api/<slug>/form-config?facility_type=연습실   # 공개 — 해당 유형의 물품 분류만
GET  /api/<slug>/admin/form-config                  # 관리자 — 전체
PUT  /api/<slug>/admin/form-config                  # 보낸 키만 부분 수정
```
물품 분류는 `{title, facility_types[], allow_other, items:[{name, qty}]}` 이며
`facility_types` 가 비면 모든 시설에, `item.qty=true` 면 수량 입력칸이 노출됩니다.

## 테스트
```bash
pip install -r requirements-dev.txt
pytest                 # 200 케이스: 도메인 규칙 + 슈퍼/기관공개/기관관리자 + 테넌트 격리
```
테스트는 `DB_FOLDER` 를 임시 폴더로 지정해 격리 실행합니다.

## 배포 시 반드시 바꿀 것 ⚠️
1. **슈퍼 비밀번호** — 첫 로그인 후 `/super/password` 로 즉시 변경 (또는 `SUPER_PASSWORD` 주입).
2. **기관 관리자 비밀번호** — 슈퍼 페이지에서 기관별로 변경.
3. **`CORS_ORIGINS`** — 프론트 실제 도메인.
4. **쿠키** — cross-site HTTPS 면 `SESSION_COOKIE_SAMESITE=None`, `SESSION_COOKIE_SECURE=true`.
5. **WSGI 서버** — `app.run(debug=True)` 는 개발용. 운영은 gunicorn/waitress 로 `app:app` 서빙.
