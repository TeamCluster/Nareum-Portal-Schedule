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
    status              TEXT    NOT NULL DEFAULT 'pending',
    start_time          TEXT    NOT NULL,   -- ISO8601 'YYYY-MM-DDTHH:MM:SS'
    end_time            TEXT    NOT NULL,
    participant_info    TEXT    DEFAULT '{}',  -- JSON 문자열
    requested_equipment TEXT    DEFAULT '[]',  -- JSON 문자열
    is_deleted          INTEGER NOT NULL DEFAULT 0,
    reject_reason       TEXT,
    created_at          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resv_facility ON reservations(facility_id);
CREATE INDEX IF NOT EXISTS idx_resv_start    ON reservations(start_time);
CREATE INDEX IF NOT EXISTS idx_resv_access   ON reservations(access_id);
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
    finally:
        conn.close()


def _migrate_places_contact_columns(conn: sqlite3.Connection) -> None:
    """옛 places 스키마에 연락처 컬럼이 없으면 추가 (멱등)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(places)").fetchall()}
    for col in ("address", "phone", "email"):
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
    """기관 추가 시 해당 기관의 DB 파일 + 스키마를 생성 (멱등)."""
    conn = _connect(place_db_path(slug))
    try:
        conn.executescript(PLACE_SCHEMA)
        conn.commit()
    finally:
        conn.close()


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
