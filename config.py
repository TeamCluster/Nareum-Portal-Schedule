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
SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"


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

# 신뢰하는 리버스 프록시 홉 수. 1 이상이면 ProxyFix 로 X-Forwarded-Proto/Host 를
# 신뢰해 request.is_secure / Secure 쿠키가 올바로 동작. 프록시가 없으면 0.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))

# 공통 보안 응답 헤더 on/off (기본 on — 끌 이유는 거의 없음).
ENABLE_SECURITY_HEADERS = _env_bool("ENABLE_SECURITY_HEADERS", True)

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
MAX_HOURS_PUBLIC = 2   # 1회 최대 예약 시간 (1시간 단위로 자유 선택)
WEEKLY_LIMIT = 2       # 같은 신청인(이름+연락처)의 주간(월~일) 최대 예약 횟수

# 슬롯을 점유하는(신규 예약을 막는) 상태
ACTIVE_STATUSES = ("confirmed", "pending")

# --- 이용 결과(attendance) — 종이 규정 15·16 의 6개월 제한 근거 --------------
#   ''          아직 처리 전
#   attended    이용 후 이용확인 완료 (정상)
#   no_show     취소 신청 없이 미사용 (규정 15)
#   unverified  이용했으나 이용확인을 받지 않음 (규정 16)
ATTENDANCE_VALUES = ("", "attended", "no_show", "unverified")

# 위 값 중 재대관 제한(패널티)을 발생시키는 것.
PENALTY_ATTENDANCE = ("no_show", "unverified")

# --- 대관 규칙 기본값 (기관별로 place_settings 의 booking_rules 에 저장) -----
# 기관마다 운영 방침이 다르므로 DB 에 저장하고 '운영 설정' 화면에서 수정한다.
# 여기 값은 첫 생성 시 시딩되는 기본값(나름청소년활동센터 기준).
DEFAULT_BOOKING_RULES = {
    # 예약 가능 기간: 오늘 + min ~ 오늘 + max (max=14 → 2주)
    "booking_min_days": BOOKING_MIN_DAYS,
    "booking_max_days": BOOKING_MAX_DAYS,
    # 이용일 기준 며칠 전까지 신청자가 직접 취소할 수 있는지 (1 = 하루 전까지)
    "cancel_deadline_days": 1,
    # 노쇼/이용확인 미실시 시 재대관을 막는 기간(개월)
    "penalty_months": 6,
    # 2시간 이용 후 뒤에 예약이 없을 때 현장에서 연장 가능한 시간
    "extension_hours": 1,
}

# 규칙 값의 허용 범위 (관리자 입력 검증용).
BOOKING_RULE_LIMITS = {
    "booking_min_days": (0, 60),
    "booking_max_days": (1, 365),
    "cancel_deadline_days": (0, 30),
    "penalty_months": (0, 60),
    "extension_hours": (0, 6),
}

# --- 정기 고정활동 유형 ---------------------------------------------------
# 동아리 정기활동뿐 아니라 센터 프로그램·시설 점검·외부 정기대관 등도 같은 표에
# 들어가므로 유형을 구분한다. 'etc' 는 레거시 행의 기본값이기도 하다.
RECURRING_KINDS = ("club", "program", "etc")
RECURRING_KIND_LABELS = {"club": "동아리", "program": "프로그램", "etc": "기타"}

# --- 외부 동아리 서비스 (ClubLog) ----------------------------------------
# 관리자 '동아리 단기대관' 추가 시 동아리 목록을 여기서 가져온다(백엔드 프록시).
# 조회 실패는 치명적이지 않다 — 관리자는 동아리명을 직접 입력할 수 있다.
CLUBLOG_API_BASE = os.environ.get(
    "CLUBLOG_API_BASE", "https://clublog-api.team-cluster.kr"
).rstrip("/")
CLUBLOG_TIMEOUT = float(os.environ.get("CLUBLOG_TIMEOUT", "5"))
CLUBLOG_CACHE_TTL = int(os.environ.get("CLUBLOG_CACHE_TTL", "300"))  # 초
CLUBLOG_MAX_BYTES = 512 * 1024

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


# --- 신청서 항목 (종이 「시설대관이용신청서」 기준) ------------------------
# 이용인원 연령 구분. 각 구분마다 남/여를 따로 받는다(종이 서식과 동일).
PARTICIPANT_BANDS = ("elementary", "middle", "high", "teen", "adult")

# 성별 키. 'unspecified' 는 성별 구분 도입 이전에 저장된 예약을 손실 없이
# 읽기 위한 레거시 버킷이며, 신규 신청에서는 사용하지 않는다.
PARTICIPANT_GENDERS = ("male", "female", "unspecified")

# --- 기관별 기본 시드값 (place_settings 에 저장 후 기관 관리자가 수정) ----
# 아래 3개는 기관마다 다르므로 DB(place_settings)에 저장하고 관리자 화면에서
# 편집한다. 여기 값은 첫 생성 시 시딩되는 기본값(나름청소년활동센터 서식 기준).

# 필요 물품 목록. group.facility_types 가 비어 있으면 모든 시설에 노출된다.
# item.qty=True 면 수량 입력칸이 함께 표시된다(예: 마이크 ( )대).
DEFAULT_EQUIPMENT_CATALOG = [
    {
        "title": "음악",
        "facility_types": ["연습실", "공연장", "스튜디오"],
        "allow_other": True,
        "items": [
            {"name": "앰프(스탠다드)"}, {"name": "앰프(일렉)"},
            {"name": "앰프(어쿠스틱)"}, {"name": "앰프(베이스)"},
            {"name": "기타(일렉)"}, {"name": "기타(어쿠스틱)"}, {"name": "기타(베이스)"},
            {"name": "스피커(메인)"}, {"name": "스피커(모니터)"},
            {"name": "풋스위치"}, {"name": "키보드"}, {"name": "드럼"}, {"name": "이펙터"},
            {"name": "마이크", "qty": True},
        ],
    },
    {
        "title": "댄스 · 강의 등",
        "facility_types": [],
        "allow_other": True,
        "items": [
            {"name": "컴퓨터"}, {"name": "앰프"}, {"name": "빔 프로젝터"}, {"name": "TV"},
            {"name": "마이크", "qty": True},
        ],
    },
]

# 신청 화면에 표시되는 「공지 및 준수사항」 (종이 앞면 공통사항).
DEFAULT_FORM_NOTICE = [
    "당일 대관 불가, 최소 이용 3일 전까지 신청 필수",
    "대관 예약은 예약일 기준 최대 2주까지 가능",
    "대관 시간 전 이용 승인 필요, 20분 경과 시 취소",
    "1팀당 2시간, 일주일에 2회 대관 가능 (이용 시간은 1시간 단위로 선택)",
    "2시간 사용 이후 뒤이은 대관예약이 없을 시 현장에서 1시간 연장 가능",
    "사용 신청서 장비만 사용 가능",
    "모든 기기 사용 후 데이터 삭제 및 주변정리 필수",
    "시설 내 식·음료 반입 불가",
    "시설 검사 후 귀가 (기기 파손 시 본인 책임)",
    "대관 취소는 이용 1일 전까지 가능 (이후에는 담당자에게 문의)",
    "취소 신청 없이 미사용하거나 이용확인을 받지 않으면 6개월간 대관 불가",
    "기타 문의 사항은 담당자에게 문의",
    "이용시간 : 화~금 09:00~20:00 / 토~일 09:00~18:00 (※ 월요일 휴관)",
]

# 신청 화면에 표시되는 「대관 규정 및 유의사항」 (종이 뒷면).
DEFAULT_FORM_RULES = [
    "대관 1일 전까지 취소(변경) 신청서를 제출하시기 바랍니다.",
    "시설사용 후 이용시설을 원상복구 후, 반드시 담당자의 이용확인을 받으신 후 이용확인대장에 서명하셔야 합니다.",
    "미풍양속을 해할 우려가 있을 때, 특정 정당 및 종교의 목적을 가지고 있을 때, 기타 부적당하다고 인정될 때는 이용이 제한됩니다.",
    "매주 월요일은 휴관일로 대관이 불가합니다.",
    "대관은 동아리 청소년들 및 센터 프로그램에 우선 배정됩니다.",
    "당일 대관은 받지 않습니다. 최소 이용 3일 전까지 신청해주세요.",
    "대관 사용은 1팀당 2시간, 일주일에 2번 사용가능하며, 이용 시간은 1시간 단위로 선택하실 수 있습니다.",
    "대관예약은 예약일 기준 최대 2주까지 예약이 가능하며, 예약은 하루씩만 가능합니다.",
    "청소년 시설이므로 성인의 경우 오후 2시 이전까지만 대관이 가능합니다. (현장 안내 사항)",
    "대관 시간 전에 이용확인을 받으셔야 하며, 20분이 경과하면 취소 처리됩니다.",
    "개인물품(기타, 스틱, 노트북, 문구류 등)은 개인 및 단체에서 준비하셔야 합니다.",
    "신청자 외 다른 사람은 사용하실 수 없습니다.",
    "시설사용신청서에 신청한 장비만 사용가능하며, 사용 후에는 원상복구 및 반납하셔야 합니다.",
    "2시간 이용 후 뒤이은 대관예약이 없는 경우, 현장에서 담당자 확인을 거쳐 1시간 연장하실 수 있습니다.",
    "시설물 파손 시 전액 배상하여야 합니다.",
    "시설 내에서는 식·음료를 반입할 수 없습니다.",
    "외벽에 부착물을 붙일 경우 담당직원에게 문의 바랍니다.",
    "대관 신청 후 취소신청 없이 사용하지 않는 경우는 6개월간 대관이 불가능합니다. (단, 천재지변 등 제외)",
    "시설이용 후 이용확인을 받지 않는 경우 6개월간 대관이 불가합니다.",
]
