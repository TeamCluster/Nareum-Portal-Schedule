"""애플리케이션 설정 (멀티테넌트).

데이터 구조:
  db/super.sqlite3       # 슈퍼 관리자 — places + super_admin + app_settings
  db/<slug>.sqlite3      # 기관마다 별도 파일 (예: nareum.sqlite3) — facilities + reservations

비밀번호 / 비밀키:
  - 슈퍼 관리자 비밀번호 → super.sqlite3 의 super_admin 테이블 (해시, 변경 가능)
  - 기관별 관리자 비밀번호 → super.sqlite3 의 places 테이블 (해시, 변경 가능)
  - Flask SECRET_KEY     → super.sqlite3 의 app_settings 테이블 (첫 실행 시 자동 생성)

설정 파일(.env)이 필수는 아닙니다. 첫 실행 시 콘솔에 슈퍼 임시 비밀번호가
1회 출력되니, 그것으로 첫 로그인 후 즉시 변경하세요. (CORS 도메인 등 배포
환경값만 환경변수로 덮어쓰면 됩니다.)
"""
import os
import re

# 백엔드 디렉터리의 절대 경로
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# --- SQLite 폴더 / 파일 경로 ---------------------------------------------
# 테스트/배포에서 DB_FOLDER 환경변수로 위치를 덮어쓸 수 있음.
DB_FOLDER = os.environ.get("DB_FOLDER") or os.path.join(BASE_DIR, "db")
SUPER_DB_PATH = os.path.join(DB_FOLDER, "super.sqlite3")


def place_db_path(slug: str) -> str:
    """기관 slug 로부터 해당 기관의 DB 파일 경로 반환."""
    return os.path.join(DB_FOLDER, f"{slug}.sqlite3")


# --- 슬러그 검증 ---------------------------------------------------------
# URL 경로 + DB 파일명에 함께 쓰이므로 안전한 문자만 허용.
SLUG_REGEX = re.compile(r"^[a-z][a-z0-9_-]{1,29}$")

# URL 경로 / 시스템 용어와 충돌할 가능성이 있는 식별자는 차단.
RESERVED_SLUGS = frozenset({"super", "api", "admin", "static", "public", "manage"})


def is_valid_slug(slug: str) -> bool:
    """슬러그가 형식 + 예약어 규칙을 모두 통과하는지 검증."""
    if not isinstance(slug, str):
        return False
    if slug in RESERVED_SLUGS:
        return False
    return bool(SLUG_REGEX.match(slug))


# --- 서비스 표시명 -------------------------------------------------------
SERVICE_NAME = os.environ.get("SERVICE_NAME", "나름센터 활동실 대관")

# --- 프론트엔드 (CORS 허용 도메인, 콤마 구분) ----------------------------
# 운영에서 도메인이 다르면 환경변수 CORS_ORIGINS 로 덮어쓰세요.
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

# --- 세션 쿠키 -----------------------------------------------------------
# 크로스사이트 HTTPS 배포면 SESSION_COOKIE_SAMESITE=None, SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

# --- 예약 도메인 규칙 (프론트와 공유되는 비즈니스 규칙) ------------------
# 예약 가능 기간: 오늘 + MIN_DAYS ~ 오늘 + MAX_DAYS
BOOKING_MIN_DAYS = int(os.environ.get("BOOKING_MIN_DAYS", 3))
BOOKING_MAX_DAYS = int(os.environ.get("BOOKING_MAX_DAYS", 14))

# 운영 시간: 예약 시작 가능 시각은 OPEN_HOUR .. CLOSE_HOUR-1
OPEN_HOUR = 9
CLOSE_HOUR = 18

# 공개 신청 제한
MAX_HOURS_PUBLIC = 2   # 1회 최대 예약 시간
WEEKLY_LIMIT = 2       # 같은 신청인(이름+연락처)의 주간(월~일) 최대 예약 횟수

# 슬롯을 점유하는(신규 예약을 막는) 상태
ACTIVE_STATUSES = ("confirmed", "pending")

# --- 기본 기관 시딩 (첫 실행 시 places 가 비어 있으면 생성) --------------
# 기존 단일 기관 운영을 그대로 잇기 위한 기본 기관. 슈퍼 페이지에서 추가/삭제 가능.
DEFAULT_PLACE_SLUG = os.environ.get("DEFAULT_PLACE_SLUG", "nareum")
DEFAULT_PLACE_FULL_NAME = os.environ.get("DEFAULT_PLACE_FULL_NAME", "나름청소년활동센터")
DEFAULT_PLACE_SHORT_NAME = os.environ.get("DEFAULT_PLACE_SHORT_NAME", "나름")

# 기본 기관에 시딩할 시설 5종 (기존 데이터 계승)
DEFAULT_FACILITIES = [
    {"name": "활력충전터", "type": "연습실", "image_url": "/static/img/room001.jpg", "description": "밴드/음악 연습 공간"},
    {"name": "창의키움터", "type": "활동실", "image_url": "/static/img/room002.jpg", "description": "과학 활동 공간"},
    {"name": "탐구개발터", "type": "활동실", "image_url": "/static/img/room003.jpg", "description": "3D 프린터가 있는 오픈LAB실"},
    {"name": "상상이룸터", "type": "연습실", "image_url": "/static/img/room004.jpg", "description": "댄스 연습 특화 공간"},
    {"name": "생각나눔터", "type": "회의실", "image_url": "/static/img/room005.jpg", "description": "회의 공간"},
]
