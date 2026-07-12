"""공통 휴무일/공휴일 — 전 기관 공유. 슈퍼 관리자가 관리.

DB: super.sqlite3 의 common_holidays (date, name, type).
  type='closure' 휴무일(완전 휴무) / type='holiday' 공휴일(기관 설정에 따라 운영).

매년 다수 기관이 동일한 공휴일을 공유하므로 슈퍼에서 한 번만 등록하면
모든 기관에 적용된다. 단 기관은 holiday_excludes 로 특정 날짜를 제외할 수 있다.
"""
from datetime import datetime

from db import get_super_db
from . import korea_holidays
from .errors import ApiError

VALID_TYPES = ("closure", "holiday")


def _parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ApiError("날짜 형식이 올바르지 않습니다.")


def list_common_holidays():
    rows = get_super_db().execute(
        "SELECT id, date, name, type, source FROM common_holidays ORDER BY date"
    ).fetchall()
    return [dict(r) for r in rows]


def add_common_holiday(date_str, name="", type_="holiday"):
    target = _parse_date(date_str)
    if type_ not in VALID_TYPES:
        raise ApiError("유형이 올바르지 않습니다. (closure/holiday)")
    db = get_super_db()
    try:
        db.execute(
            "INSERT INTO common_holidays (date, name, type, source) VALUES (?, ?, ?, 'manual')",
            (target.isoformat(), (name or "").strip(), type_),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise ApiError("이미 등록된 날짜입니다.")
    return {"ok": True}


def delete_common_holiday(holiday_id):
    db = get_super_db()
    cur = db.execute("DELETE FROM common_holidays WHERE id = ?", (holiday_id,))
    db.commit()
    if cur.rowcount == 0:
        raise ApiError("공통 휴무일을 찾을 수 없습니다.", 404)
    return {"ok": True}


def sync_korea_holidays(year):
    """해당 연도의 한국 공휴일을 자동 채운다(대체공휴일 포함).

    같은 연도의 기존 'auto' 항목을 먼저 지우고 최신 공휴일을 다시 넣어,
    공휴일 폐지/변경/대체공휴일 추가 등을 반영. 수동('manual') 항목과
    같은 날짜는 보존(INSERT OR IGNORE).
    반환: {ok, year, count(공식 공휴일 수), added(신규 반영 수)}.
    """
    try:
        y = int(year)
    except (ValueError, TypeError):
        raise ApiError("연도가 올바르지 않습니다.")
    if not (2000 <= y <= 2100):
        raise ApiError("연도는 2000~2100 사이여야 합니다.")

    try:
        official = korea_holidays.fetch_korea_holidays(y)
    except Exception as err:  # noqa: BLE001
        raise ApiError(f"공휴일 데이터를 불러오지 못했습니다: {err}")

    db = get_super_db()
    db.execute("DELETE FROM common_holidays WHERE source = 'auto' AND date LIKE ?", (f"{y}-%",))
    added = 0
    for h in official:
        cur = db.execute(
            "INSERT OR IGNORE INTO common_holidays (date, name, type, source)"
            " VALUES (?, ?, 'holiday', 'auto')",
            (h["date"], h["name"]),
        )
        added += cur.rowcount
    db.commit()
    return {"ok": True, "year": y, "count": len(official), "added": added}


def common_holidays_on(date_iso):
    """특정 날짜(YYYY-MM-DD)의 공통 휴무일 목록 — day_config 계산용."""
    rows = get_super_db().execute(
        "SELECT name, type FROM common_holidays WHERE date = ?", (date_iso,)
    ).fetchall()
    return [dict(r) for r in rows]
