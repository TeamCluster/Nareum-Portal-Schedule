"""예약(reservations) 도메인 — 기관 DB(db/<slug>.sqlite3).

운영시간(요일별)·휴무일·정기 고정활동을 반영해 예약 가능 시간을 계산/검증한다.
모든 함수는 기관 커넥션(db.get_place_db(slug))을 받는다. 검증 실패는 ApiError.

시간은 ISO 문자열('YYYY-MM-DDTHH:MM:SS')로 저장, 정수 시각(시간 단위)만 사용.
weekday 는 월=0 ~ 일=6 (파이썬 date.weekday()).
"""
import json
import uuid
from datetime import date, datetime, time, timedelta

import config
from . import facility_service
from .errors import ApiError

ISO = "%Y-%m-%dT%H:%M:%S"


# ----------------------------------------------------------------------
# 날짜/시간 헬퍼
# ----------------------------------------------------------------------
def booking_window(today=None):
    today = today or date.today()
    return (today + timedelta(days=config.BOOKING_MIN_DAYS),
            today + timedelta(days=config.BOOKING_MAX_DAYS))


def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ApiError("날짜 형식이 올바르지 않습니다.")


def _day_bounds_iso(target_date):
    start = datetime.combine(target_date, time.min).strftime(ISO)
    end = datetime.combine(target_date + timedelta(days=1), time.min).strftime(ISO)
    return start, end


def _hours_to_iso(target_date, start_hour, end_hour):
    start = datetime.combine(target_date, time.min) + timedelta(hours=start_hour)
    end = datetime.combine(target_date, time.min) + timedelta(hours=end_hour)
    return start.strftime(ISO), end.strftime(ISO)


def _active_placeholders():
    return ",".join("?" for _ in config.ACTIVE_STATUSES)


# ----------------------------------------------------------------------
# 운영시간 / 휴무일 (설정)
# ----------------------------------------------------------------------
def get_operating_hours(conn):
    """요일별 운영시간 7행 반환 (weekday 0~6 순)."""
    rows = conn.execute(
        "SELECT weekday, is_open, open_hour, close_hour FROM operating_hours ORDER BY weekday"
    ).fetchall()
    existing = {r["weekday"]: r for r in rows}
    result = []
    for wd in range(7):
        r = existing.get(wd)
        if r:
            result.append({"weekday": wd, "is_open": bool(r["is_open"]),
                           "open_hour": r["open_hour"], "close_hour": r["close_hour"]})
        else:
            result.append({"weekday": wd, "is_open": True,
                           "open_hour": config.OPEN_HOUR, "close_hour": config.CLOSE_HOUR})
    return result


def set_operating_hours(conn, items):
    """요일별 운영시간 일괄 설정. items: [{weekday,is_open,open_hour,close_hour}, ...]."""
    if not isinstance(items, list):
        raise ApiError("운영시간 형식이 올바르지 않습니다.")
    for it in items:
        try:
            wd = int(it["weekday"])
            is_open = 1 if it.get("is_open") else 0
            oh = int(it["open_hour"])
            ch = int(it["close_hour"])
        except (KeyError, ValueError, TypeError):
            raise ApiError("운영시간 값이 올바르지 않습니다.")
        if not (0 <= wd <= 6):
            raise ApiError("요일 값이 올바르지 않습니다.")
        if is_open:
            if not (config.HOUR_ABS_MIN <= oh < ch <= config.HOUR_ABS_MAX):
                raise ApiError(
                    f"운영시간은 {config.HOUR_ABS_MIN}시 이후, 시작<종료, "
                    f"{config.HOUR_ABS_MAX}시 이내여야 합니다."
                )
        conn.execute(
            "INSERT INTO operating_hours (weekday, is_open, open_hour, close_hour)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(weekday) DO UPDATE SET is_open=excluded.is_open,"
            " open_hour=excluded.open_hour, close_hour=excluded.close_hour",
            (wd, is_open, oh, ch),
        )
    conn.commit()
    return get_operating_hours(conn)


def list_closures(conn):
    rows = conn.execute(
        "SELECT id, date, reason FROM closures ORDER BY date"
    ).fetchall()
    return [dict(r) for r in rows]


def add_closure(conn, date_str, reason=""):
    target = parse_date(date_str)
    try:
        conn.execute(
            "INSERT INTO closures (date, reason) VALUES (?, ?)",
            (target.isoformat(), (reason or "").strip()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise ApiError("이미 등록된 휴무일입니다.")
    return {"ok": True}


def delete_closure(conn, closure_id):
    cur = conn.execute("DELETE FROM closures WHERE id = ?", (closure_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise ApiError("휴무일을 찾을 수 없습니다.", 404)
    return {"ok": True}


def day_config(conn, target_date):
    """해당 날짜의 운영 상태. {weekday, is_open, open_hour, close_hour, closed_reason}.

    is_open=False 이면 예약 불가(정기휴무 또는 휴무일 지정).
    """
    wd = target_date.weekday()
    row = conn.execute(
        "SELECT is_open, open_hour, close_hour FROM operating_hours WHERE weekday = ?", (wd,)
    ).fetchone()
    reg_open = bool(row["is_open"]) if row else True
    open_hour = row["open_hour"] if row else config.OPEN_HOUR
    close_hour = row["close_hour"] if row else config.CLOSE_HOUR

    closure = conn.execute(
        "SELECT reason FROM closures WHERE date = ?", (target_date.isoformat(),)
    ).fetchone()

    if closure is not None:
        return {"weekday": wd, "is_open": False, "open_hour": open_hour,
                "close_hour": close_hour, "closed_reason": closure["reason"] or "휴무일"}
    if not reg_open:
        return {"weekday": wd, "is_open": False, "open_hour": open_hour,
                "close_hour": close_hour, "closed_reason": "정기 휴무일"}
    return {"weekday": wd, "is_open": True, "open_hour": open_hour,
            "close_hour": close_hour, "closed_reason": ""}


# ----------------------------------------------------------------------
# 정기 고정활동 (recurring_blocks)
# ----------------------------------------------------------------------
def list_recurring_blocks(conn):
    rows = conn.execute(
        "SELECT r.id, r.facility_id, r.weekday, r.start_hour, r.end_hour, r.title,"
        " f.name AS facility_name"
        " FROM recurring_blocks r LEFT JOIN facilities f ON f.id = r.facility_id"
        " ORDER BY r.weekday, r.start_hour"
    ).fetchall()
    return [dict(r) for r in rows]


def add_recurring_block(conn, data):
    facility_id = data.get("facility_id")
    if not facility_id or facility_service.get_facility(conn, facility_id) is None:
        raise ApiError("시설을 선택해주세요.")
    try:
        weekday = int(data.get("weekday"))
        start_hour = int(data.get("start_hour"))
        end_hour = int(data.get("end_hour"))
    except (ValueError, TypeError):
        raise ApiError("요일/시간 값이 올바르지 않습니다.")
    if not (0 <= weekday <= 6):
        raise ApiError("요일 값이 올바르지 않습니다.")
    if not (config.HOUR_ABS_MIN <= start_hour < end_hour <= config.HOUR_ABS_MAX):
        raise ApiError("시간 범위가 올바르지 않습니다. (시작<종료)")
    title = (data.get("title") or "").strip()
    conn.execute(
        "INSERT INTO recurring_blocks (facility_id, weekday, start_hour, end_hour, title)"
        " VALUES (?, ?, ?, ?, ?)",
        (facility_id, weekday, start_hour, end_hour, title),
    )
    conn.commit()
    return {"ok": True}


def delete_recurring_block(conn, block_id):
    cur = conn.execute("DELETE FROM recurring_blocks WHERE id = ?", (block_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise ApiError("정기활동을 찾을 수 없습니다.", 404)
    return {"ok": True}


def _blocks_for(conn, facility_id, weekday):
    return conn.execute(
        "SELECT start_hour, end_hour, title FROM recurring_blocks"
        " WHERE facility_id = ? AND weekday = ? ORDER BY start_hour",
        (facility_id, weekday),
    ).fetchall()


# ----------------------------------------------------------------------
# 검증/점유 계산
# ----------------------------------------------------------------------
def resolve_hours(selected_hours, open_hour, close_hour, max_hours=None):
    """선택 시간 검증 → (start_hour, end_hour). 운영시간 범위는 인자로 받는다."""
    if not selected_hours:
        raise ApiError("이용 시간을 하나 이상 선택해주세요.")
    try:
        hours = sorted(int(h) for h in selected_hours)
    except (ValueError, TypeError):
        raise ApiError("이용 시간 값이 올바르지 않습니다.")

    if max_hours is not None and len(hours) > max_hours:
        raise ApiError(f"예약은 하루에 최대 {max_hours}시간까지만 가능합니다.")

    for h in hours:
        if not (open_hour <= h < close_hour):
            raise ApiError(
                f"운영 시간({open_hour:02d}:00~{close_hour:02d}:00) 내에서만 예약할 수 있습니다."
            )

    start_hour, end_hour = hours[0], hours[-1] + 1
    if end_hour - start_hour != len(hours):
        raise ApiError("이용 시간은 연속된 시간으로만 선택 가능합니다.")
    return start_hour, end_hour


def parse_participants(payload):
    keys = ["elementary", "middle", "high", "teen", "adult"]
    result = {}
    for k in keys:
        try:
            result[k] = int((payload or {}).get(k, 0) or 0)
        except (ValueError, TypeError):
            raise ApiError("참가 인원 값이 올바르지 않습니다.")
    return result


def booked_hours_for(conn, facility_id, target_date, exclude_res_id=None):
    """해당 시설/날짜에 점유된 시각(int) 집합 — 예약 + 정기 고정활동 포함."""
    day_start, day_end = _day_bounds_iso(target_date)
    sql = (
        "SELECT start_time, end_time FROM reservations"
        " WHERE facility_id = ? AND start_time >= ? AND start_time < ?"
        f" AND is_deleted = 0 AND status IN ({_active_placeholders()})"
    )
    params = [facility_id, day_start, day_end, *config.ACTIVE_STATUSES]
    if exclude_res_id:
        sql += " AND id != ?"
        params.append(exclude_res_id)

    hours = set()
    for row in conn.execute(sql, params).fetchall():
        s = datetime.strptime(row["start_time"], ISO).hour
        e = datetime.strptime(row["end_time"], ISO).hour
        hours.update(range(s, e))

    for b in _blocks_for(conn, facility_id, target_date.weekday()):
        hours.update(range(b["start_hour"], b["end_hour"]))
    return hours


def has_overlap(conn, facility_id, start_iso, end_iso, exclude_res_id=None):
    sql = (
        "SELECT 1 FROM reservations"
        " WHERE facility_id = ? AND start_time < ? AND end_time > ?"
        f" AND is_deleted = 0 AND status IN ({_active_placeholders()})"
    )
    params = [facility_id, end_iso, start_iso, *config.ACTIVE_STATUSES]
    if exclude_res_id:
        sql += " AND id != ?"
        params.append(exclude_res_id)
    return conn.execute(sql, params).fetchone() is not None


def has_block_overlap(conn, facility_id, weekday, start_hour, end_hour):
    return conn.execute(
        "SELECT 1 FROM recurring_blocks"
        " WHERE facility_id = ? AND weekday = ? AND start_hour < ? AND end_hour > ?",
        (facility_id, weekday, end_hour, start_hour),
    ).fetchone() is not None


def _guard_open(cfg):
    if not cfg["is_open"]:
        raise ApiError(f"해당 날짜는 예약할 수 없습니다. ({cfg['closed_reason']})")


# ----------------------------------------------------------------------
# 직렬화
# ----------------------------------------------------------------------
def _to_dict(conn, row, include_facility=True):
    data = {
        "id": row["id"],
        "access_id": row["access_id"],
        "facility_id": row["facility_id"],
        "applicant_name": row["applicant_name"],
        "applicant_contact": row["applicant_contact"],
        "applicant_school": row["applicant_school"],
        "applicant_club": row["applicant_club"],
        "status": row["status"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "participant_info": json.loads(row["participant_info"] or "{}"),
        "requested_equipment": json.loads(row["requested_equipment"] or "[]"),
        "is_deleted": bool(row["is_deleted"]),
        "reject_reason": row["reject_reason"],
        "created_at": row["created_at"],
    }
    if include_facility:
        fac = facility_service.get_facility(conn, row["facility_id"])
        if fac:
            data["facility"] = fac
    return data


_SELECT = "SELECT * FROM reservations WHERE "


def _fetch_one(conn, where, params, include_facility=True):
    row = conn.execute(_SELECT + where, params).fetchone()
    return _to_dict(conn, row, include_facility) if row else None


# ----------------------------------------------------------------------
# 공개 — 현황 / 생성 / 조회 / 취소
# ----------------------------------------------------------------------
def availability(conn, date_str):
    min_date, max_date = booking_window()
    payload = {
        "min_date": min_date.isoformat(),
        "max_date": max_date.isoformat(),
        "selected_date": date_str,
        "is_reservable": True,
        "is_open": True,
        "closed_reason": "",
        "open_hour": config.OPEN_HOUR,
        "close_hour": config.CLOSE_HOUR,
        "facilities": [],
    }
    facilities = facility_service.get_facilities(conn)

    if not date_str:
        payload["facilities"] = [
            {**f, "hours": {}, "sold_out": False} for f in facilities
        ]
        return payload

    target_date = parse_date(date_str)
    cfg = day_config(conn, target_date)
    payload["open_hour"] = cfg["open_hour"]
    payload["close_hour"] = cfg["close_hour"]
    payload["is_open"] = cfg["is_open"]
    payload["closed_reason"] = cfg["closed_reason"]

    if target_date < min_date or target_date > max_date or not cfg["is_open"]:
        payload["is_reservable"] = False

    for f in facilities:
        if not cfg["is_open"]:
            payload["facilities"].append({**f, "hours": {}, "sold_out": True})
            continue
        booked = booked_hours_for(conn, f["id"], target_date)
        hours = {
            str(h): ("booked" if h in booked else "available")
            for h in range(cfg["open_hour"], cfg["close_hour"])
        }
        payload["facilities"].append(
            {**f, "hours": hours, "sold_out": "available" not in hours.values()}
        )
    return payload


def create_public_reservation(conn, data):
    facility_id = data.get("facility_id")
    facility = facility_service.get_facility(conn, facility_id) if facility_id else None
    if facility is None:
        raise ApiError("존재하지 않는 시설입니다.", 404)

    date_str = data.get("date")
    if not date_str:
        raise ApiError("예약할 날짜를 선택해주세요.")
    target_date = parse_date(date_str)

    min_date, max_date = booking_window()
    if target_date < min_date or target_date > max_date:
        raise ApiError(
            f"예약은 {min_date.isoformat()} 부터 {max_date.isoformat()} 까지만 가능합니다."
        )

    cfg = day_config(conn, target_date)
    _guard_open(cfg)

    name = (data.get("name") or "").strip()
    contact = (data.get("contact") or "").strip()
    if not name or not contact:
        raise ApiError("신청인 이름과 연락처를 입력해주세요.")

    school = (data.get("school") or "").strip() or None
    club = (data.get("club") or "").strip() or None

    participants = parse_participants(data.get("participants"))
    if sum(participants.values()) < 1:
        raise ApiError("참가 인원을 최소 1명 이상 입력해주세요.")

    equipment = data.get("equipment") or []

    start_hour, end_hour = resolve_hours(
        data.get("hours"), cfg["open_hour"], cfg["close_hour"], max_hours=config.MAX_HOURS_PUBLIC)
    start_iso, end_iso = _hours_to_iso(target_date, start_hour, end_hour)

    # 같은 신청인, 같은 날: 하루 1회만.
    day_start, day_end = _day_bounds_iso(target_date)
    same_day = conn.execute(
        "SELECT 1 FROM reservations WHERE applicant_name = ? AND applicant_contact = ?"
        " AND start_time >= ? AND start_time < ? AND is_deleted = 0"
        f" AND status IN ({_active_placeholders()})",
        [name, contact, day_start, day_end, *config.ACTIVE_STATUSES],
    ).fetchone()
    if same_day:
        raise ApiError(
            "같은 이름과 연락처로 해당 날짜에 이미 예약된 내역이 있습니다. (하루 1회만 예약 가능)"
        )

    # 주간(월~일) 제한.
    start_of_week = target_date - timedelta(days=target_date.weekday())
    end_of_week = start_of_week + timedelta(days=7)
    week_start_iso, _ = _day_bounds_iso(start_of_week)
    week_end_iso, _ = _day_bounds_iso(end_of_week)
    weekly_count = conn.execute(
        "SELECT COUNT(*) AS c FROM reservations WHERE applicant_name = ? AND applicant_contact = ?"
        " AND start_time >= ? AND start_time < ? AND is_deleted = 0"
        f" AND status IN ({_active_placeholders()})",
        [name, contact, week_start_iso, week_end_iso, *config.ACTIVE_STATUSES],
    ).fetchone()["c"]
    if weekly_count >= config.WEEKLY_LIMIT:
        raise ApiError(
            f"해당 주간(월~일)에 이미 {config.WEEKLY_LIMIT}회의 예약이 존재합니다. "
            f"일주일에 최대 {config.WEEKLY_LIMIT}회까지만 예약하실 수 있습니다."
        )

    if has_overlap(conn, facility_id, start_iso, end_iso):
        raise ApiError("선택하신 시간에 이미 다른 예약이 진행 중입니다.")
    if has_block_overlap(conn, facility_id, target_date.weekday(), start_hour, end_hour):
        raise ApiError("선택하신 시간은 정기 고정활동이 있어 예약할 수 없습니다.")

    access_id = str(uuid.uuid4())
    now = datetime.now().strftime(ISO)
    conn.execute(
        "INSERT INTO reservations"
        " (access_id, facility_id, applicant_name, applicant_contact, applicant_school,"
        "  applicant_club, status, start_time, end_time, participant_info,"
        "  requested_equipment, is_deleted, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, 0, ?)",
        (access_id, facility_id, name, contact, school, club, start_iso, end_iso,
         json.dumps(participants, ensure_ascii=False),
         json.dumps(equipment, ensure_ascii=False), now),
    )
    conn.commit()
    return access_id


def get_by_access_id(conn, access_id):
    res = _fetch_one(conn, "access_id = ?", [access_id])
    if res is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    return res


def lookup(conn, name, contact):
    name = (name or "").strip()
    contact = (contact or "").strip()
    if not name or not contact:
        raise ApiError("이름과 연락처를 입력해주세요.")
    rows = conn.execute(
        "SELECT * FROM reservations WHERE applicant_name = ? AND applicant_contact = ?"
        " ORDER BY start_time DESC",
        (name, contact),
    ).fetchall()
    return [_to_dict(conn, r) for r in rows]


def cancel(conn, res_id):
    row = conn.execute("SELECT * FROM reservations WHERE id = ?", (res_id,)).fetchone()
    if row is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    if row["is_deleted"] or row["status"] not in ("confirmed", "pending"):
        raise ApiError("이미 취소되었거나 유효하지 않은 예약입니다.")
    conn.execute(
        "UPDATE reservations SET status = 'cancelled', is_deleted = 1 WHERE id = ?",
        (res_id,),
    )
    conn.commit()
    return {"ok": True, "message": "예약이 정상적으로 취소되었습니다."}


# ----------------------------------------------------------------------
# 관리자 — 목록 / 대시보드 / 생성 / 수정 / 승인 / 거절
# ----------------------------------------------------------------------
def get_reservation(conn, res_id):
    res = _fetch_one(conn, "id = ?", [res_id])
    if res is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    return res


def all_reservations(conn):
    rows = conn.execute(
        "SELECT * FROM reservations ORDER BY start_time DESC"
    ).fetchall()
    return [_to_dict(conn, r) for r in rows]


def requests_list(conn):
    rows = conn.execute(
        "SELECT * FROM reservations WHERE status = 'pending' AND is_deleted = 0"
        " ORDER BY start_time"
    ).fetchall()
    return [_to_dict(conn, r) for r in rows]


def dashboard(conn, date_str=None):
    try:
        selected_date = (
            datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
        )
    except (ValueError, TypeError):
        selected_date = date.today()

    pending = requests_list(conn)

    day_start, day_end = _day_bounds_iso(selected_date)
    today_rows = conn.execute(
        "SELECT * FROM reservations WHERE start_time >= ? AND start_time < ?"
        " AND is_deleted = 0 ORDER BY start_time",
        (day_start, day_end),
    ).fetchall()

    groups_map = {}
    order = []
    for r in today_rows:
        fac = facility_service.get_facility(conn, r["facility_id"])
        fname = fac["name"] if fac else "(삭제된 시설)"
        if fname not in groups_map:
            groups_map[fname] = []
            order.append(fname)
        groups_map[fname].append(_to_dict(conn, r))

    groups = [{"facility_name": n, "reservations": groups_map[n]} for n in order]

    return {
        "selected_date": selected_date.isoformat(),
        "prev_date": (selected_date - timedelta(days=1)).isoformat(),
        "next_date": (selected_date + timedelta(days=1)).isoformat(),
        "pending_count": len(pending),
        "pending_preview": pending[:3],
        "todays_groups": groups,
    }


def calendar_events(conn):
    events = []
    rows = conn.execute(
        "SELECT * FROM reservations WHERE is_deleted = 0"
    ).fetchall()
    for r in rows:
        fac = facility_service.get_facility(conn, r["facility_id"])
        fname = fac["name"] if fac else "?"
        events.append({
            "id": r["id"],
            "title": f"[{fname}] {r['applicant_name']}",
            "start": r["start_time"],
            "end": r["end_time"],
            "color": "#ff9800" if r["status"] == "pending" else "#4CAF50",
        })
    return events


def day_grid(conn, date_str=None):
    """일자별 시설×시간 현황 매트릭스. 운영시간 범위/휴무/정기활동 반영.

    facility.segments: 각 항목은 {type, from_hour, to_hour, ...}
      type='free' | 'res'(res_id,status,name,contact) | 'block'(title)
    휴무일이면 is_open=False, facilities 는 시설 목록만(segments 비움).
    """
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    except (ValueError, TypeError):
        target = date.today()

    cfg = day_config(conn, target)
    open_h, close_h = cfg["open_hour"], cfg["close_hour"]
    base = {
        "date": target.isoformat(),
        "is_open": cfg["is_open"],
        "closed_reason": cfg["closed_reason"],
        "open_hour": open_h,
        "close_hour": close_h,
        "hours": list(range(open_h, close_h)) if cfg["is_open"] else [],
    }

    facilities_meta = facility_service.get_facilities(conn)
    if not cfg["is_open"]:
        base["facilities"] = [{"id": f["id"], "name": f["name"], "type": f["type"],
                               "segments": []} for f in facilities_meta]
        return base

    day_start, day_end = _day_bounds_iso(target)
    rows = conn.execute(
        "SELECT id, facility_id, applicant_name, applicant_contact, status, start_time, end_time"
        " FROM reservations WHERE start_time >= ? AND start_time < ? AND is_deleted = 0"
        f" AND status IN ({_active_placeholders()}) ORDER BY start_time",
        [day_start, day_end, *config.ACTIVE_STATUSES],
    ).fetchall()

    occ = {}
    for r in rows:
        s = datetime.strptime(r["start_time"], ISO).hour
        e = datetime.strptime(r["end_time"], ISO).hour
        occ.setdefault(r["facility_id"], []).append({
            "type": "res", "start_hour": s, "end_hour": e,
            "res_id": r["id"], "status": r["status"],
            "name": r["applicant_name"], "contact": r["applicant_contact"],
        })

    facilities = []
    for f in facilities_meta:
        items = list(occ.get(f["id"], []))
        for b in _blocks_for(conn, f["id"], target.weekday()):
            items.append({"type": "block", "start_hour": b["start_hour"],
                          "end_hour": b["end_hour"], "title": b["title"] or "정기활동"})
        items.sort(key=lambda x: x["start_hour"])

        segments, h, idx = [], open_h, 0
        while h < close_h:
            if idx < len(items) and items[idx]["start_hour"] <= h:
                seg = items[idx]
                idx += 1
                end = min(seg["end_hour"], close_h)
                if end <= h:
                    continue
                out = {"type": seg["type"], "from_hour": h, "to_hour": end}
                if seg["type"] == "res":
                    out.update({"res_id": seg["res_id"], "status": seg["status"],
                                "name": seg["name"], "contact": seg["contact"]})
                else:
                    out["title"] = seg["title"]
                segments.append(out)
                h = end
            else:
                next_start = items[idx]["start_hour"] if idx < len(items) else close_h
                end = min(max(next_start, h + 1), close_h)
                segments.append({"type": "free", "from_hour": h, "to_hour": end})
                h = end
        facilities.append({"id": f["id"], "name": f["name"], "type": f["type"],
                           "segments": segments})

    base["facilities"] = facilities
    return base


def create_admin_reservation(conn, data):
    """관리자 직접 추가 — 확정 상태(0명 허용, 시간제한 없음). 중복/정기활동만 차단."""
    facility_id = data.get("facility_id")
    facility = facility_service.get_facility(conn, facility_id) if facility_id else None
    if facility is None:
        raise ApiError("시설을 선택해주세요.")

    target_date = parse_date(data.get("date"))
    cfg = day_config(conn, target_date)
    _guard_open(cfg)
    start_hour, end_hour = resolve_hours(data.get("hours"), cfg["open_hour"], cfg["close_hour"])
    start_iso, end_iso = _hours_to_iso(target_date, start_hour, end_hour)

    if has_overlap(conn, facility_id, start_iso, end_iso):
        raise ApiError("선택하신 시설/시간에 이미 등록된 예약이 있어 추가할 수 없습니다.")
    if has_block_overlap(conn, facility_id, target_date.weekday(), start_hour, end_hour):
        raise ApiError("선택하신 시간은 정기 고정활동이 있어 추가할 수 없습니다.")

    access_id = str(uuid.uuid4())
    now = datetime.now().strftime(ISO)
    participants = parse_participants(data.get("participants"))
    cur = conn.execute(
        "INSERT INTO reservations"
        " (access_id, facility_id, applicant_name, applicant_contact, applicant_school,"
        "  applicant_club, status, start_time, end_time, participant_info,"
        "  requested_equipment, is_deleted, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, 0, ?)",
        (access_id, facility_id,
         (data.get("name") or "").strip(), (data.get("contact") or "").strip(),
         (data.get("school") or "").strip() or None,
         (data.get("club") or "").strip() or None,
         start_iso, end_iso,
         json.dumps(participants, ensure_ascii=False),
         json.dumps(data.get("equipment") or [], ensure_ascii=False), now),
    )
    conn.commit()
    return {"ok": True, "id": cur.lastrowid}


def update_reservation(conn, res_id, data):
    row = conn.execute("SELECT id FROM reservations WHERE id = ?", (res_id,)).fetchone()
    if row is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)

    facility_id = data.get("facility_id")
    if not facility_id or facility_service.get_facility(conn, facility_id) is None:
        raise ApiError("시설을 선택해주세요.")

    target_date = parse_date(data.get("date"))
    cfg = day_config(conn, target_date)
    _guard_open(cfg)
    start_hour, end_hour = resolve_hours(data.get("hours"), cfg["open_hour"], cfg["close_hour"])
    start_iso, end_iso = _hours_to_iso(target_date, start_hour, end_hour)

    if has_overlap(conn, facility_id, start_iso, end_iso, exclude_res_id=res_id):
        raise ApiError("선택하신 시설/시간에 이미 등록된 다른 예약이 있어 변경할 수 없습니다.")
    if has_block_overlap(conn, facility_id, target_date.weekday(), start_hour, end_hour):
        raise ApiError("선택하신 시간은 정기 고정활동이 있어 변경할 수 없습니다.")

    new_status = data.get("status")
    if new_status not in ("pending", "confirmed", "cancelled", "rejected"):
        raise ApiError("올바르지 않은 상태 값입니다.")

    is_deleted = 1 if new_status in ("cancelled", "rejected") else 0
    reject_reason = data.get("reject_reason", "") if new_status == "rejected" else None

    conn.execute(
        "UPDATE reservations SET facility_id = ?, start_time = ?, end_time = ?,"
        " applicant_name = ?, applicant_contact = ?, applicant_school = ?,"
        " applicant_club = ?, participant_info = ?, requested_equipment = ?,"
        " status = ?, is_deleted = ?, reject_reason = ? WHERE id = ?",
        (facility_id, start_iso, end_iso,
         (data.get("name") or "").strip(), (data.get("contact") or "").strip(),
         (data.get("school") or "").strip() or None,
         (data.get("club") or "").strip() or None,
         json.dumps(parse_participants(data.get("participants")), ensure_ascii=False),
         json.dumps(data.get("equipment") or [], ensure_ascii=False),
         new_status, is_deleted, reject_reason, res_id),
    )
    conn.commit()
    return {"ok": True, "message": "예약 정보가 성공적으로 수정되었습니다."}


def approve(conn, res_id):
    row = conn.execute(
        "SELECT applicant_name FROM reservations WHERE id = ?", (res_id,)
    ).fetchone()
    if row is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    conn.execute(
        "UPDATE reservations SET status = 'confirmed', is_deleted = 0 WHERE id = ?",
        (res_id,),
    )
    conn.commit()
    return {"ok": True, "message": f"{row['applicant_name']}님의 예약이 승인되었습니다."}


def reject(conn, res_id, reason=None):
    row = conn.execute(
        "SELECT applicant_name FROM reservations WHERE id = ?", (res_id,)
    ).fetchone()
    if row is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    conn.execute(
        "UPDATE reservations SET status = 'rejected', is_deleted = 1, reject_reason = ? WHERE id = ?",
        (reason or "관리자 직권 거절", res_id),
    )
    conn.commit()
    return {"ok": True, "message": f"{row['applicant_name']}님의 예약이 거절 처리되었습니다."}
