"""기관(places) CRUD — 슈퍼 관리자 페이지에서 사용.

DB:  db/super.sqlite3 의 places 테이블

이름은 두 가지로 받음:
  full_name   풀네임 (예: "나름청소년활동센터") — 공개 예약 페이지 상단/푸터
  short_name  축약별칭 (예: "나름") — 관리자 헤더

연락처(address/phone/email)는 공개 푸터 표기용 선택값.

삭제 정책(옵션 B): places 행만 삭제하고 db/<slug>.sqlite3 파일은 보존.
같은 slug 로 재추가하면 이전 예약 데이터가 그대로 복구됨.
"""
import sqlite3
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from config import is_valid_slug
from db import get_super_db, init_place_db

MIN_PASSWORD_LENGTH = 6
MAX_FULL_NAME_LENGTH = 100
MAX_SHORT_NAME_LENGTH = 20

_PUBLIC_COLS = (
    "id, slug, full_name, short_name, address, phone, email, created_at"
)


# ----------------------------------------------------------------------
# 조회
# ----------------------------------------------------------------------
def get_places():
    """기관 목록 (최신순). 비밀번호 해시는 응답에 포함하지 않음."""
    rows = get_super_db().execute(
        f"SELECT {_PUBLIC_COLS} FROM places ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_place(slug: str):
    """단건 조회. 없으면 None."""
    row = get_super_db().execute(
        f"SELECT {_PUBLIC_COLS} FROM places WHERE slug = ?", (slug,)
    ).fetchone()
    return dict(row) if row else None


# ----------------------------------------------------------------------
# 추가
# ----------------------------------------------------------------------
def add_place(slug, full_name, short_name, password,
              address="", phone="", email=""):
    """기관 추가. (성공여부, 메시지, 결과dict) 반환.

    흐름: 검증 -> init_place_db()(파일 생성) -> INSERT.
    """
    if not is_valid_slug(slug):
        return False, (
            "슬러그(영문식별자)는 영문 소문자로 시작하는 2~30자여야 합니다. "
            "허용 문자: 소문자, 숫자, 하이픈(-), 언더스코어(_). "
            "예약어(super/api/admin/static/public/manage)는 사용 불가."
        ), None

    full_name = (full_name or "").strip()
    short_name = (short_name or "").strip()
    if not full_name:
        return False, "기관 풀네임을 입력해주세요.", None
    if not short_name:
        return False, "기관 축약별칭을 입력해주세요.", None
    if len(full_name) > MAX_FULL_NAME_LENGTH:
        return False, f"기관 풀네임은 {MAX_FULL_NAME_LENGTH}자 이내여야 합니다.", None
    if len(short_name) > MAX_SHORT_NAME_LENGTH:
        return False, f"기관 축약별칭은 {MAX_SHORT_NAME_LENGTH}자 이내여야 합니다.", None

    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return False, f"기관 관리자 비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.", None

    # DB 파일 + 스키마 보장 (멱등)
    init_place_db(slug)

    db = get_super_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute(
            "INSERT INTO places"
            " (slug, full_name, short_name, password_hash, address, phone, email, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (slug, full_name, short_name, generate_password_hash(password),
             (address or "").strip(), (phone or "").strip(), (email or "").strip(), now),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return False, f"슬러그 '{slug}' 는 이미 사용 중입니다.", None

    return True, f"기관 '{full_name}' 이(가) 추가되었습니다.", get_place(slug)


# ----------------------------------------------------------------------
# 수정 (기관 정보 / 연락처)
# ----------------------------------------------------------------------
def update_place_info(slug, full_name=None, short_name=None,
                      address=None, phone=None, email=None):
    """기관 표시명/연락처 수정. 전달된 필드만 갱신. (성공여부, 메시지) 반환."""
    place = get_place(slug)
    if not place:
        return False, "해당 기관을 찾을 수 없습니다."

    fields, values = [], []
    if full_name is not None:
        full_name = full_name.strip()
        if not full_name:
            return False, "기관 풀네임을 비울 수 없습니다."
        if len(full_name) > MAX_FULL_NAME_LENGTH:
            return False, f"기관 풀네임은 {MAX_FULL_NAME_LENGTH}자 이내여야 합니다."
        fields.append("full_name = ?"); values.append(full_name)
    if short_name is not None:
        short_name = short_name.strip()
        if not short_name:
            return False, "기관 축약별칭을 비울 수 없습니다."
        if len(short_name) > MAX_SHORT_NAME_LENGTH:
            return False, f"기관 축약별칭은 {MAX_SHORT_NAME_LENGTH}자 이내여야 합니다."
        fields.append("short_name = ?"); values.append(short_name)
    for col, val in (("address", address), ("phone", phone), ("email", email)):
        if val is not None:
            fields.append(f"{col} = ?"); values.append(val.strip())

    if not fields:
        return False, "변경할 내용이 없습니다."

    values.append(slug)
    db = get_super_db()
    db.execute(f"UPDATE places SET {', '.join(fields)} WHERE slug = ?", values)
    db.commit()
    return True, "기관 정보가 수정되었습니다."


# ----------------------------------------------------------------------
# 삭제 (옵션 B — DB 파일 보존)
# ----------------------------------------------------------------------
def delete_place(slug: str):
    """기관 삭제. places 행만 삭제, db/<slug>.sqlite3 파일은 보존."""
    db = get_super_db()
    cur = db.execute("DELETE FROM places WHERE slug = ?", (slug,))
    db.commit()
    if cur.rowcount == 0:
        return False, "해당 기관을 찾을 수 없습니다."
    return True, (
        f"기관 '{slug}' 이(가) 삭제되었습니다. "
        f"데이터 파일은 보존되어 있어 같은 슬러그로 재추가하면 복구됩니다."
    )


# ----------------------------------------------------------------------
# 비밀번호 변경 / 검증
# ----------------------------------------------------------------------
def update_place_password(slug: str, new_password: str):
    if not isinstance(new_password, str) or len(new_password) < MIN_PASSWORD_LENGTH:
        return False, f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
    db = get_super_db()
    cur = db.execute(
        "UPDATE places SET password_hash = ? WHERE slug = ?",
        (generate_password_hash(new_password), slug),
    )
    db.commit()
    if cur.rowcount == 0:
        return False, "해당 기관을 찾을 수 없습니다."
    return True, "기관 관리자 비밀번호가 변경되었습니다."


def verify_place_password(slug: str, password: str) -> bool:
    if not isinstance(password, str) or not password:
        return False
    row = get_super_db().execute(
        "SELECT password_hash FROM places WHERE slug = ?", (slug,)
    ).fetchone()
    if not row:
        return False
    return check_password_hash(row["password_hash"], password)
