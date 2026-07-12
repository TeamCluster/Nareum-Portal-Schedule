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
db/<slug>.sqlite3     facilities · reservations   ← 기관마다 별도 파일로 완전 격리
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
| 기관 공개 | `GET /info·facilities·availability`, `GET .../booked-times`, `POST /reservations`, `GET /reservations/<access_id>`, `POST /reservations/lookup`, `POST /reservations/<id>/cancel` |
| 기관 관리자 | `dashboard·requests·reservations(목록/생성)·reservations/<id>(GET/PUT)·calendar-events·booked-times·approve·reject`, 시설: `GET/POST/PUT/DELETE .../admin/facilities` |

## 세션/인증
- `super_logged_in: bool` — 슈퍼 로그인 상태
- `place_admins: {slug: bool}` — 기관별 독립 로그인 상태 (한 세션이 여러 기관에 로그인 가능)
- 데코레이터: `super_required`, `place_required`(slug 유효+존재), `place_admin_required`

## 예약 비즈니스 규칙 (config.py)
- 예약 가능 기간: 오늘+`BOOKING_MIN_DAYS`(3) ~ 오늘+`BOOKING_MAX_DAYS`(14)
- 운영 09:00~18:00, 연속 시간만 선택
- 공개 신청: 최대 2시간, 하루 1회, 주간(월~일) 최대 2회, 참가 인원 ≥ 1
- 취소/거절은 soft delete (`is_deleted`)

## 테스트
```bash
pip install -r requirements-dev.txt
pytest                 # 70 케이스: 도메인 규칙 + 슈퍼/기관공개/기관관리자 + 테넌트 격리
```
테스트는 `DB_FOLDER` 를 임시 폴더로 지정해 격리 실행합니다.

## 배포 시 반드시 바꿀 것 ⚠️
1. **슈퍼 비밀번호** — 첫 로그인 후 `/super/password` 로 즉시 변경 (또는 `SUPER_PASSWORD` 주입).
2. **기관 관리자 비밀번호** — 슈퍼 페이지에서 기관별로 변경.
3. **`CORS_ORIGINS`** — 프론트 실제 도메인.
4. **쿠키** — cross-site HTTPS 면 `SESSION_COOKIE_SAMESITE=None`, `SESSION_COOKIE_SECURE=true`.
5. **WSGI 서버** — `app.run(debug=True)` 는 개발용. 운영은 gunicorn/waitress 로 `app:app` 서빙.
