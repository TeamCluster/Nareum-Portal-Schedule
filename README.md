# Nareum-Portal-Schedule (Backend API)

나름청소년활동센터 대관 예약 시스템의 **Flask REST API** 백엔드입니다.
프론트엔드는 별도 저장소(`Nareum-Portal-Schedule-FE`, React + Vite)에서 이 API를 소비합니다.

## 스택
- Flask (app factory + Blueprints)
- Flask-SQLAlchemy (기본 SQLite, `DATABASE_URL`로 교체 가능)
- Flask-Cors (세션 쿠키 기반 인증, credentials 허용)

## 실행
```bash
pip install -r requirements.txt
cp .env.example .env   # 값 조정 (SECRET_KEY, MANAGE_PASSWORD 등)
python app.py          # http://127.0.0.1:5000
```
최초 실행 시 테이블 생성 · 경량 마이그레이션 · 기본 시설 5개 시딩이 자동 수행됩니다.

## 테스트
```bash
pip install -r requirements-dev.txt
pytest                 # tests/ 하위 pytest 스위트 (인메모리 SQLite)
```
`tests/`는 예약 도메인 규칙(helpers)과 공개·관리자 API 흐름을 커버합니다(53 케이스).

## 구조
```
app.py            # create_app() 팩토리 + init_db() 시딩/마이그레이션
config.py         # 환경변수 기반 설정
models/__init__.py# Facility, Reservation 모델 + to_dict()
api/helpers.py    # 예약 도메인 로직(예약 가능 기간, 시간 검증, 중복 확인 등)
api/public.py     # 공개 엔드포인트 (/api/*)
api/admin.py      # 관리자 엔드포인트 (/api/admin/*, 세션 필요)
static/img/       # 시설 이미지 (프론트가 절대경로로 참조)
```

## 주요 엔드포인트
### 공개 (`/api`)
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/facilities` | 시설 목록 |
| GET | `/availability?date=YYYY-MM-DD` | 날짜별 시설 예약 현황 그리드 |
| GET | `/facilities/<id>/booked-times?date=` | 해당 시설/날짜의 예약된 시간 |
| POST | `/reservations` | 예약 신청 (검증 포함) |
| GET | `/reservations/<access_id>` | 예약 완료 상세 |
| POST | `/reservations/lookup` | 이름+연락처로 조회 |
| POST | `/reservations/<id>/cancel` | 예약 취소 |
| GET | `/config`, `/health` | 서비스 설정 / 헬스체크 |

### 관리자 (`/api/admin`, 세션 필요)
`login`, `logout`, `me`, `dashboard`, `requests`, `reservations`(목록/생성),
`reservations/<id>`(상세/PUT 수정), `calendar-events`, `booked-times`,
`reservations/<id>/approve`, `reservations/<id>/reject`.

## 예약 비즈니스 규칙
- 예약 가능 기간: 오늘+`BOOKING_MIN_DAYS`(3) ~ 오늘+`BOOKING_MAX_DAYS`(14)
- 운영 시간 09:00~18:00, 연속된 시간만 선택 가능
- 공개 신청: 최대 2시간, 하루 1회, 주간(월~일) 최대 2회, 참가 인원 ≥ 1
- 취소/거절은 soft delete (`is_deleted`) 처리
