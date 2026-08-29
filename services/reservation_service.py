"""예약(reservations) 도메인 — 기관 DB(db/<slug>.sqlite3).

운영시간(요일별)·휴무일·정기 고정활동을 반영해 예약 가능 시간을 계산/검증한다.
모든 함수는 기관 커넥션(db.get_place_db(slug))을 받는다. 검증 실패는 ApiError.

시간은 ISO 문자열('YYYY-MM-DDTHH:MM:SS')로 저장, 정수 시각(시간 단위)만 사용.
weekday 는 월=0 ~ 일=6 (파이썬 date.weekday()).
"""
import calendar
import json
import uuid
from datetime import date, datetime, time, timedelta

import config
from . import facility_service, holiday_service
from .errors import ApiError

ISO = "%Y-%m-%dT%H:%M:%S"


# ----------------------------------------------------------------------
# 날짜/시간 헬퍼
# ----------------------------------------------------------------------
def get_booking_rules(conn=None):
    """기관의 대관 규칙. conn 이 없거나 값이 없으면 config 기본값.

    기관마다 방침이 달라 place_settings 의 booking_rules(JSON)에 저장한다.
    """
    rules = dict(config.DEFAULT_BOOKING_RULES)
    if conn is None:
        return rules
    row = conn.execute(
        "SELECT value FROM place_settings WHERE key = 'booking_rules'"
    ).fetchone()
    if row:
        try:
            stored = json.loads(row["value"])
        except (ValueError, TypeError):
            stored = {}
        for key in rules:
            if isinstance(stored.get(key), int) and not isinstance(stored[key], bool):
                rules[key] = stored[key]
    return rules


def set_booking_rules(conn, data):
    """관리자 저장 — 보낸 키만 갱신하고 허용 범위를 검증한다."""
    rules = get_booking_rules(conn)
    data = data or {}
    if not isinstance(data, dict):
        raise ApiError("대관 규칙 형식이 올바르지 않습니다.")

    labels = {
        "booking_min_days": "최소 신청 기한(일)",
        "booking_max_days": "예약 가능 범위(일)",
        "cancel_deadline_days": "취소 마감(일)",
        "penalty_months": "재대관 제한 기간(개월)",
        "extension_hours": "현장 연장 가능 시간",
    }
    for key, (low, high) in config.BOOKING_RULE_LIMITS.items():
        if key not in data:
            continue
        try:
            value = int(data[key])
        except (ValueError, TypeError):
            raise ApiError(f"{labels[key]} 값이 올바르지 않습니다.")
        if not (low <= value <= high):
            raise ApiError(f"{labels[key]} 은(는) {low}~{high} 범위여야 합니다.")
        rules[key] = value

    if rules["booking_min_days"] > rules["booking_max_days"]:
        raise ApiError("최소 신청 기한은 예약 가능 범위보다 클 수 없습니다.")

    conn.execute(
        "INSERT INTO place_settings (key, value) VALUES ('booking_rules', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps(rules, ensure_ascii=False),),
    )
    conn.commit()
    return rules


def booking_window(conn=None, today=None, rules=None):
    """예약 가능 날짜 범위 (오늘+min ~ 오늘+max). 기관 규칙을 따른다."""
    rules = rules or get_booking_rules(conn)
    today = today or date.today()
    return (today + timedelta(days=rules["booking_min_days"]),
            today + timedelta(days=rules["booking_max_days"]))


def add_months(base, months):
    """base 로부터 months 개월 뒤 날짜 (월말 보정)."""
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def penalty_until(conn, name, contact, rules=None):
    """노쇼/이용확인 미실시(규정 15·16)로 인한 재대관 제한 해제일.

    제한이 없으면 None. 마지막 위반 이용일 + penalty_months 가 해제일이며,
    그 날짜부터 다시 대관할 수 있다.
    """
    rules = rules or get_booking_rules(conn)
    months = rules["penalty_months"]
    if months <= 0 or not name or not contact:
        return None

    placeholders = ",".join("?" for _ in config.PENALTY_ATTENDANCE)
    row = conn.execute(
        "SELECT MAX(start_time) AS last FROM reservations"
        " WHERE applicant_name = ? AND applicant_contact = ?"
        f" AND attendance IN ({placeholders})",
        [name, contact, *config.PENALTY_ATTENDANCE],
    ).fetchone()
    if not row or not row["last"]:
        return None
    return add_months(datetime.strptime(row["last"], ISO).date(), months)


def cancel_deadline(start_time_iso, rules):
    """신청자가 직접 취소할 수 있는 마지막 날짜. 0 이면 당일까지 허용."""
    days = rules["cancel_deadline_days"]
    start_date = datetime.strptime(start_time_iso, ISO).date()
    return start_date - timedelta(days=days)


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
        "SELECT r.id, r.facility_id, r.weekday, r.start_hour, r.end_hour, r.title, r.kind,"
        " f.name AS facility_name"
        " FROM recurring_blocks r LEFT JOIN facilities f ON f.id = r.facility_id"
        " ORDER BY r.weekday, r.start_hour"
    ).fetchall()
    return [{**dict(r), "kind_label": config.RECURRING_KIND_LABELS.get(r["kind"], "기타")}
            for r in rows]


def _parse_recurring_block(conn, data):
    """정기 고정활동 입력 검증 → 저장 값 튜플.

    동아리 정기활동뿐 아니라 센터 프로그램·시설 점검·외부 정기대관도 들어오므로
    유형(kind)을 함께 받는다. 활동명은 현황표에 그대로 표시되어 없으면 무슨 일정인지
    알 수 없으므로 필수.
    """
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

    kind = (data.get("kind") or "etc").strip()
    if kind not in config.RECURRING_KINDS:
        raise ApiError("활동 유형이 올바르지 않습니다. (동아리/프로그램/기타)")

    title = (data.get("title") or "").strip()[:100]
    if not title:
        label = config.RECURRING_KIND_LABELS[kind]
        hint = {"club": "동아리명", "program": "프로그램명"}.get(kind, "활동명")
        raise ApiError(f"{label} 정기활동의 {hint}을(를) 입력해주세요.")

    return facility_id, weekday, start_hour, end_hour, title, kind


def add_recurring_block(conn, data):
    values = _parse_recurring_block(conn, data)
    cur = conn.execute(
        "INSERT INTO recurring_blocks (facility_id, weekday, start_hour, end_hour, title, kind)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        values,
    )
    conn.commit()
    return {"ok": True, "id": cur.lastrowid}


def update_recurring_block(conn, block_id, data):
    """등록된 정기활동 수정 — 학기마다 시간·담당이 바뀌는 경우가 잦다."""
    if conn.execute(
        "SELECT 1 FROM recurring_blocks WHERE id = ?", (block_id,)
    ).fetchone() is None:
        raise ApiError("정기활동을 찾을 수 없습니다.", 404)
    facility_id, weekday, start_hour, end_hour, title, kind = _parse_recurring_block(conn, data)
    conn.execute(
        "UPDATE recurring_blocks SET facility_id = ?, weekday = ?, start_hour = ?,"
        " end_hour = ?, title = ?, kind = ? WHERE id = ?",
        (facility_id, weekday, start_hour, end_hour, title, kind, block_id),
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
        "SELECT start_hour, end_hour, title, kind FROM recurring_blocks"
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


def _positive_int(value, message):
    """빈값은 0. 숫자가 아니거나 음수면 ApiError."""
    if value in (None, ""):
        return 0
    try:
        num = int(value)
    except (ValueError, TypeError):
        raise ApiError(message)
    if num < 0:
        raise ApiError(message)
    return num


def parse_participants(payload):
    """이용 인원을 {연령대: {male, female, unspecified}} 형태로 정규화.

    종이 서식이 연령대마다 남/여를 따로 받으므로 성별까지 저장한다.
    레거시 데이터( {"middle": 3} 처럼 성별 없이 저장된 값 )는 손실 없이
    unspecified 버킷으로 읽어들인다 — 신규 신청은 male/female 만 채운다.
    """
    payload = payload or {}
    if not isinstance(payload, dict):
        raise ApiError("참가 인원 값이 올바르지 않습니다.")

    result = {}
    for band in config.PARTICIPANT_BANDS:
        raw = payload.get(band, 0)
        if isinstance(raw, dict):
            counts = {
                g: _positive_int(raw.get(g), "참가 인원 값이 올바르지 않습니다.")
                for g in config.PARTICIPANT_GENDERS
            }
        else:
            # 레거시(성별 미구분) 숫자.
            counts = {g: 0 for g in config.PARTICIPANT_GENDERS}
            counts["unspecified"] = _positive_int(raw, "참가 인원 값이 올바르지 않습니다.")
        result[band] = counts
    return result


def total_participants(participants):
    """정규화된 인원 dict 의 총합."""
    return sum(sum(counts.values()) for counts in (participants or {}).values())


def parse_equipment(payload):
    """필요 물품을 [{name, qty}] 형태로 정규화.

    종이 서식의 '마이크 ( )대' 처럼 수량이 필요한 항목이 있어 문자열이 아닌
    객체로 저장한다. 레거시(문자열 배열)도 수량 1로 읽어들인다.
    """
    if payload in (None, ""):
        return []
    if not isinstance(payload, list):
        raise ApiError("필요 물품 값이 올바르지 않습니다.")

    items, seen = [], set()
    for entry in payload:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            raise ApiError("필요 물품 값이 올바르지 않습니다.")
        name = (entry.get("name") or "").strip()[:100]
        if not name or name in seen:
            continue
        qty = _positive_int(entry.get("qty", 1), "필요 물품 수량이 올바르지 않습니다.") or 1
        seen.add(name)
        items.append({"name": name, "qty": qty})
    return items


MAX_ACTIVITY_LEN = 200


def parse_applicant(data, require_activity=False):
    """종이 서식의 '신청인 / 연락처 / 활동내용' 블록을 정규화.

    require_activity 는 공개 신청에서만 True — 관리자 직접 추가는 활동내용을
    나중에 채울 수 있게 비워둘 수 있다.
    """
    name = (data.get("name") or "").strip()
    contact = (data.get("contact") or "").strip()
    activity = (data.get("activity") or "").strip()
    if len(activity) > MAX_ACTIVITY_LEN:
        raise ApiError(f"활동내용은 {MAX_ACTIVITY_LEN}자를 넘을 수 없습니다.")
    if require_activity and not activity:
        raise ApiError("활동내용을 입력해주세요. (예: 춤연습, 밴드합주, 보드게임)")

    age = data.get("age", data.get("applicant_age"))
    age = _positive_int(age, "나이 값이 올바르지 않습니다.") or None
    if age is not None and age > 120:
        raise ApiError("나이 값이 올바르지 않습니다.")

    return {
        "name": name,
        "contact": contact,
        "age": age,
        "school": (data.get("school") or "").strip() or None,
        "club": (data.get("club") or "").strip() or None,
        # 종이 서식의 '주소 또는 E-Mail' 칸.
        "address": (data.get("address") or "").strip()[:200] or None,
        "activity": activity,
    }


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
def _to_dict(conn, row, include_facility=True, rules=None):
    """예약 1건 직렬화. rules 를 넘기면 목록 조회 시 규칙 재조회를 피한다."""
    rules = rules or get_booking_rules(conn)
    cancellable = (
        not row["is_deleted"]
        and row["status"] in ("pending", "confirmed")
        and date.today() <= cancel_deadline(row["start_time"], rules)
    )
    data = {
        "id": row["id"],
        "access_id": row["access_id"],
        "facility_id": row["facility_id"],
        "applicant_name": row["applicant_name"],
        "applicant_contact": row["applicant_contact"],
        "applicant_school": row["applicant_school"],
        "applicant_club": row["applicant_club"],
        "applicant_age": row["applicant_age"],
        "applicant_address": row["applicant_address"],
        "activity": row["activity"] or "",
        "status": row["status"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "participant_info": parse_participants(json.loads(row["participant_info"] or "{}")),
        "requested_equipment": parse_equipment(json.loads(row["requested_equipment"] or "[]")),
        "is_deleted": bool(row["is_deleted"]),
        "reject_reason": row["reject_reason"],
        "attendance": row["attendance"] or "",
        "attendance_at": row["attendance_at"],
        # 신청자가 지금 스스로 취소할 수 있는지 (규칙을 프론트에 중복 구현하지 않도록).
        "can_cancel": cancellable,
        "cancel_deadline": cancel_deadline(row["start_time"], rules).isoformat(),
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


def _to_dicts(conn, rows):
    """여러 건 직렬화 — 규칙은 한 번만 읽는다."""
    rules = get_booking_rules(conn)
    return [_to_dict(conn, r, rules=rules) for r in rows]


# ----------------------------------------------------------------------
# 공개 — 현황 / 생성 / 조회 / 취소
# ----------------------------------------------------------------------
def availability(conn, date_str):
    min_date, max_date = booking_window(conn)
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

    rules = get_booking_rules(conn)
    min_date, max_date = booking_window(conn, rules=rules)
    if target_date < min_date or target_date > max_date:
        raise ApiError(
            f"예약은 {min_date.isoformat()} 부터 {max_date.isoformat()} 까지만 가능합니다."
        )

    cfg = day_config(conn, target_date)
    _guard_open(cfg)

    applicant = parse_applicant(data, require_activity=True)
    name, contact = applicant["name"], applicant["contact"]
    if not name or not contact:
        raise ApiError("신청인 이름과 연락처를 입력해주세요.")

    # 규정 15·16 — 노쇼/이용확인 미실시 이력이 있으면 제한 기간 동안 대관 불가.
    blocked_until = penalty_until(conn, name, contact, rules)
    if blocked_until and target_date < blocked_until:
        raise ApiError(
            f"취소 신청 없이 미사용하거나 이용확인을 받지 않은 이력이 있어 "
            f"{blocked_until.isoformat()} 부터 대관하실 수 있습니다. "
            f"(자세한 사항은 담당자에게 문의해주세요.)"
        )

    participants = parse_participants(data.get("participants"))
    if total_participants(participants) < 1:
        raise ApiError("참가 인원을 최소 1명 이상 입력해주세요.")

    equipment = parse_equipment(data.get("equipment"))

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
        "  applicant_club, applicant_age, applicant_address, activity, status,"
        "  start_time, end_time, participant_info,"
        "  requested_equipment, is_deleted, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, 0, ?)",
        (access_id, facility_id, name, contact, applicant["school"], applicant["club"],
         applicant["age"], applicant["address"], applicant["activity"],
         start_iso, end_iso,
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
    return _to_dicts(conn, rows)


def cancel(conn, res_id):
    """신청자 직접 취소 — 이용일 기준 취소 마감을 지나면 거절한다(규정: 대관 1일 전까지).

    마감 이후 취소는 담당자가 관리자 화면에서 상태를 바꾸는 경로만 남긴다.
    """
    row = conn.execute("SELECT * FROM reservations WHERE id = ?", (res_id,)).fetchone()
    if row is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    if row["is_deleted"] or row["status"] not in ("confirmed", "pending"):
        raise ApiError("이미 취소되었거나 유효하지 않은 예약입니다.")

    rules = get_booking_rules(conn)
    deadline = cancel_deadline(row["start_time"], rules)
    if date.today() > deadline:
        days = rules["cancel_deadline_days"]
        raise ApiError(
            f"이용일 {days}일 전({deadline.isoformat()})까지만 직접 취소하실 수 있습니다. "
            f"담당자에게 문의해주세요."
        )

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
    return _to_dicts(conn, rows)


def requests_list(conn):
    rows = conn.execute(
        "SELECT * FROM reservations WHERE status = 'pending' AND is_deleted = 0"
        " ORDER BY start_time"
    ).fetchall()
    return _to_dicts(conn, rows)


def dashboard(conn, date_str=None):
    try:
        selected_date = (
            datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
        )
    except (ValueError, TypeError):
        selected_date = date.today()

    pending = requests_list(conn)
    rules = get_booking_rules(conn)

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
        groups_map[fname].append(_to_dict(conn, r, rules=rules))

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
      type='free' | 'res'(res_id,status,name,contact) | 'block'(title,kind)
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
                cell[h] = {"key": f"blk-{i}", "type": "block",
                           "title": b["title"] or "정기활동", "kind": b["kind"] or "etc"}
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
                    seg["kind"] = here["kind"]
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

    # 실제 예약 간 중복(이중 예약)은 여전히 차단.
    if has_overlap(conn, facility_id, start_iso, end_iso):
        raise ApiError("선택하신 시설/시간에 이미 등록된 예약이 있어 추가할 수 없습니다.")

    warnings = []
    if not cfg["is_open"]:
        warnings.append(f"휴무일({cfg['closed_reason']})에 예약을 추가했습니다.")
    if has_block_overlap(conn, facility_id, target_date.weekday(), start_hour, end_hour):
        warnings.append("정기 고정활동과 겹치는 시간에 예약을 추가했습니다.")

    access_id = str(uuid.uuid4())
    now = datetime.now().strftime(ISO)
    participants = parse_participants(data.get("participants"))
    applicant = parse_applicant(data)
    # 동아리 단기대관은 개인 신청인이 없다 — 이름을 비우면 동아리명을 표시명으로 쓴다
    # (현황표/목록에서 빈칸으로 보이지 않도록).
    if not applicant["name"] and applicant["club"]:
        applicant["name"] = applicant["club"]
    cur = conn.execute(
        "INSERT INTO reservations"
        " (access_id, facility_id, applicant_name, applicant_contact, applicant_school,"
        "  applicant_club, applicant_age, applicant_address, activity, status,"
        "  start_time, end_time, participant_info,"
        "  requested_equipment, is_deleted, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, 0, ?)",
        (access_id, facility_id,
         applicant["name"], applicant["contact"], applicant["school"], applicant["club"],
         applicant["age"], applicant["address"], applicant["activity"],
         start_iso, end_iso,
         json.dumps(participants, ensure_ascii=False),
         json.dumps(parse_equipment(data.get("equipment")), ensure_ascii=False), now),
    )
    conn.commit()
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

    if has_overlap(conn, facility_id, start_iso, end_iso, exclude_res_id=res_id):
        raise ApiError("선택하신 시설/시간에 이미 등록된 다른 예약이 있어 변경할 수 없습니다.")

    warnings = []
    if not cfg["is_open"]:
        warnings.append(f"휴무일({cfg['closed_reason']})로 지정된 날짜입니다.")
    if has_block_overlap(conn, facility_id, target_date.weekday(), start_hour, end_hour):
        warnings.append("정기 고정활동과 겹치는 시간입니다.")

    new_status = data.get("status")
    if new_status not in ("pending", "confirmed", "cancelled", "rejected"):
        raise ApiError("올바르지 않은 상태 값입니다.")

    is_deleted = 1 if new_status in ("cancelled", "rejected") else 0
    reject_reason = data.get("reject_reason", "") if new_status == "rejected" else None

    applicant = parse_applicant(data)
    conn.execute(
        "UPDATE reservations SET facility_id = ?, start_time = ?, end_time = ?,"
        " applicant_name = ?, applicant_contact = ?, applicant_school = ?,"
        " applicant_club = ?, applicant_age = ?, applicant_address = ?, activity = ?,"
        " participant_info = ?, requested_equipment = ?,"
        " status = ?, is_deleted = ?, reject_reason = ? WHERE id = ?",
        (facility_id, start_iso, end_iso,
         applicant["name"], applicant["contact"], applicant["school"], applicant["club"],
         applicant["age"], applicant["address"], applicant["activity"],
         json.dumps(parse_participants(data.get("participants")), ensure_ascii=False),
         json.dumps(parse_equipment(data.get("equipment")), ensure_ascii=False),
         new_status, is_deleted, reject_reason, res_id),
    )
    conn.commit()
    return {"ok": True, "message": "예약 정보가 성공적으로 수정되었습니다.", "warnings": warnings}


ATTENDANCE_LABELS = {
    "": "미처리",
    "attended": "이용확인 완료",
    "no_show": "노쇼(미사용)",
    "unverified": "이용확인 미실시",
}


def set_attendance(conn, res_id, value):
    """이용 결과 기록 — 노쇼/이용확인 미실시는 재대관 제한(규정 15·16)으로 이어진다."""
    if value not in config.ATTENDANCE_VALUES:
        raise ApiError("이용 결과 값이 올바르지 않습니다.")

    row = conn.execute(
        "SELECT applicant_name, applicant_contact, start_time FROM reservations WHERE id = ?",
        (res_id,),
    ).fetchone()
    if row is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)

    conn.execute(
        "UPDATE reservations SET attendance = ?, attendance_at = ? WHERE id = ?",
        (value, datetime.now().strftime(ISO) if value else None, res_id),
    )
    conn.commit()

    rules = get_booking_rules(conn)
    blocked_until = penalty_until(conn, row["applicant_name"], row["applicant_contact"], rules)
    message = f"{row['applicant_name']}님의 이용 결과를 '{ATTENDANCE_LABELS[value]}'(으)로 기록했습니다."
    if value in config.PENALTY_ATTENDANCE and blocked_until:
        message += f" {blocked_until.isoformat()} 까지 대관이 제한됩니다."
    return {
        "ok": True,
        "message": message,
        "attendance": value,
        "blocked_until": blocked_until.isoformat() if blocked_until else None,
    }


def extend(conn, res_id):
    """현장 연장 — 뒤이은 대관예약이 없을 때 종료 시각을 규칙만큼 늘린다.

    운영 종료 시각·다른 예약·정기 고정활동에 걸리면 거절한다.
    """
    row = conn.execute("SELECT * FROM reservations WHERE id = ?", (res_id,)).fetchone()
    if row is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    if row["is_deleted"] or row["status"] != "confirmed":
        raise ApiError("확정된 예약만 연장할 수 있습니다.")

    rules = get_booking_rules(conn)
    hours = rules["extension_hours"]
    if hours <= 0:
        raise ApiError("이 기관은 현장 연장을 운영하지 않습니다.")

    end_dt = datetime.strptime(row["end_time"], ISO)
    target_date = datetime.strptime(row["start_time"], ISO).date()
    new_end_dt = end_dt + timedelta(hours=hours)

    cfg = day_config(conn, target_date)
    if end_dt.hour + hours > cfg["close_hour"]:
        raise ApiError(
            f"운영 종료 시각({cfg['close_hour']:02d}:00)을 넘겨 연장할 수 없습니다."
        )

    new_end_iso = new_end_dt.strftime(ISO)
    if has_overlap(conn, row["facility_id"], row["end_time"], new_end_iso, exclude_res_id=res_id):
        raise ApiError("뒤이은 시간에 다른 대관예약이 있어 연장할 수 없습니다.")
    if has_block_overlap(conn, row["facility_id"], target_date.weekday(),
                         end_dt.hour, end_dt.hour + hours):
        raise ApiError("뒤이은 시간에 정기 고정활동이 있어 연장할 수 없습니다.")

    conn.execute("UPDATE reservations SET end_time = ? WHERE id = ?", (new_end_iso, res_id))
    conn.commit()
    return {
        "ok": True,
        "message": f"{row['applicant_name']}님의 예약을 {hours}시간 연장했습니다."
                   f" (~{new_end_dt.strftime('%H:%M')})",
        "end_time": new_end_iso,
    }


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
