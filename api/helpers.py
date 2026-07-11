"""Shared business logic and helpers for the API blueprints."""

from datetime import datetime, timedelta
from functools import wraps

from flask import current_app, jsonify, session

from models import Reservation

# Statuses that occupy a time slot (block new bookings).
ACTIVE_STATUSES = ["confirmed", "pending"]

# Operating hours: bookable start hours are OPEN_HOUR .. CLOSE_HOUR-1.
OPEN_HOUR = 9
CLOSE_HOUR = 18

MAX_HOURS_PUBLIC = 2
WEEKLY_LIMIT = 2


class ApiError(Exception):
    """Raised to return a JSON error with a specific HTTP status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def error_response(message, status=400):
    return jsonify({"error": message}), status


def booking_window(today=None):
    """Return (min_date, max_date) the public may book within."""
    today = today or datetime.today().date()
    min_days = current_app.config["BOOKING_MIN_DAYS"]
    max_days = current_app.config["BOOKING_MAX_DAYS"]
    return today + timedelta(days=min_days), today + timedelta(days=max_days)


def parse_date(date_str):
    """Parse YYYY-MM-DD -> date, raising ApiError on failure."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ApiError("날짜 형식이 올바르지 않습니다.")


def day_bounds(target_date):
    start = datetime.combine(target_date, datetime.min.time())
    end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
    return start, end


def booked_hours_for(facility_id, target_date, exclude_res_id=None):
    """Set of hour ints already occupied for a facility on a given date."""
    start, end = day_bounds(target_date)
    query = Reservation.query.filter(
        Reservation.facility_id == facility_id,
        Reservation.start_time >= start,
        Reservation.start_time < end,
        Reservation.is_deleted == False,  # noqa: E712
        Reservation.status.in_(ACTIVE_STATUSES),
    )
    if exclude_res_id:
        query = query.filter(Reservation.id != exclude_res_id)

    hours = set()
    for res in query.all():
        for hour in range(res.start_time.hour, res.end_time.hour):
            hours.add(hour)
    return hours


def resolve_hours(selected_hours, max_hours=None):
    """Validate a list of selected hour ints and return (start_dt_hour, end_hour).

    Ensures the hours are within operating range, contiguous, and (optionally)
    within a maximum count. Raises ApiError with a user-facing message.
    """
    if not selected_hours:
        raise ApiError("이용 시간을 하나 이상 선택해주세요.")

    try:
        hours = sorted(int(h) for h in selected_hours)
    except (ValueError, TypeError):
        raise ApiError("이용 시간 값이 올바르지 않습니다.")

    if max_hours is not None and len(hours) > max_hours:
        raise ApiError(f"예약은 하루에 최대 {max_hours}시간까지만 가능합니다.")

    for hour in hours:
        if not (OPEN_HOUR <= hour < CLOSE_HOUR):
            raise ApiError("운영 시간(09:00~18:00) 내에서만 예약할 수 있습니다.")

    start_hour = hours[0]
    end_hour = hours[-1] + 1

    if end_hour - start_hour != len(hours):
        raise ApiError("이용 시간은 연속된 시간으로만 선택 가능합니다.")

    return start_hour, end_hour


def hours_to_datetimes(target_date, start_hour, end_hour):
    start_dt = datetime.combine(target_date, datetime.min.time().replace(hour=start_hour))
    end_dt = datetime.combine(target_date, datetime.min.time().replace(hour=end_hour))
    return start_dt, end_dt


def has_overlap(facility_id, start_dt, end_dt, exclude_res_id=None):
    query = Reservation.query.filter(
        Reservation.facility_id == facility_id,
        Reservation.start_time < end_dt,
        Reservation.end_time > start_dt,
        Reservation.is_deleted == False,  # noqa: E712
        Reservation.status.in_(ACTIVE_STATUSES),
    )
    if exclude_res_id:
        query = query.filter(Reservation.id != exclude_res_id)
    return query.first() is not None


def parse_participants(payload):
    """Coerce participant counts to ints, defaulting missing keys to 0."""
    keys = ["elementary", "middle", "high", "teen", "adult"]
    result = {}
    for key in keys:
        try:
            result[key] = int(payload.get(key, 0) or 0)
        except (ValueError, TypeError):
            raise ApiError("참가 인원 값이 올바르지 않습니다.")
    return result


def admin_required(view):
    """Decorator guarding admin-only endpoints via the session flag."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("manage_logged_in"):
            return error_response("관리자 로그인이 필요합니다.", 401)
        return view(*args, **kwargs)

    return wrapper
