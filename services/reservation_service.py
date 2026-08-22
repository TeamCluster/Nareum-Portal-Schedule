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
from db import write_lock
from . import facility_service, holiday_service
from .errors import ApiError

ISO = "%Y-%m-%dT%H:%M:%S"

# --- 입력 길이 상한 -------------------------------------------------------
# 공개(비로그인) 신청에서 들어오는 값이므로 상한이 없으면 임의 크기의 행을
# 무제한으로 밀어넣을 수 있다. 실제 입력보다 넉넉하되 남용은 막는 값.
MAX_NAME_LENGTH = 50
MAX_CONTACT_LENGTH = 30
MAX_SCHOOL_LENGTH = 100
MAX_CLUB_LENGTH = 100
MAX_REASON_LENGTH = 500
MAX_EQUIPMENT_ITEMS = 20
MAX_EQUIPMENT_LENGTH = 50
MAX_PARTICIPANTS_PER_GROUP = 1000


def _clip(value, limit, label):
    """문자열 정리 + 길이 검증. 초과하면 자르지 않고 거절한다(조용한 절삭 방지)."""
    text = (value or "").strip()
    if len(text) > limit:
        raise ApiError(f"{label}은(는) {limit}자 이내여야 합니다.")
    return text


def _applicant_fields(data):
    """신청인 관련 자유입력 필드를 검증해 반환. 빈 선택항목은 None."""
    return {
        "name": _clip(data.get("name"), MAX_NAME_LENGTH, "이름"),
        "contact": _clip(data.get("contact"), MAX_CONTACT_LENGTH, "연락처"),
        "school": _clip(data.get("school"), MAX_SCHOOL_LENGTH, "학교명") or None,
        "club": _clip(data.get("club"), MAX_CLUB_LENGTH, "동아리명") or None,
    }


def _clean_equipment(value):
    """요청 물품 목록 검증 — 문자열 리스트, 개수·길이 제한."""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ApiError("요청 물품 형식이 올바르지 않습니다.")
    if len(value) > MAX_EQUIPMENT_ITEMS:
        raise ApiError(f"요청 물품은 최대 {MAX_EQUIPMENT_ITEMS}개까지 선택할 수 있습니다.")
    items = []
    for item in value:
        if not isinstance(item, str):
            raise ApiError("요청 물품 형식이 올바르지 않습니다.")
        text = item.strip()
        if not text:
            continue
        if len(text) > MAX_EQUIPMENT_LENGTH:
            raise ApiError(f"요청 물품명은 {MAX_EQUIPMENT_LENGTH}자 이내여야 합니다.")
        items.append(text)
    return items


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


VALID_CLOSURE_TYPES = ("closure", "holiday")


def list_closures(conn):
    """기관 지정 휴무일/공휴일 목록 (type 포함)."""
    rows = conn.execute(
        "SELECT id, date, reason, type FROM closures ORDER BY date"
    ).fetchall()
    return [dict(r) for r in rows]


def add_closure(conn, date_str, name="", type_="closure"):
    target = parse_date(date_str)
    if type_ not in VALID_CLOSURE_TYPES:
        raise ApiError("유형이 올바르지 않습니다. (closure/holiday)")
    try:
        conn.execute(
            "INSERT INTO closures (date, reason, type) VALUES (?, ?, ?)",
            (target.isoformat(), (name or "").strip(), type_),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise ApiError("이미 등록된 날짜입니다.")
    return {"ok": True}


def delete_closure(conn, closure_id):
    cur = conn.execute("DELETE FROM closures WHERE id = ?", (closure_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise ApiError("휴무일을 찾을 수 없습니다.", 404)
    return {"ok": True}


# --- 공통(슈퍼) 휴무일 제외 / 공휴일 운영 설정 -----------------------------
def add_exclude(conn, date_str):
    """슈퍼 공통 휴무일을 이 기관에서 제외(별도 관리)."""
    target = parse_date(date_str)
    conn.execute("INSERT OR IGNORE INTO holiday_excludes (date) VALUES (?)", (target.isoformat(),))
    conn.commit()
    return {"ok": True}


def delete_exclude(conn, date_str):
    """제외 해제(공통 휴무일 다시 적용)."""
    target = parse_date(date_str)
    conn.execute("DELETE FROM holiday_excludes WHERE date = ?", (target.isoformat(),))
    conn.commit()
    return {"ok": True}


def get_holiday_operates(conn):
    row = conn.execute(
        "SELECT value FROM place_settings WHERE key = 'holiday_operates'"
    ).fetchone()
    return bool(row and row["value"] == "1")


def set_holiday_operates(conn, value):
    conn.execute(
        "INSERT INTO place_settings (key, value) VALUES ('holiday_operates', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if value else "0",),
    )
    conn.commit()
    return {"ok": True, "holiday_operates": bool(value)}


def org_holidays_view(conn):
    """관리자 UI용: 공휴일 운영 설정 + 공통(제외여부) + 기관 지정 목록."""
    excludes = {r["date"] for r in conn.execute("SELECT date FROM holiday_excludes").fetchall()}
    common = [{**h, "excluded": h["date"] in excludes}
              for h in holiday_service.list_common_holidays()]
    return {
        "holiday_operates": get_holiday_operates(conn),
        "common": common,
        "place": list_closures(conn),
    }


def day_config(conn, target_date):
    """해당 날짜의 운영 상태.

    반환: {weekday, is_open, open_hour, close_hour, closed_reason, note}
    규칙:
      - 휴무일(type=closure): 무조건 휴무.
      - 공휴일(type=holiday): holiday_operates 이면 일요일(주말) 운영시간 적용, 아니면 휴무.
      - 그 외: 요일별 운영시간(정기휴무면 휴무).
    공통(슈퍼) 휴무일도 반영하되, 이 기관에서 제외(holiday_excludes)한 날짜는 무시.
    """
    wd = target_date.weekday()
    row = conn.execute(
        "SELECT is_open, open_hour, close_hour FROM operating_hours WHERE weekday = ?", (wd,)
    ).fetchone()
    reg_open = bool(row["is_open"]) if row else True
    open_hour = row["open_hour"] if row else config.OPEN_HOUR
    close_hour = row["close_hour"] if row else config.CLOSE_HOUR
    date_iso = target_date.isoformat()

    def result(is_open, oh, ch, reason, note=""):
        return {"weekday": wd, "is_open": is_open, "open_hour": oh,
                "close_hour": ch, "closed_reason": reason, "note": note}

    # 유효 휴무/공휴일: 기관 지정 + (제외되지 않은) 공통.
    entries = []
    for c in conn.execute(
        "SELECT reason AS name, type FROM closures WHERE date = ?", (date_iso,)
    ).fetchall():
        entries.append({"name": c["name"], "type": c["type"] or "closure"})
    excluded = conn.execute(
        "SELECT 1 FROM holiday_excludes WHERE date = ?", (date_iso,)
    ).fetchone() is not None
    if not excluded:
        for h in holiday_service.common_holidays_on(date_iso):
            entries.append({"name": h["name"], "type": h["type"] or "holiday"})

    # 휴무일(closure) 우선 — 무조건 휴무.
    closure = next((e for e in entries if e["type"] == "closure"), None)
    if closure:
        return result(False, open_hour, close_hour, closure["name"] or "휴무일")

    # 공휴일(holiday).
    holiday = next((e for e in entries if e["type"] == "holiday"), None)
    if holiday:
        name = holiday["name"] or "공휴일"
        if not get_holiday_operates(conn):
            return result(False, open_hour, close_hour, name)
        sun = conn.execute(
            "SELECT is_open, open_hour, close_hour FROM operating_hours WHERE weekday = 6"
        ).fetchone()
        if sun and not sun["is_open"]:
            return result(False, open_hour, close_hour, f"{name} (일요일 휴무)")
        soh = sun["open_hour"] if sun else config.OPEN_HOUR
        sch = sun["close_hour"] if sun else config.CLOSE_HOUR
        return result(True, soh, sch, "", note=f"공휴일: {name} (주말 운영시간 적용)")

    if not reg_open:
        return result(False, open_hour, close_hour, "정기 휴무일")
    return result(True, open_hour, close_hour, "")


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
            count = int((payload or {}).get(k, 0) or 0)
        except (ValueError, TypeError):
            raise ApiError("참가 인원 값이 올바르지 않습니다.")
        if not (0 <= count <= MAX_PARTICIPANTS_PER_GROUP):
            raise ApiError(
                f"참가 인원은 구분별 0~{MAX_PARTICIPANTS_PER_GROUP}명 사이여야 합니다."
            )
        result[k] = count
    return result


def booked_hours_for(conn, facility_id, target_date, exclude_res_id=None, include_blocks=True):
    """해당 시설/날짜에 점유된 시각(int) 집합 — 예약(+ 옵션: 정기 고정활동)."""
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

    if include_blocks:
        hours |= block_hours_for(conn, facility_id, target_date)
    return hours


def block_hours_for(conn, facility_id, target_date):
    """해당 시설/날짜(요일)의 정기 고정활동 점유 시각(int) 집합."""
    hours = set()
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
        "note": "",
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
    payload["note"] = cfg.get("note", "")

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

    fields = _applicant_fields(data)
    name, contact = fields["name"], fields["contact"]
    school, club = fields["school"], fields["club"]
    if not name or not contact:
        raise ApiError("신청인 이름과 연락처를 입력해주세요.")

    participants = parse_participants(data.get("participants"))
    if sum(participants.values()) < 1:
        raise ApiError("참가 인원을 최소 1명 이상 입력해주세요.")

    equipment = _clean_equipment(data.get("equipment"))

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

    access_id = str(uuid.uuid4())
    now = datetime.now().strftime(ISO)
    # 겹침 검사와 INSERT 사이에 다른 요청이 끼어들면 이중 예약이 되므로
    # 쓰기 잠금을 잡은 채로 다시 확인하고 넣는다.
    with write_lock(conn):
        if has_overlap(conn, facility_id, start_iso, end_iso):
            raise ApiError("선택하신 시간에 이미 다른 예약이 진행 중입니다.")
        if has_block_overlap(conn, facility_id, target_date.weekday(), start_hour, end_hour):
            raise ApiError("선택하신 시간은 정기 고정활동이 있어 예약할 수 없습니다.")
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


def cancel(conn, res_id, name, contact):
    """공개 취소 — 신청인 본인 확인(이름+연락처)이 일치해야 한다.

    이 라우트는 로그인 없이 열려 있으므로 소유자 확인이 없으면 예약 id 를
    1 부터 훑는 것만으로 전체 예약을 말소할 수 있다(IDOR). `lookup` 과 같은
    기준(이름+연락처)으로 대조하고, 불일치와 부재를 같은 404 로 응답해
    id 존재 여부가 새어나가지 않게 한다.
    """
    name = _clip(name, MAX_NAME_LENGTH, "이름")
    contact = _clip(contact, MAX_CONTACT_LENGTH, "연락처")
    if not name or not contact:
        raise ApiError("예약자 이름과 연락처를 입력해주세요.")

    row = conn.execute(
        "SELECT id, status, is_deleted FROM reservations"
        " WHERE id = ? AND applicant_name = ? AND applicant_contact = ?",
        (res_id, name, contact),
    ).fetchone()
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
        # 시간 단위 점유를 만든다. 정기활동을 먼저 채우고 예약으로 덮어써서
        # 겹치는 구간에서 예약(임의 예약건)이 우선하여 보이도록 한다.
        cell = {}  # hour -> {"key":..., "type":..., ...}
        for i, b in enumerate(_blocks_for(conn, f["id"], target.weekday())):
            for h in range(max(b["start_hour"], open_h), min(b["end_hour"], close_h)):
                cell[h] = {"key": f"blk-{i}", "type": "block", "title": b["title"] or "정기활동"}
        for r in occ.get(f["id"], []):
            for h in range(max(r["start_hour"], open_h), min(r["end_hour"], close_h)):
                cell[h] = {"key": f"res-{r['res_id']}", "type": "res", "res_id": r["res_id"],
                           "status": r["status"], "name": r["name"], "contact": r["contact"]}

        # 같은 점유(동일 key) 연속 구간을 하나의 세그먼트로 병합, 빈 구간은 free.
        segments, h = [], open_h
        while h < close_h:
            here = cell.get(h)
            if here is None:
                start = h
                while h < close_h and cell.get(h) is None:
                    h += 1
                segments.append({"type": "free", "from_hour": start, "to_hour": h})
            else:
                start = h
                while h < close_h and cell.get(h) is not None and cell[h]["key"] == here["key"]:
                    h += 1
                seg = {"type": here["type"], "from_hour": start, "to_hour": h}
                if here["type"] == "res":
                    seg.update({"res_id": here["res_id"], "status": here["status"],
                                "name": here["name"], "contact": here["contact"]})
                else:
                    seg["title"] = here["title"]
                segments.append(seg)
        facilities.append({"id": f["id"], "name": f["name"], "type": f["type"],
                           "segments": segments})

    base["facilities"] = facilities
    return base


def week_grid(conn, date_str=None):
    """주간(월~일) 시설별 현황. 각 날짜를 day_grid 로 계산해 모은다.

    프론트는 행=시간, 열=[요일 × 시설] 리소스 그리드로 렌더한다.
    hour_min/max 는 그 주에서 운영하는 날들의 최소 시작 ~ 최대 종료 시각.
    """
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    except (ValueError, TypeError):
        target = date.today()

    monday = target - timedelta(days=target.weekday())
    days = [day_grid(conn, (monday + timedelta(days=i)).isoformat()) for i in range(7)]

    opens = [d["open_hour"] for d in days if d["is_open"]]
    closes = [d["close_hour"] for d in days if d["is_open"]]
    hour_min = min(opens) if opens else config.OPEN_HOUR
    hour_max = max(closes) if closes else config.CLOSE_HOUR

    facilities = [{"id": f["id"], "name": f["name"]}
                  for f in facility_service.get_facilities(conn)]

    return {
        "week_start": monday.isoformat(),
        "prev_week": (monday - timedelta(days=7)).isoformat(),
        "next_week": (monday + timedelta(days=7)).isoformat(),
        "hour_min": hour_min,
        "hour_max": hour_max,
        "facilities": facilities,
        "days": days,
    }


def create_admin_reservation(conn, data):
    """관리자 직접 추가 — 확정 상태(0명 허용, 시간제한 없음). 중복/정기활동만 차단."""
    facility_id = data.get("facility_id")
    facility = facility_service.get_facility(conn, facility_id) if facility_id else None
    if facility is None:
        raise ApiError("시설을 선택해주세요.")

    target_date = parse_date(data.get("date"))
    cfg = day_config(conn, target_date)
    # 관리자 직접 추가는 정기휴무일/정기 고정활동을 무시할 수 있음(경고만 반환).
    start_hour, end_hour = resolve_hours(data.get("hours"), cfg["open_hour"], cfg["close_hour"])
    start_iso, end_iso = _hours_to_iso(target_date, start_hour, end_hour)

    warnings = []
    if not cfg["is_open"]:
        warnings.append(f"휴무일({cfg['closed_reason']})에 예약을 추가했습니다.")
    if has_block_overlap(conn, facility_id, target_date.weekday(), start_hour, end_hour):
        warnings.append("정기 고정활동과 겹치는 시간에 예약을 추가했습니다.")

    access_id = str(uuid.uuid4())
    now = datetime.now().strftime(ISO)
    participants = parse_participants(data.get("participants"))
    fields = _applicant_fields(data)
    # 실제 예약 간 중복(이중 예약)은 여전히 차단 — 쓰기 잠금 안에서 확인.
    with write_lock(conn):
        if has_overlap(conn, facility_id, start_iso, end_iso):
            raise ApiError("선택하신 시설/시간에 이미 등록된 예약이 있어 추가할 수 없습니다.")
        cur = conn.execute(
            "INSERT INTO reservations"
            " (access_id, facility_id, applicant_name, applicant_contact, applicant_school,"
            "  applicant_club, status, start_time, end_time, participant_info,"
            "  requested_equipment, is_deleted, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, 0, ?)",
            (access_id, facility_id,
             fields["name"], fields["contact"], fields["school"], fields["club"],
             start_iso, end_iso,
             json.dumps(participants, ensure_ascii=False),
             json.dumps(_clean_equipment(data.get("equipment")), ensure_ascii=False), now),
        )
    return {"ok": True, "id": cur.lastrowid, "warnings": warnings}


def update_reservation(conn, res_id, data):
    row = conn.execute("SELECT id FROM reservations WHERE id = ?", (res_id,)).fetchone()
    if row is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)

    facility_id = data.get("facility_id")
    if not facility_id or facility_service.get_facility(conn, facility_id) is None:
        raise ApiError("시설을 선택해주세요.")

    target_date = parse_date(data.get("date"))
    cfg = day_config(conn, target_date)
    # 관리자 수정도 정기휴무일/정기 고정활동을 무시할 수 있음(경고만).
    start_hour, end_hour = resolve_hours(data.get("hours"), cfg["open_hour"], cfg["close_hour"])
    start_iso, end_iso = _hours_to_iso(target_date, start_hour, end_hour)

    warnings = []
    if not cfg["is_open"]:
        warnings.append(f"휴무일({cfg['closed_reason']})로 지정된 날짜입니다.")
    if has_block_overlap(conn, facility_id, target_date.weekday(), start_hour, end_hour):
        warnings.append("정기 고정활동과 겹치는 시간입니다.")

    new_status = data.get("status")
    if new_status not in ("pending", "confirmed", "cancelled", "rejected"):
        raise ApiError("올바르지 않은 상태 값입니다.")

    is_deleted = 1 if new_status in ("cancelled", "rejected") else 0
    reject_reason = _clip(data.get("reject_reason", ""), MAX_REASON_LENGTH,
                          "거절 사유") if new_status == "rejected" else None
    fields = _applicant_fields(data)
    participants = parse_participants(data.get("participants"))
    equipment = _clean_equipment(data.get("equipment"))

    with write_lock(conn):
        if has_overlap(conn, facility_id, start_iso, end_iso, exclude_res_id=res_id):
            raise ApiError("선택하신 시설/시간에 이미 등록된 다른 예약이 있어 변경할 수 없습니다.")
        conn.execute(
            "UPDATE reservations SET facility_id = ?, start_time = ?, end_time = ?,"
            " applicant_name = ?, applicant_contact = ?, applicant_school = ?,"
            " applicant_club = ?, participant_info = ?, requested_equipment = ?,"
            " status = ?, is_deleted = ?, reject_reason = ? WHERE id = ?",
            (facility_id, start_iso, end_iso,
             fields["name"], fields["contact"], fields["school"], fields["club"],
             json.dumps(participants, ensure_ascii=False),
             json.dumps(equipment, ensure_ascii=False),
             new_status, is_deleted, reject_reason, res_id),
        )
    return {"ok": True, "message": "예약 정보가 성공적으로 수정되었습니다.", "warnings": warnings}


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
