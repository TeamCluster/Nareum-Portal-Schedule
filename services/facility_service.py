"""시설(facilities) CRUD 및 조회 — 기관 DB(db/<slug>.sqlite3).

기관 관리자가 자기 기관의 시설을 관리하고, 공개 페이지가 시설 목록을 읽는다.
모든 함수는 기관 커넥션(db.get_place_db(slug))을 받는다.
"""
from datetime import datetime

from . import image_service
from .errors import ApiError

MAX_NAME_LENGTH = 100


def _to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "capacity": row["capacity"],
        "description": row["description"],
        "image_url": row["image_url"],
    }


def get_facilities(conn):
    rows = conn.execute(
        "SELECT id, name, type, capacity, description, image_url"
        " FROM facilities ORDER BY id"
    ).fetchall()
    return [_to_dict(r) for r in rows]


def get_facility(conn, facility_id):
    row = conn.execute(
        "SELECT id, name, type, capacity, description, image_url"
        " FROM facilities WHERE id = ?",
        (facility_id,),
    ).fetchone()
    return _to_dict(row) if row else None


def add_facility(conn, data):
    name = (data.get("name") or "").strip()
    ftype = (data.get("type") or "").strip()
    if not name:
        raise ApiError("시설 이름을 입력해주세요.")
    if not ftype:
        raise ApiError("시설 유형을 입력해주세요.")
    if len(name) > MAX_NAME_LENGTH:
        raise ApiError(f"시설 이름은 {MAX_NAME_LENGTH}자 이내여야 합니다.")

    capacity = data.get("capacity")
    try:
        capacity = int(capacity) if capacity not in (None, "") else None
    except (ValueError, TypeError):
        raise ApiError("수용 인원 값이 올바르지 않습니다.")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO facilities (name, type, capacity, description, image_url, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (name, ftype, capacity,
         (data.get("description") or "").strip() or None,
         (data.get("image_url") or "").strip() or None, now),
    )
    conn.commit()
    return get_facility(conn, cur.lastrowid)


def update_facility(conn, facility_id, data):
    if get_facility(conn, facility_id) is None:
        raise ApiError("시설을 찾을 수 없습니다.", 404)

    name = (data.get("name") or "").strip()
    ftype = (data.get("type") or "").strip()
    if not name:
        raise ApiError("시설 이름을 입력해주세요.")
    if not ftype:
        raise ApiError("시설 유형을 입력해주세요.")

    capacity = data.get("capacity")
    try:
        capacity = int(capacity) if capacity not in (None, "") else None
    except (ValueError, TypeError):
        raise ApiError("수용 인원 값이 올바르지 않습니다.")

    conn.execute(
        "UPDATE facilities SET name = ?, type = ?, capacity = ?,"
        " description = ?, image_url = ? WHERE id = ?",
        (name, ftype, capacity,
         (data.get("description") or "").strip() or None,
         (data.get("image_url") or "").strip() or None, facility_id),
    )
    conn.commit()
    return get_facility(conn, facility_id)


def set_image_url(conn, facility_id, image_url):
    """업로드된 이미지 URL 을 시설에 반영."""
    conn.execute("UPDATE facilities SET image_url = ? WHERE id = ?", (image_url, facility_id))
    conn.commit()
    return get_facility(conn, facility_id)


def delete_facility(conn, facility_id):
    fac = get_facility(conn, facility_id)
    if fac is None:
        raise ApiError("시설을 찾을 수 없습니다.", 404)
    # 업로드 이미지가 있으면 파일도 정리(용량 관리). 예약은 FK CASCADE 로 삭제됨.
    image_service.delete_image_file(fac.get("image_url"))
    conn.execute("DELETE FROM facilities WHERE id = ?", (facility_id,))
    conn.commit()
    return True
