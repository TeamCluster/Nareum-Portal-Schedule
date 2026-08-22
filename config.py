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

# --- .env 로드 -----------------------------------------------------------
# 아래 설정은 전부 os.environ 에서 읽으므로, .env 파일을 쓰려면 여기서 먼저
# 환경으로 올려야 한다. 이미 셸/systemd 로 넣은 값은 덮어쓰지 않는다.
# python-dotenv 가 없으면 조용히 건너뛴다(셸 환경변수만으로도 동작).
#
# LOAD_DOTENV=0 이면 건너뛴다 — 테스트는 개발자 로컬의 .env(운영값일 수 있음)에
# 영향을 받으면 안 되므로 conftest 가 이 값을 끈다.
if os.environ.get("LOAD_DOTENV", "1").strip().lower() not in ("0", "false", "no", "off"):
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)

# --- SQLite 폴더 / 파일 경로 ---------------------------------------------
# 테스트/배포에서 DB_FOLDER 환경변수로 위치를 덮어쓸 수 있음.
DB_FOLDER = os.environ.get("DB_FOLDER") or os.path.join(BASE_DIR, "db")
SUPER_DB_PATH = os.path.join(DB_FOLDER, "super.sqlite3")

# 정적 파일 루트(시설 이미지 업로드 저장 위치). 업로드 이미지는
# static/<slug>/facility/<파일명> 에 저장되고 /static/... 로 서빙된다.
STATIC_ROOT = os.environ.get("STATIC_ROOT") or os.path.join(BASE_DIR, "static")


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
#
# SameSite 는 true/false 가 아니라 Strict / Lax / None 셋 중 하나다. 주변 설정이
# 전부 불리언이라 헷갈리기 쉬운데, 잘못된 값을 넣으면 쿠키를 굽는 시점(=로그인
# 응답)에야 터져서 "모든 로그인이 500" 이 된다. 부팅 때 바로 잡는다.
_SAMESITE_CHOICES = {"strict": "Strict", "lax": "Lax", "none": "None"}
_raw_samesite = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax").strip()
if _raw_samesite.lower() not in _SAMESITE_CHOICES:
    raise ValueError(
        f"SESSION_COOKIE_SAMESITE 값이 잘못되었습니다: {_raw_samesite!r}\n"
        "  Strict / Lax / None 중 하나여야 합니다 (true/false 아님).\n"
        "  같은 오리진(프록시 구성)이면 Lax, 프론트·백 도메인이 다르면 None."
    )
SESSION_COOKIE_SAMESITE = _SAMESITE_CHOICES[_raw_samesite.lower()]
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").strip().lower() == "true"

# SameSite=None 은 브라우저가 Secure 를 요구한다. 둘이 어긋나면 쿠키가 조용히
# 버려져 "로그인은 성공하는데 계속 로그아웃되는" 증상이 된다.
if SESSION_COOKIE_SAMESITE == "None" and not SESSION_COOKIE_SECURE:
    raise ValueError(
        "SESSION_COOKIE_SAMESITE=None 은 SESSION_COOKIE_SECURE=true 가 필요합니다.\n"
        "  (브라우저가 Secure 없는 SameSite=None 쿠키를 거부합니다.)"
    )


# --- 전송 구간 보안 (HTTPS/TLS) -----------------------------------------
# 비밀번호를 포함한 모든 통신을 암호화하기 위한 설정. 저장은 이미 pbkdf2 해시로
# 안전하며, 여기서는 "전송 중" 노출을 막는다.
#
#   운영(HTTPS) 권장값:  FORCE_HTTPS=true  SESSION_COOKIE_SECURE=true
#   개발(HTTP)  기본값:  FORCE_HTTPS=false SESSION_COOKIE_SECURE=false
def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# HTTP 로 들어온 요청을 HTTPS 로 301 리다이렉트한다. 리버스 프록시(nginx 등)
# 뒤에서는 X-Forwarded-Proto 헤더를 신뢰해 판단한다(아래 TRUSTED_PROXY_HOPS).
FORCE_HTTPS = _env_bool("FORCE_HTTPS", False)

# HSTS: 브라우저에게 "이 도메인은 앞으로도 HTTPS 로만 접속" 을 강제(초 단위).
# HTTPS 로 실제 서빙될 때만 헤더를 내보낸다(HTTP 개발 중엔 자동 무시).
# 0 이면 HSTS 미사용. 운영 안정화 후 31536000(1년) 권장.
HSTS_MAX_AGE = int(os.environ.get("HSTS_MAX_AGE", "0"))
HSTS_INCLUDE_SUBDOMAINS = _env_bool("HSTS_INCLUDE_SUBDOMAINS", False)

# 신뢰하는 리버스 프록시 홉 수 (X-Forwarded-For 기준). 프록시가 없으면 0.
#   0  포트포워딩 등 프록시 없음
#   1  프록시 1대 (시놀로지 역방향 프록시만 / Cloudflare Tunnel)
#   2  Cloudflare(주황 구름) + 시놀로지 역방향 프록시
# 이 값은 "실제 클라이언트 IP" 판정에 쓰이며, 로그인 시도 제한이 여기에 의존한다.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))

# ⚠️ Proto/Host 는 홉 수가 다르다.
# nginx(시놀로지 역방향 프록시 포함)는 X-Forwarded-For 는 $proxy_add_x_forwarded_for
# 로 **누적**하지만, X-Forwarded-Proto/Host 는 $scheme/$host 로 **덮어쓴다**.
# 그래서 프록시가 2대여도 Proto 헤더 값은 보통 1개다. 여기에 2 를 적용하면
# ProxyFix 가 값을 못 찾아 request.is_secure 가 False 로 남고,
# FORCE_HTTPS 와 맞물려 **무한 리다이렉트**가 난다.
# 특별한 이유가 없으면 기본값(프록시가 있으면 1)을 그대로 두면 된다.
_default_proto_hops = 1 if TRUSTED_PROXY_HOPS > 0 else 0
TRUSTED_PROTO_HOPS = int(os.environ.get("TRUSTED_PROTO_HOPS", _default_proto_hops))
TRUSTED_HOST_HOPS = int(os.environ.get("TRUSTED_HOST_HOPS", _default_proto_hops))

# 공통 보안 응답 헤더 on/off (기본 on — 끌 이유는 거의 없음).
ENABLE_SECURITY_HEADERS = _env_bool("ENABLE_SECURITY_HEADERS", True)

# --- 스토리지 ------------------------------------------------------------
# DB_FOLDER / STATIC_ROOT 가 앱 디렉터리 안에 있어도 괜찮은 배포(시놀로지 NAS,
# VPS, 바인드 마운트한 컨테이너 등)라면 true 로 두어 부팅 경고를 끈다.
# 경고는 "컨테이너 안 + 앱 디렉터리 안" 일 때만 나오므로 보통은 건드릴 일이 없다.
STORAGE_PERSISTENT = _env_bool("STORAGE_PERSISTENT", False)

# --- 로그인 시도 제한 (무차별 대입 방어) --------------------------------
# 같은 IP 가 관측 창(WINDOW) 안에서 MAX 회 실패하면 LOCKOUT 초 동안 잠근다.
# MAX 를 0 으로 두면 기능이 꺼진다(권장하지 않음).
LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", 10))
LOGIN_WINDOW_SECONDS = int(os.environ.get("LOGIN_WINDOW_SECONDS", 900))    # 15분
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", 900))  # 15분

# --- 업로드 -------------------------------------------------------------
# 요청 본문 전체의 상한. image_service 의 5MB 검사는 파일을 읽은 뒤에 이뤄지므로,
# 그보다 앞단에서 거대한 요청 자체를 끊어야 한다(멀티파트 오버헤드분 여유 포함).
MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 6 * 1024 * 1024))

# --- 예약 도메인 규칙 (프론트와 공유되는 비즈니스 규칙) ------------------
# 예약 가능 기간: 오늘 + MIN_DAYS ~ 오늘 + MAX_DAYS
BOOKING_MIN_DAYS = int(os.environ.get("BOOKING_MIN_DAYS", 3))
BOOKING_MAX_DAYS = int(os.environ.get("BOOKING_MAX_DAYS", 14))

# 기본 운영 시간(기관 생성 시 요일별 operating_hours 기본값). 실제 운영시간은
# 기관 관리자가 요일별로 조정하며, 예약 로직은 그 값을 따른다.
OPEN_HOUR = 9
CLOSE_HOUR = 18

# 관리자가 설정 가능한 운영시간의 절대 허용 범위(시각).
HOUR_ABS_MIN = 6
HOUR_ABS_MAX = 24

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
