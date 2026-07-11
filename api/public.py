"""Public (non-admin) API endpoints."""

import uuid
from datetime import timedelta

from flask import Blueprint, jsonify, request

from models import db, Facility, Reservation
from .helpers import (
    ACTIVE_STATUSES,
    CLOSE_HOUR,
    OPEN_HOUR,
    MAX_HOURS_PUBLIC,
    WEEKLY_LIMIT,
    ApiError,
    booked_hours_for,
    booking_window,
    day_bounds,
    has_overlap,
    hours_to_datetimes,
    parse_date,
    parse_participants,
    resolve_hours,
)

public_bp = Blueprint("public", __name__)


@public_bp.get("/facilities")
def list_facilities():
    facilities = Facility.query.order_by(Facility.id).all()
    return jsonify([f.to_dict() for f in facilities])


@public_bp.get("/availability")
def availability():
    """Facility availability grid for a given date (drives the home page)."""
    date_str = request.args.get("date")
    min_date, max_date = booking_window()

    payload = {
        "min_date": min_date.isoformat(),
        "max_date": max_date.isoformat(),
        "selected_date": date_str,
        "is_reservable": True,
        "facilities": [],
    }

    facilities = Facility.query.order_by(Facility.id).all()

    if not date_str:
        # No date chosen yet: return facilities without slot data.
        payload["facilities"] = [
            {**f.to_dict(), "hours": {}, "sold_out": False} for f in facilities
        ]
        return jsonify(payload)

    target_date = parse_date(date_str)

    if target_date < min_date or target_date > max_date:
        payload["is_reservable"] = False

    for facility in facilities:
        booked = booked_hours_for(facility.id, target_date)
        hours = {
            str(h): ("booked" if h in booked else "available")
            for h in range(OPEN_HOUR, CLOSE_HOUR)
        }
        sold_out = "available" not in hours.values()
        payload["facilities"].append(
            {**facility.to_dict(), "hours": hours, "sold_out": sold_out}
        )

    return jsonify(payload)


@public_bp.get("/facilities/<int:facility_id>/booked-times")
def facility_booked_times(facility_id):
    date_str = request.args.get("date")
    if not date_str:
        return jsonify([])
    target_date = parse_date(date_str)
    exclude = request.args.get("exclude_res_id", type=int)
    return jsonify(sorted(booked_hours_for(facility_id, target_date, exclude)))


@public_bp.post("/reservations")
def create_reservation():
    data = request.get_json(silent=True) or {}

    facility_id = data.get("facility_id")
    facility = Facility.query.get(facility_id) if facility_id else None
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

    name = (data.get("name") or "").strip()
    contact = (data.get("contact") or "").strip()
    if not name or not contact:
        raise ApiError("신청인 이름과 연락처를 입력해주세요.")

    school = (data.get("school") or "").strip() or None
    club = (data.get("club") or "").strip() or None

    participants = parse_participants(data.get("participants") or {})
    if sum(participants.values()) < 1:
        raise ApiError("참가 인원을 최소 1명 이상 입력해주세요.")

    equipment = data.get("equipment") or []

    start_hour, end_hour = resolve_hours(data.get("hours"), max_hours=MAX_HOURS_PUBLIC)
    start_dt, end_dt = hours_to_datetimes(target_date, start_hour, end_hour)

    # Same person, same day: only one reservation allowed.
    day_start, day_end = day_bounds(target_date)
    same_day = Reservation.query.filter(
        Reservation.applicant_name == name,
        Reservation.applicant_contact == contact,
        Reservation.start_time >= day_start,
        Reservation.start_time < day_end,
        Reservation.is_deleted == False,  # noqa: E712
        Reservation.status.in_(ACTIVE_STATUSES),
    ).first()
    if same_day:
        raise ApiError(
            "같은 이름과 연락처로 해당 날짜에 이미 예약된 내역이 있습니다. (하루 1회만 예약 가능)"
        )

    # Weekly limit (Mon–Sun).
    start_of_week = target_date - timedelta(days=target_date.weekday())
    end_of_week = start_of_week + timedelta(days=7)
    weekly_count = Reservation.query.filter(
        Reservation.applicant_name == name,
        Reservation.applicant_contact == contact,
        Reservation.start_time >= day_bounds(start_of_week)[0],
        Reservation.start_time < day_bounds(end_of_week)[0],
        Reservation.is_deleted == False,  # noqa: E712
        Reservation.status.in_(ACTIVE_STATUSES),
    ).count()
    if weekly_count >= WEEKLY_LIMIT:
        raise ApiError(
            f"해당 주간(월~일)에 이미 {WEEKLY_LIMIT}회의 예약이 존재합니다. "
            f"일주일에 최대 {WEEKLY_LIMIT}회까지만 예약하실 수 있습니다."
        )

    if has_overlap(facility.id, start_dt, end_dt):
        raise ApiError("선택하신 시간에 이미 다른 예약이 진행 중입니다.")

    reservation = Reservation(
        access_id=str(uuid.uuid4()),
        facility_id=facility.id,
        applicant_name=name,
        applicant_contact=contact,
        applicant_school=school,
        applicant_club=club,
        start_time=start_dt,
        end_time=end_dt,
        participant_info=participants,
        requested_equipment=equipment,
        status="pending",
        is_deleted=False,
    )
    db.session.add(reservation)
    db.session.commit()

    return jsonify({"access_id": reservation.access_id}), 201


@public_bp.get("/reservations/<string:access_id>")
def get_reservation(access_id):
    reservation = Reservation.query.filter_by(access_id=access_id).first()
    if reservation is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    return jsonify(reservation.to_dict())


@public_bp.post("/reservations/lookup")
def lookup_reservations():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    contact = (data.get("contact") or "").strip()
    if not name or not contact:
        raise ApiError("이름과 연락처를 입력해주세요.")

    reservations = (
        Reservation.query.filter(
            Reservation.applicant_name == name,
            Reservation.applicant_contact == contact,
        )
        .order_by(Reservation.start_time.desc())
        .all()
    )
    return jsonify([r.to_dict() for r in reservations])


@public_bp.post("/reservations/<int:res_id>/cancel")
def cancel_reservation(res_id):
    reservation = Reservation.query.get(res_id)
    if reservation is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)

    if reservation.is_deleted or reservation.status not in ["confirmed", "pending"]:
        raise ApiError("이미 취소되었거나 유효하지 않은 예약입니다.")

    reservation.status = "cancelled"
    reservation.is_deleted = True
    db.session.commit()
    return jsonify({"ok": True, "message": "예약이 정상적으로 취소되었습니다."})
