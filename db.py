"""SQLite 데이터베이스 — 멀티테넌트 + 자체 부트스트랩.

구조:
  db/super.sqlite3
      places          # 기관 목록 (slug, full_name, short_name, password_hash, 연락처)
      super_admin     # 슈퍼 관리자 비밀번호 해시 (단일 row, id = 1)
      app_settings    # 시스템 설정 (SECRET_KEY 등 key-value)
  db/<slug>.sqlite3
      facilities, reservations   # 기관별 실제 예약 데이터

설계 요점:
  * 한 HTTP 요청 안에서 super DB 와 여러 기관 DB 에 동시에 접근 가능.
    flask.g.dbs 에 {'super': conn, '<slug>': conn, ...} 형태로 캐싱.
  * 첫 실행 시 init_super_db() 가:
      - 슈퍼 비밀번호가 없으면 임시값을 생성해 콘솔에 1회 출력
      - SECRET_KEY 가 없으면 랜덤 생성해 app_settings 에 저장
      - 기관이 하나도 없으면 기본 기관(config.DEFAULT_PLACE_SLUG)을 시설과 함께 시딩
    설정 파일(.env) 없이 동작하는 구조.
"""
import json
import os
import secrets
import sqlite3
from datetime import datetime

from flask import g
from werkzeug.security import generate_password_hash

import config
from config import DB_FOLDER, SUPER_DB_PATH, place_db_path


# ----------------------------------------------------------------------
# 스키마
# ----------------------------------------------------------------------
SUPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT    NOT NULL UNIQUE,
    full_name     TEXT    NOT NULL,     -- 풀네임 (예: 나름청소년활동센터)
    short_name    TEXT    NOT NULL,     -- 축약별칭 (예: 나름)
    password_hash TEXT    NOT NULL,     -- 기관 관리자 비밀번호 해시
    address       TEXT    DEFAULT '',   -- 공개 푸터 표기용 연락처
    phone         TEXT    DEFAULT '',
    email         TEXT    DEFAULT '',
    header_image  TEXT    DEFAULT '',   -- 공개 헤더 로고 (static/<slug>/header.<ext>)
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_places_slug ON places(slug);

-- 슈퍼 관리자는 1명뿐 -> id = 1 만 허용하는 단일 row 테이블.
CREATE TABLE IF NOT EXISTS super_admin (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

-- 시스템 자동 설정 (SECRET_KEY 등). key-value 로 확장 가능.
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 공통 휴무일/공휴일 (전 기관 공유). 슈퍼 관리자가 관리.
--   type='closure' 휴무일(완전 휴무) / type='holiday' 공휴일(기관 설정에 따라 운영)
CREATE TABLE IF NOT EXISTS common_holidays (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    date   TEXT NOT NULL UNIQUE,        -- YYYY-MM-DD
    name   TEXT DEFAULT '',
    type   TEXT NOT NULL DEFAULT 'holiday',
    source TEXT NOT NULL DEFAULT 'manual'   -- 'manual' | 'auto'(한국 공휴일 동기화)
);
"""

PLACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    type        TEXT    NOT NULL,
    capacity    INTEGER,
    description TEXT,
    image_url   TEXT,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS reservations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    access_id           TEXT    UNIQUE,
    facility_id         INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
    applicant_name      TEXT    NOT NULL,
    applicant_contact   TEXT    NOT NULL,
    applicant_school    TEXT,
    applicant_club      TEXT,
    applicant_age       INTEGER,                -- 종이 서식의 '이름 (나이)'
    applicant_address   TEXT,                   -- 종이 서식의 '주소 또는 E-Mail'
    activity            TEXT    NOT NULL DEFAULT '',  -- 활동내용 (예: 춤연습, 밴드합주)
    status              TEXT    NOT NULL DEFAULT 'pending',
    start_time          TEXT    NOT NULL,   -- ISO8601 'YYYY-MM-DDTHH:MM:SS'
    end_time            TEXT    NOT NULL,
    participant_info    TEXT    DEFAULT '{}',  -- JSON 문자열
    requested_equipment TEXT    DEFAULT '[]',  -- JSON 문자열
    is_deleted          INTEGER NOT NULL DEFAULT 0,
    reject_reason       TEXT,
    -- 이용 결과: ''(미처리) | attended | no_show | unverified (규정 15·16 패널티 근거)
    attendance          TEXT    NOT NULL DEFAULT '',
    attendance_at       TEXT,
    created_at          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resv_facility ON reservations(facility_id);
CREATE INDEX IF NOT EXISTS idx_resv_start    ON reservations(start_time);
CREATE INDEX IF NOT EXISTS idx_resv_access   ON reservations(access_id);

-- 요일별 운영시간 (weekday: 0=월 ~ 6=일). 7행 고정, init 시 기본값 시딩.
CREATE TABLE IF NOT EXISTS operating_hours (
    weekday    INTEGER PRIMARY KEY,        -- 0..6 (Mon..Sun)
    is_open    INTEGER NOT NULL DEFAULT 1,
    open_hour  INTEGER NOT NULL DEFAULT 9,
    close_hour INTEGER NOT NULL DEFAULT 18
);

-- 기관별 휴무일/공휴일 (특정 날짜). type='closure'|'holiday'.
CREATE TABLE IF NOT EXISTS closures (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    date   TEXT NOT NULL UNIQUE,           -- YYYY-MM-DD
    reason TEXT DEFAULT '',
    type   TEXT NOT NULL DEFAULT 'closure'
);

-- 슈퍼 공통 휴무일을 이 기관에서 제외(별도 관리)한 날짜.
CREATE TABLE IF NOT EXISTS holiday_excludes (
    date TEXT PRIMARY KEY                  -- YYYY-MM-DD
);

-- 기관 설정 key-value (holiday_operates 등).
CREATE TABLE IF NOT EXISTS place_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 정기 고정활동 (매주 반복, 시설별 점유 → 대관 겹침 방지)
CREATE TABLE IF NOT EXISTS recurring_blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    facility_id INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
    weekday     INTEGER NOT NULL,          -- 0..6 (Mon..Sun)
    start_hour  INTEGER NOT NULL,
    end_hour    INTEGER NOT NULL,
    title       TEXT DEFAULT '',
    -- 활동 유형: club(동아리) | program(센터 프로그램) | etc(점검·외부 정기대관 등)
    kind        TEXT NOT NULL DEFAULT 'etc'
);

CREATE INDEX IF NOT EXISTS idx_block_facility ON recurring_blocks(facility_id);
CREATE INDEX IF NOT EXISTS idx_block_weekday  ON recurring_blocks(weekday);
"""


# ----------------------------------------------------------------------
# 커넥션 헬퍼
# ----------------------------------------------------------------------
def _connect(path: str) -> sqlite3.Connection:
    """주어진 경로의 SQLite 에 연결. Flask 컨텍스트 밖에서도 사용 가능."""
    os.makedirs(DB_FOLDER, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_cached(key: str, path: str) -> sqlite3.Connection:
    """g.dbs 에서 해당 key 의 커넥션을 꺼내거나, 없으면 새로 만들어 캐싱."""
    if "dbs" not in g:
        g.dbs = {}
    if key not in g.dbs:
        g.dbs[key] = _connect(path)
    return g.dbs[key]


def get_super_db() -> sqlite3.Connection:
    """슈퍼 DB 커넥션."""
    return _get_cached("super", SUPER_DB_PATH)


def get_place_db(slug: str) -> sqlite3.Connection:
    """기관 DB 커넥션. 슬러그 유효성/존재 여부는 라우트 데코레이터에서 검증."""
    return _get_cached(slug, place_db_path(slug))


def close_dbs(_=None) -> None:
    """요청 종료 시 열려있는 모든 커넥션을 닫음."""
    dbs = g.pop("dbs", None)
    if dbs:
        for conn in dbs.values():
            try:
                conn.close()
            except Exception:
                pass


# ----------------------------------------------------------------------
# 스키마 초기화 + 첫 실행 부트스트랩
# ----------------------------------------------------------------------
def init_super_db() -> None:
    """앱 시작 시 슈퍼 DB 의 테이블 + 초기값(슈퍼 비밀번호, SECRET_KEY)을 보장.

    멱등 — 이미 값이 있으면 건드리지 않음. 기관이 하나도 없으면 기본 기관을 시딩.
    """
    conn = _connect(SUPER_DB_PATH)
    try:
        conn.executescript(SUPER_SCHEMA)
        _migrate_places_contact_columns(conn)
        # common_holidays 에 source 컬럼이 없으면 추가(레거시).
        ch_cols = {r[1] for r in conn.execute("PRAGMA table_info(common_holidays)").fetchall()}
        if "source" not in ch_cols:
            conn.execute("ALTER TABLE common_holidays ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")

        # 1) 슈퍼 비밀번호가 없으면 임시값 생성 + 콘솔 출력
        row = conn.execute("SELECT id FROM super_admin WHERE id = 1").fetchone()
        if not row:
            temp_password = os.environ.get("SUPER_PASSWORD") or secrets.token_urlsafe(12)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO super_admin (id, password_hash, updated_at) VALUES (1, ?, ?)",
                (generate_password_hash(temp_password), now),
            )
            _print_initial_password_banner(temp_password)

        # 2) SECRET_KEY 가 없으면 랜덤 생성
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'secret_key'"
        ).fetchone()
        if not row:
            secret = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ('secret_key', ?)",
                (secret,),
            )

        conn.commit()

        # 3) 기관이 없으면 기본 기관 + 시설 시딩
        has_place = conn.execute("SELECT 1 FROM places LIMIT 1").fetchone()
        if not has_place:
            _seed_default_place(conn)
        else:
            # 이미 있는 기관 DB 는 여기서만 스키마 마이그레이션 기회를 얻는다
            # (init_place_db 는 멱등이라 매 실행 호출해도 안전).
            for row in conn.execute("SELECT slug FROM places").fetchall():
                init_place_db(row["slug"])
    finally:
        conn.close()


def _migrate_places_contact_columns(conn: sqlite3.Connection) -> None:
    """옛 places 스키마에 연락처 컬럼이 없으면 추가 (멱등)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(places)").fetchall()}
    for col in ("address", "phone", "email", "header_image"):
        if col not in cols:
            conn.execute(f"ALTER TABLE places ADD COLUMN {col} TEXT DEFAULT ''")


def _seed_default_place(super_conn: sqlite3.Connection) -> None:
    """기본 기관(config.DEFAULT_PLACE_*)을 시설과 함께 생성.

    슈퍼 페이지 없이도 기존처럼 바로 예약 서비스를 쓸 수 있게 하는 부트스트랩.
    기관 관리자 임시 비밀번호를 콘솔에 1회 출력.
    """
    slug = config.DEFAULT_PLACE_SLUG
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    temp_password = os.environ.get("DEFAULT_PLACE_PASSWORD") or "manage123"

    super_conn.execute(
        "INSERT INTO places (slug, full_name, short_name, password_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            slug,
            config.DEFAULT_PLACE_FULL_NAME,
            config.DEFAULT_PLACE_SHORT_NAME,
            generate_password_hash(temp_password),
            now,
        ),
    )
    super_conn.commit()

    init_place_db(slug)
    pconn = _connect(place_db_path(slug))
    try:
        # 멱등: 기관 DB 파일이 보존되어 이미 시설이 있으면 재삽입하지 않음
        # (슈퍼 DB 만 초기화된 경우 중복 시딩 방지).
        existing = pconn.execute("SELECT COUNT(*) AS c FROM facilities").fetchone()["c"]
        if existing == 0:
            for f in config.DEFAULT_FACILITIES:
                pconn.execute(
                    "INSERT INTO facilities (name, type, capacity, description, image_url, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (f["name"], f["type"], f.get("capacity"), f.get("description"),
                     f.get("image_url"), now),
                )
            pconn.commit()
    finally:
        pconn.close()

    if not os.environ.get("QUIET_BOOTSTRAP"):
        print(f"[seed] 기본 기관 '{slug}' 생성 (관리자 임시 비밀번호: {temp_password})", flush=True)


def _print_initial_password_banner(password: str) -> None:
    """첫 실행 시 슈퍼 임시 비밀번호를 콘솔에 1회 출력."""
    if os.environ.get("QUIET_BOOTSTRAP"):
        return
    bar = "=" * 64
    print()
    print(bar)
    print("  슈퍼 관리자 초기 비밀번호:")
    print(f"      {password}")
    print()
    print("  이 비밀번호로 첫 로그인한 뒤 즉시 변경하세요.")
    print("  이 메시지는 다시 표시되지 않습니다.")
    print(bar)
    print(flush=True)


def init_place_db(slug: str) -> None:
    """기관 추가 시 해당 기관의 DB 파일 + 스키마를 생성 (멱등).

    운영시간 7행(요일별) 기본값도 멱등하게 시딩(기본: 매일 09~18 운영).
    """
    conn = _connect(place_db_path(slug))
    try:
        conn.executescript(PLACE_SCHEMA)
        # 레거시 마이그레이션: closures 에 type 컬럼이 없으면 추가.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(closures)").fetchall()}
        if "type" not in cols:
            conn.execute("ALTER TABLE closures ADD COLUMN type TEXT NOT NULL DEFAULT 'closure'")
        _migrate_reservation_form_columns(conn)
        # 레거시 마이그레이션: recurring_blocks 에 유형 컬럼이 없으면 추가.
        blk_cols = {r[1] for r in conn.execute("PRAGMA table_info(recurring_blocks)").fetchall()}
        if "kind" not in blk_cols:
            conn.execute("ALTER TABLE recurring_blocks ADD COLUMN kind TEXT NOT NULL DEFAULT 'etc'")
        for wd in range(7):
            conn.execute(
                "INSERT OR IGNORE INTO operating_hours (weekday, is_open, open_hour, close_hour)"
                " VALUES (?, 1, ?, ?)",
                (wd, config.OPEN_HOUR, config.CLOSE_HOUR),
            )
        # 기본 설정: 공휴일에는 휴무(0). 기관이 운영으로 바꾸면 일요일 운영시간 적용.
        conn.execute(
            "INSERT OR IGNORE INTO place_settings (key, value) VALUES ('holiday_operates', '0')"
        )
        _seed_form_settings(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_reservation_form_columns(conn: sqlite3.Connection) -> None:
    """옛 reservations 스키마에 종이 신청서 항목 컬럼이 없으면 추가 (멱등)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reservations)").fetchall()}
    for col, ddl in (
        ("applicant_age", "INTEGER"),
        ("applicant_address", "TEXT"),
        ("activity", "TEXT NOT NULL DEFAULT ''"),
        ("attendance", "TEXT NOT NULL DEFAULT ''"),
        ("attendance_at", "TEXT"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE reservations ADD COLUMN {col} {ddl}")


def _seed_form_settings(conn: sqlite3.Connection) -> None:
    """신청서 설정(필요 물품 목록·공지·대관규정)과 대관 규칙 기본값을 멱등하게 시딩.

    기관마다 장비와 규정이 다르므로 place_settings 에 JSON 으로 저장하고,
    기관 관리자가 '신청서 설정' 화면에서 수정한다.
    """
    defaults = {
        "equipment_catalog": config.DEFAULT_EQUIPMENT_CATALOG,
        "form_notice": config.DEFAULT_FORM_NOTICE,
        "form_rules": config.DEFAULT_FORM_RULES,
        "booking_rules": config.DEFAULT_BOOKING_RULES,
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO place_settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def place_db_exists(slug: str) -> bool:
    """기관 DB 파일이 디스크에 존재하는지."""
    return os.path.exists(place_db_path(slug))


# ----------------------------------------------------------------------
# 시스템 설정 접근자 (app_settings)
# ----------------------------------------------------------------------
def get_secret_key() -> str:
    """Flask SECRET_KEY 를 슈퍼 DB 에서 가져옴. init_super_db() 선행 필요."""
    conn = _connect(SUPER_DB_PATH)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'secret_key'"
        ).fetchone()
        if not row:
            raise RuntimeError(
                "SECRET_KEY 가 초기화되지 않았습니다. init_super_db() 를 먼저 호출하세요."
            )
        return row["value"]
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 디버그용 — 단독 실행하면 슈퍼 DB 초기화
# ----------------------------------------------------------------------
if __name__ == "__main__":
    init_super_db()
    print(f"Super DB ready: {SUPER_DB_PATH}")
