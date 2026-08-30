"""시설(facilities) CRUD 및 조회 — 기관 DB(db/<slug>.sqlite3).

기관 관리자가 자기 기관의 시설을 관리하고, 공개 페이지가 시설 목록을 읽는다.
모든 함수는 기관 커넥션(db.get_place_db(slug))을 받는다.
"""
from datetime import datetime

from db import write_lock

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
        # sort_order 가 같으면(= 예전 데이터) 등록 차례대로.
        " FROM facilities ORDER BY sort_order, id"
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
    # 새 시설은 목록 맨 뒤에 붙인다(0 으로 두면 기존 시설 앞으로 끼어든다).
    next_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM facilities").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO facilities (name, type, capacity, description, image_url, created_at,"
        " sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, ftype, capacity,
         (data.get("description") or "").strip() or None,
         (data.get("image_url") or "").strip() or None, now, next_order),
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


def move_facility(conn, facility_id, direction):
    """시설을 목록에서 한 칸 위/아래로 옮긴다 (표시 순서만 — id 는 그대로).

    바로 옆 시설과 sort_order 를 맞바꾼다. 옮기기 전에 현재 순서대로 1..N 으로 다시
    번호를 매기는데, 예전 데이터는 sort_order 가 모두 0(동률)이라 그대로 맞바꾸면
    아무 변화가 없기 때문이다. 이 정규화는 보이는 순서를 바꾸지 않는다.
    """
    if direction not in ("up", "down"):
        raise ApiError("이동 방향이 올바르지 않습니다. (up/down)")

    # 읽기(순서 파악) → 쓰기 사이에 다른 요청이 끼어들면 순서가 뒤엉키므로 잠금 안에서.
    with write_lock(conn):
        rows = conn.execute(
            "SELECT id FROM facilities ORDER BY sort_order, id").fetchall()
        ids = [r["id"] for r in rows]
        if facility_id not in ids:
            raise ApiError("시설을 찾을 수 없습니다.", 404)

        for pos, fid in enumerate(ids, start=1):
            conn.execute("UPDATE facilities SET sort_order = ? WHERE id = ?", (pos, fid))

        i = ids.index(facility_id)
        j = i - 1 if direction == "up" else i + 1
        if not 0 <= j < len(ids):
            raise ApiError("더 이동할 수 없습니다.")

        conn.execute("UPDATE facilities SET sort_order = ? WHERE id = ?", (j + 1, ids[i]))
        conn.execute("UPDATE facilities SET sort_order = ? WHERE id = ?", (i + 1, ids[j]))

    return {"ok": True}


def delete_facility(conn, facility_id):
    fac = get_facility(conn, facility_id)
    if fac is None:
        raise ApiError("시설을 찾을 수 없습니다.", 404)
    # 업로드 이미지가 있으면 파일도 정리(용량 관리). 예약은 FK CASCADE 로 삭제됨.
    image_service.delete_image_file(fac.get("image_url"))
    conn.execute("DELETE FROM facilities WHERE id = ?", (facility_id,))
    conn.commit()
    return True
