# 인수인계 문서 — 백엔드 (Nareum-Portal-Schedule)

> 나름청소년활동센터 대관 예약 시스템의 **Flask REST API** 백엔드.
> 이 문서는 프로젝트를 이어받는 개발자를 위한 배경·설계 결정·주의사항 정리입니다.
> 실행/엔드포인트 요약은 [README.md](README.md) 참고.

## 1. 프로젝트 개요
- 청소년활동센터의 활동실 5곳을 온라인으로 대관 신청/승인 관리하는 시스템.
- **사용자 흐름**: 날짜 선택 → 시설별 예약 현황 확인 → 대관 신청(승인 대기) → 관리자 승인 시 확정 → 사용자 예약 조회/취소.
- **관리자 흐름**: 로그인 → 대시보드(승인 대기·일자별 현황·주간 캘린더) → 승인/거절, 직접 추가, 수정.

## 2. 리팩토링 배경 (무엇이 바뀌었나)
기존에는 **Flask + Jinja 템플릿 + 바닐라 JS**로 된 단일 애플리케이션이었으나,
백엔드(API)와 프론트엔드(React SPA)를 분리했습니다.

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| 응답 | Jinja `render_template` (HTML) | 전부 JSON REST API |
| 라우팅 | `app.py` 한 파일에 전 라우트 | `create_app()` 팩토리 + `api/public.py`·`api/admin.py` 블루프린트 |
| 검증 로직 | 각 뷰에 중복 산재 | `api/helpers.py`로 공통화 |
| 관리자 접근 | `@app.before_request` 경로 검사 | `@admin_required` 데코레이터 (401 반환) |
| 화면 | `templates/`, `static/js·css` | **삭제** (프론트엔드 저장소로 이동) |
| 프론트 연동 | 없음(서버 렌더링) | CORS + 세션 쿠키(credentials) |

제거된 것: `templates/` 전체, `static/js/`, `static/css/`.
유지된 것: `static/img/`(시설 이미지 — 프론트가 절대경로로 참조), `models/`.

## 3. 아키텍처 & 설계 결정
- **App factory 패턴**: `create_app(config_class)` — 테스트/배포 시 설정 주입 용이. `app = create_app()` 전역 인스턴스는 WSGI 진입점.
- **설정은 전부 환경변수**: [config.py](config.py) `Config` 클래스. `.env`(로컬) 또는 실제 환경변수로 오버라이드. `.env.example` 참고.
- **DB는 SQLite 기본, 교체 가능**: `DATABASE_URL`만 바꾸면 Postgres 등으로 전환. 단, 모델의 `db.JSON` 컬럼(`participant_info`, `requested_equipment`)은 Postgres에서 `JSONB`로 자연스럽게 매핑되지만 SQLite에선 텍스트로 저장됨(동작엔 지장 없음).
- **인증은 세션 쿠키**: `MANAGE_PASSWORD` 단일 비밀번호 → `session['manage_logged_in']=True`. 사용자 계정 개념은 없음(원본 그대로). 프론트는 `credentials: include`로 쿠키 전송.
- **에러 처리 일원화**: 도메인 로직에서 `ApiError(message, status)`를 raise → `app.errorhandler(ApiError)`가 `{"error": message}` + 상태코드로 변환. 프론트는 이 `error` 필드를 사용자에게 그대로 노출.

## 4. 예약 도메인 규칙 (핵심 — [api/helpers.py](api/helpers.py))
> 규칙을 바꾸려면 대부분 helpers.py와 config.py만 손대면 됩니다.

- **예약 가능 기간**: 오늘+`BOOKING_MIN_DAYS`(기본 3) ~ 오늘+`BOOKING_MAX_DAYS`(기본 14). `booking_window()`.
- **운영 시간**: `OPEN_HOUR=9` ~ `CLOSE_HOUR=18` (예약 시작 시각 09~17시).
- **연속 시간만**: `resolve_hours()`가 정렬 후 `end-start == len` 검사.
- **공개 신청 제한**: 최대 2시간(`MAX_HOURS_PUBLIC`), 같은 이름+연락처로 하루 1회, 주간(월~일) 최대 2회(`WEEKLY_LIMIT`), 참가 인원 총합 ≥ 1.
- **관리자 직접 추가/수정**: 위 공개 제한 없음(0명 허용, 2시간 초과 허용). 단 시설/시간 **중복(overlap)만** 차단.
- **활성 상태**: `ACTIVE_STATUSES = ["confirmed","pending"]` — 이 상태만 슬롯을 점유. 중복/현황 계산은 모두 이 기준.
- **soft delete**: 취소/거절은 삭제하지 않고 `is_deleted=True` + status 변경. 목록/조회는 `is_deleted==False` 필터. `reject_reason`에 거절 사유 저장.

## 5. 데이터 모델 ([models/__init__.py](models/__init__.py))
- `Facility`: 시설. `type`에 "연습"이 포함되면 프론트에서 장비 선택 UI 노출.
- `Reservation`: 예약. `access_id`(UUID)는 완료 페이지 공개 조회용 키. `participant_info`(dict), `requested_equipment`(list)는 JSON 컬럼.
- `to_dict()`로 직렬화. `Reservation.to_dict(include_facility=True)`는 중첩 시설 정보 포함.

## 6. 마이그레이션 & 시딩 ([app.py](app.py) `init_db`)
- `python app.py` 실행 시 `init_db(app)` 호출 → `db.create_all()` + 레거시 컬럼 추가(`access_id`, `is_deleted`, `reject_reason`) idempotent 실행 + `access_id` 백필 + 시설 5개 시딩(비어 있을 때만).
- **주의**: 정식 마이그레이션 도구(Alembic/Flask-Migrate)는 없음. 스키마 변경 시 `init_db`의 `ALTER TABLE` 블록에 추가하거나, 규모가 커지면 Flask-Migrate 도입 권장.
- DB 파일은 `instance/resv.db`에 생성됨(Flask instance 폴더). 초기화하려면 이 파일 삭제 후 재실행.

## 7. 배포 시 반드시 바꿀 것 ⚠️
1. **`SECRET_KEY`** — 기본값 `dev-secret-change-me`. 세션 위조 방지를 위해 랜덤 문자열 필수.
2. **`MANAGE_PASSWORD`** — 기본값 `manage123`. 관리자 비밀번호.
3. **`CORS_ORIGINS`** — 프론트 실제 도메인으로 설정.
4. **쿠키 설정** — 프론트가 다른 도메인(cross-site)이면 `SESSION_COOKIE_SAMESITE=None`, `SESSION_COOKIE_SECURE=true`(HTTPS 필수). 같은 도메인/리버스프록시면 기본 `Lax`로 충분.
5. **WSGI 서버** — `app.run(debug=True)`는 개발용. 운영은 gunicorn/waitress 등으로 `app:app` 서빙.

## 8. 알려진 한계 / 향후 과제 (TODO)
- [ ] 관리자 인증이 단일 비밀번호 → 실 운영 시 사용자별 계정/역할 고려.
- [ ] 정식 DB 마이그레이션(Flask-Migrate) 미도입.
- [ ] 예약 신청 시 서버 측 rate limiting/reCAPTCHA 없음(무분별 신청 방지 필요 시 추가).
- [ ] 이메일/문자 알림(승인·거절 시) 없음.
- [ ] 자동화 테스트 부재 — `pytest` + `app.test_client()`로 helpers 규칙부터 커버 권장.
- [ ] `overlap`/`same_day`/`weekly` 검사에 트랜잭션 잠금이 없어 동시 요청 시 극단적으로 이중 예약 가능(현재 트래픽 규모에선 사실상 무해).

## 9. 프론트엔드와의 계약
- 프론트 저장소: `../Nareum-Portal-Schedule-FE` (그쪽 [HANDOVER.md] 참고).
- 개발 시 프론트는 Vite 프록시로 `/api`·`/static`을 이 백엔드(:5000)로 전달 → same-origin으로 세션 쿠키 동작.
- **API 응답 형태를 바꾸면** 프론트 `src/api/types.ts`도 함께 수정해야 함(타입 계약).
