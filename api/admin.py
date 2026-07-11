"""Admin (manage) API endpoints. All routes except login require a session."""

import uuid
from datetime import datetime, date, timedelta
from itertools import groupby

from flask import Blueprint, current_app, jsonify, request, session

from models import db, Facility, Reservation
from .helpers import (
    ApiError,
    admin_required,
    booked_hours_for,
    day_bounds,
    has_overlap,
    hours_to_datetimes,
    parse_date,
    parse_participants,
    resolve_hours,
)

admin_bp = Blueprint("admin", __name__)


@admin_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    if password == current_app.config["MANAGE_PASSWORD"]:
        session["manage_logged_in"] = True
        return jsonify({"ok": True})
    raise ApiError("비밀번호가 올바르지 않습니다.", 401)


@admin_bp.post("/logout")
def logout():
    session.pop("manage_logged_in", None)
    return jsonify({"ok": True})


@admin_bp.get("/me")
def me():
    return jsonify({"logged_in": bool(session.get("manage_logged_in"))})


@admin_bp.get("/dashboard")
@admin_required
def dashboard():
    date_str = request.args.get("date")
    try:
        selected_date = (
            datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
        )
    except ValueError:
        selected_date = date.today()

    pending = (
        Reservation.query.filter_by(status="pending", is_deleted=False)
        .order_by(Reservation.start_time)
        .all()
    )

    start_of_day, end_of_day = day_bounds(selected_date)
    todays = (
        Reservation.query.filter(
            Reservation.start_time >= start_of_day,
            Reservation.start_time < end_of_day,
            Reservation.is_deleted == False,  # noqa: E712
        )
        .join(Facility)
        .order_by(Facility.name, Reservation.start_time)
        .all()
    )

    # Group today's reservations by facility name for the daily panel.
    groups = []
    for facility_name, items in groupby(todays, key=lambda r: r.facility.name):
        groups.append(
            {
                "facility_name": facility_name,
                "reservations": [r.to_dict() for r in items],
            }
        )

    return jsonify(
        {
            "selected_date": selected_date.isoformat(),
            "prev_date": (selected_date - timedelta(days=1)).isoformat(),
            "next_date": (selected_date + timedelta(days=1)).isoformat(),
            "pending_count": len(pending),
            "pending_preview": [r.to_dict() for r in pending[:3]],
            "todays_groups": groups,
        }
    )


@admin_bp.get("/requests")
@admin_required
def requests_list():
    pending = (
        Reservation.query.filter_by(status="pending", is_deleted=False)
        .order_by(Reservation.start_time)
        .all()
    )
    return jsonify([r.to_dict() for r in pending])


@admin_bp.get("/reservations")
@admin_required
def all_reservations():
    reservations = Reservation.query.order_by(Reservation.start_time.desc()).all()
    return jsonify([r.to_dict() for r in reservations])


@admin_bp.get("/reservations/<int:res_id>")
@admin_required
def get_reservation(res_id):
    reservation = Reservation.query.get(res_id)
    if reservation is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    return jsonify(reservation.to_dict())


@admin_bp.get("/calendar-events")
@admin_required
def calendar_events():
    reservations = Reservation.query.filter_by(is_deleted=False).all()
    events = []
    for res in reservations:
        events.append(
            {
                "id": res.id,
                "title": f"[{res.facility.name}] {res.applicant_name}",
                "start": res.start_time.isoformat(),
                "end": res.end_time.isoformat(),
                "color": "#ff9800" if res.status == "pending" else "#4CAF50",
            }
        )
    return jsonify(events)


@admin_bp.get("/booked-times")
@admin_required
def admin_booked_times():
    facility_id = request.args.get("facility_id", type=int)
    date_str = request.args.get("date")
    if not facility_id or not date_str:
        return jsonify([])
    target_date = parse_date(date_str)
    exclude = request.args.get("exclude_res_id", type=int)
    return jsonify(sorted(booked_hours_for(facility_id, target_date, exclude)))


@admin_bp.post("/reservations")
@admin_required
def create_reservation():
    """Admin creates an already-confirmed reservation (0 participants allowed)."""
    data = request.get_json(silent=True) or {}

    facility_id = data.get("facility_id")
    facility = Facility.query.get(facility_id) if facility_id else None
    if facility is None:
        raise ApiError("시설을 선택해주세요.")

    target_date = parse_date(data.get("date"))
    start_hour, end_hour = resolve_hours(data.get("hours"))
    start_dt, end_dt = hours_to_datetimes(target_date, start_hour, end_hour)

    if has_overlap(facility.id, start_dt, end_dt):
        raise ApiError("선택하신 시설/시간에 이미 등록된 예약이 있어 추가할 수 없습니다.")

    reservation = Reservation(
        access_id=str(uuid.uuid4()),
        facility_id=facility.id,
        applicant_name=(data.get("name") or "").strip(),
        applicant_contact=(data.get("contact") or "").strip(),
        applicant_school=(data.get("school") or "").strip() or None,
        applicant_club=(data.get("club") or "").strip() or None,
        start_time=start_dt,
        end_time=end_dt,
        participant_info=parse_participants(data.get("participants") or {}),
        requested_equipment=data.get("equipment") or [],
        status="confirmed",
        is_deleted=False,
    )
    db.session.add(reservation)
    db.session.commit()
    return jsonify({"ok": True, "id": reservation.id}), 201


@admin_bp.put("/reservations/<int:res_id>")
@admin_required
def update_reservation(res_id):
    reservation = Reservation.query.get(res_id)
    if reservation is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)

    data = request.get_json(silent=True) or {}

    facility_id = data.get("facility_id")
    if not facility_id or Facility.query.get(facility_id) is None:
        raise ApiError("시설을 선택해주세요.")

    target_date = parse_date(data.get("date"))
    start_hour, end_hour = resolve_hours(data.get("hours"))
    start_dt, end_dt = hours_to_datetimes(target_date, start_hour, end_hour)

    if has_overlap(facility_id, start_dt, end_dt, exclude_res_id=reservation.id):
        raise ApiError("선택하신 시설/시간에 이미 등록된 다른 예약이 있어 변경할 수 없습니다.")

    reservation.facility_id = facility_id
    reservation.start_time = start_dt
    reservation.end_time = end_dt
    reservation.applicant_name = (data.get("name") or "").strip()
    reservation.applicant_contact = (data.get("contact") or "").strip()
    reservation.applicant_school = (data.get("school") or "").strip() or None
    reservation.applicant_club = (data.get("club") or "").strip() or None
    reservation.participant_info = parse_participants(data.get("participants") or {})
    reservation.requested_equipment = data.get("equipment") or []

    new_status = data.get("status")
    if new_status not in ["pending", "confirmed", "cancelled", "rejected"]:
        raise ApiError("올바르지 않은 상태 값입니다.")
    reservation.status = new_status

    if new_status in ["cancelled", "rejected"]:
        reservation.is_deleted = True
        reservation.reject_reason = (
            data.get("reject_reason", "") if new_status == "rejected" else None
        )
    else:
        reservation.is_deleted = False
        reservation.reject_reason = None

    db.session.commit()
    return jsonify({"ok": True, "message": "예약 정보가 성공적으로 수정되었습니다."})


@admin_bp.post("/reservations/<int:res_id>/approve")
@admin_required
def approve(res_id):
    reservation = Reservation.query.get(res_id)
    if reservation is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    reservation.status = "confirmed"
    reservation.is_deleted = False
    db.session.commit()
    return jsonify({"ok": True, "message": f"{reservation.applicant_name}님의 예약이 승인되었습니다."})


@admin_bp.post("/reservations/<int:res_id>/reject")
@admin_required
def reject(res_id):
    reservation = Reservation.query.get(res_id)
    if reservation is None:
        raise ApiError("예약 정보를 찾을 수 없습니다.", 404)
    data = request.get_json(silent=True) or {}
    reservation.status = "rejected"
    reservation.is_deleted = True
    reservation.reject_reason = data.get("reject_reason") or "관리자 직권 거절"
    db.session.commit()
    return jsonify(
        {"ok": True, "message": f"{reservation.applicant_name}님의 예약이 거절 처리되었습니다."}
    )
