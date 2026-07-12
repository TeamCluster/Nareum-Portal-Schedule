"""나름 대관 예약 시스템 — Flask JSON API (멀티테넌트).

URL 구조:
  /api/super/...            슈퍼 관리자 (전 기관 총괄 — 기관 CRUD/비번관리)
  /api/<slug>/...           기관 공개 (예약 신청/조회)
  /api/<slug>/admin/...     기관 관리자 (예약 승인/관리, 시설 관리)

세션 구조:
  super_logged_in: bool
  place_admins: dict[str, bool]    # 기관별 독립 로그인 상태  예: {"nareum": True}

DB 초기화:
  db.init_super_db() 가 첫 실행 시 슈퍼 비밀번호/SECRET_KEY/기본 기관을 생성.
  기관 DB(<slug>.sqlite3)는 place_service.add_place 가 만든다.
"""
import io
import os
import sys
from functools import wraps

from flask import Flask, g, jsonify, request, session
from flask_cors import CORS

import config
import db
from config import is_valid_slug
from db import get_place_db, get_super_db
from services import (
    facility_service,
    place_service,
    reservation_service,
    super_service,
)
from services.errors import ApiError

# Windows 콘솔 UTF-8 (한글 로그)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ["PYTHONIOENCODING"] = "utf-8"


def create_app():
    app = Flask(__name__)

    db.init_super_db()
    app.secret_key = db.get_secret_key()
    app.teardown_appcontext(db.close_dbs)

    app.config.update(
        SESSION_COOKIE_SAMESITE=config.SESSION_COOKIE_SAMESITE,
        SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
        SESSION_COOKIE_HTTPONLY=True,
    )
    CORS(app, resources={r"/api/*": {"origins": config.CORS_ORIGINS}},
         supports_credentials=True)

    @app.errorhandler(ApiError)
    def _handle_api_error(err):
        return jsonify({"error": err.message}), err.status

    _register_meta(app)
    _register_super(app)
    _register_place(app)
    return app


# ======================================================================
#  데코레이터
# ======================================================================
def super_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("super_logged_in"):
            return jsonify({"error": "슈퍼 관리자 로그인이 필요합니다."}), 401
        return view(*args, **kwargs)
    return wrapped


def _resolve_place_or_404(slug):
    """slug 유효성 + 존재 확인. 문제 있으면 ApiError raise, 아니면 place dict."""
    if not is_valid_slug(slug):
        raise ApiError("잘못된 기관 식별자입니다.", 404)
    place = place_service.get_place(slug)
    if not place:
        raise ApiError("기관을 찾을 수 없습니다.", 404)
    return place


def place_required(view):
    """URL <slug> 가 유효+존재해야 함 (공개 라우트용). g.place 에 기관정보 주입."""
    @wraps(view)
    def wrapped(slug, *args, **kwargs):
        g.place = _resolve_place_or_404(slug)
        return view(slug, *args, **kwargs)
    return wrapped


def place_admin_required(view):
    """기관 검증 + 해당 기관 관리자 로그인 필요."""
    @wraps(view)
    def wrapped(slug, *args, **kwargs):
        g.place = _resolve_place_or_404(slug)
        if not session.get("place_admins", {}).get(slug):
            return jsonify({"error": "기관 관리자 로그인이 필요합니다."}), 401
        return view(slug, *args, **kwargs)
    return wrapped


# ======================================================================
#  메타 (헬스/공통)
# ======================================================================
def _register_meta(app):
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": config.SERVICE_NAME})

    @app.get("/api/config")
    def public_config():
        return jsonify({
            "service_name": config.SERVICE_NAME,
            "booking_min_days": config.BOOKING_MIN_DAYS,
            "booking_max_days": config.BOOKING_MAX_DAYS,
        })


# ======================================================================
#  슈퍼 관리자
# ======================================================================
def _register_super(app):
    @app.post("/api/super/login")
    def super_login():
        data = request.get_json(silent=True) or {}
        if super_service.verify_super_password(data.get("password", "")):
            session["super_logged_in"] = True
            return jsonify({"ok": True})
        return jsonify({"error": "비밀번호가 올바르지 않습니다."}), 401

    @app.post("/api/super/logout")
    def super_logout():
        session.pop("super_logged_in", None)
        return jsonify({"ok": True})

    @app.get("/api/super/session")
    def super_session():
        return jsonify({"logged_in": bool(session.get("super_logged_in"))})

    @app.post("/api/super/password")
    @super_required
    def super_password():
        data = request.get_json(silent=True) or {}
        ok, msg = super_service.update_super_password(data.get("new_password", ""))
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    # --- 기관 CRUD ---
    @app.get("/api/super/places")
    @super_required
    def super_places_list():
        return jsonify({"places": place_service.get_places()})

    @app.post("/api/super/places")
    @super_required
    def super_places_add():
        d = request.get_json(silent=True) or {}
        ok, msg, result = place_service.add_place(
            d.get("slug", ""), d.get("full_name", ""), d.get("short_name", ""),
            d.get("password", ""), d.get("address", ""), d.get("phone", ""),
            d.get("email", ""),
        )
        return jsonify({"ok": ok, "message": msg, "result": result}), (200 if ok else 400)

    @app.put("/api/super/places/<slug>")
    @super_required
    def super_places_update(slug):
        d = request.get_json(silent=True) or {}
        ok, msg = place_service.update_place_info(
            slug,
            full_name=d.get("full_name"), short_name=d.get("short_name"),
            address=d.get("address"), phone=d.get("phone"), email=d.get("email"),
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.delete("/api/super/places/<slug>")
    @super_required
    def super_places_delete(slug):
        ok, msg = place_service.delete_place(slug)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 404)

    @app.post("/api/super/places/<slug>/password")
    @super_required
    def super_place_password(slug):
        d = request.get_json(silent=True) or {}
        ok, msg = place_service.update_place_password(slug, d.get("new_password", ""))
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


# ======================================================================
#  기관 (공개 + 관리자)
# ======================================================================
def _register_place(app):
    # ---------------- 인증 ----------------
    @app.post("/api/<slug>/admin/login")
    @place_required
    def place_login(slug):
        data = request.get_json(silent=True) or {}
        if place_service.verify_place_password(slug, data.get("password", "")):
            admins = session.get("place_admins", {})
            admins[slug] = True
            session["place_admins"] = admins
            session.modified = True
            return jsonify({"ok": True})
        return jsonify({"error": "비밀번호가 올바르지 않습니다."}), 401

    @app.post("/api/<slug>/admin/logout")
    @place_required
    def place_logout(slug):
        admins = session.get("place_admins", {})
        admins.pop(slug, None)
        session["place_admins"] = admins
        session.modified = True
        return jsonify({"ok": True})

    @app.get("/api/<slug>/admin/session")
    @place_required
    def place_session(slug):
        return jsonify({"logged_in": bool(session.get("place_admins", {}).get(slug))})

    # ---------------- 공개 ----------------
    @app.get("/api/<slug>/info")
    @place_required
    def place_info(slug):
        p = g.place
        return jsonify({
            "slug": p["slug"], "full_name": p["full_name"], "short_name": p["short_name"],
            "address": p.get("address", ""), "phone": p.get("phone", ""),
            "email": p.get("email", ""),
        })

    @app.get("/api/<slug>/facilities")
    @place_required
    def place_facilities(slug):
        return jsonify(facility_service.get_facilities(get_place_db(slug)))

    @app.get("/api/<slug>/availability")
    @place_required
    def place_availability(slug):
        return jsonify(reservation_service.availability(
            get_place_db(slug), request.args.get("date")))

    @app.get("/api/<slug>/facilities/<int:facility_id>/booked-times")
    @place_required
    def place_booked_times(slug, facility_id):
        date_str = request.args.get("date")
        if not date_str:
            return jsonify([])
        target_date = reservation_service.parse_date(date_str)
        exclude = request.args.get("exclude_res_id", type=int)
        return jsonify(sorted(reservation_service.booked_hours_for(
            get_place_db(slug), facility_id, target_date, exclude)))

    @app.post("/api/<slug>/reservations")
    @place_required
    def place_create_reservation(slug):
        access_id = reservation_service.create_public_reservation(
            get_place_db(slug), request.get_json(silent=True) or {})
        return jsonify({"access_id": access_id}), 201

    @app.get("/api/<slug>/reservations/<string:access_id>")
    @place_required
    def place_get_reservation(slug, access_id):
        return jsonify(reservation_service.get_by_access_id(get_place_db(slug), access_id))

    @app.post("/api/<slug>/reservations/lookup")
    @place_required
    def place_lookup(slug):
        d = request.get_json(silent=True) or {}
        return jsonify(reservation_service.lookup(
            get_place_db(slug), d.get("name"), d.get("contact")))

    @app.post("/api/<slug>/reservations/<int:res_id>/cancel")
    @place_required
    def place_cancel(slug, res_id):
        return jsonify(reservation_service.cancel(get_place_db(slug), res_id))

    # ---------------- 관리자 (보호) ----------------
    @app.get("/api/<slug>/admin/dashboard")
    @place_admin_required
    def place_dashboard(slug):
        return jsonify(reservation_service.dashboard(
            get_place_db(slug), request.args.get("date")))

    @app.get("/api/<slug>/admin/requests")
    @place_admin_required
    def place_requests(slug):
        return jsonify(reservation_service.requests_list(get_place_db(slug)))

    @app.get("/api/<slug>/admin/reservations")
    @place_admin_required
    def place_all_reservations(slug):
        return jsonify(reservation_service.all_reservations(get_place_db(slug)))

    @app.post("/api/<slug>/admin/reservations")
    @place_admin_required
    def place_admin_create(slug):
        result = reservation_service.create_admin_reservation(
            get_place_db(slug), request.get_json(silent=True) or {})
        return jsonify(result), 201

    @app.get("/api/<slug>/admin/reservations/<int:res_id>")
    @place_admin_required
    def place_admin_get(slug, res_id):
        return jsonify(reservation_service.get_reservation(get_place_db(slug), res_id))

    @app.put("/api/<slug>/admin/reservations/<int:res_id>")
    @place_admin_required
    def place_admin_update(slug, res_id):
        return jsonify(reservation_service.update_reservation(
            get_place_db(slug), res_id, request.get_json(silent=True) or {}))

    @app.get("/api/<slug>/admin/calendar-events")
    @place_admin_required
    def place_calendar(slug):
        return jsonify(reservation_service.calendar_events(get_place_db(slug)))

    @app.get("/api/<slug>/admin/booked-times")
    @place_admin_required
    def place_admin_booked_times(slug):
        facility_id = request.args.get("facility_id", type=int)
        date_str = request.args.get("date")
        if not facility_id or not date_str:
            return jsonify([])
        target_date = reservation_service.parse_date(date_str)
        exclude = request.args.get("exclude_res_id", type=int)
        return jsonify(sorted(reservation_service.booked_hours_for(
            get_place_db(slug), facility_id, target_date, exclude)))

    @app.post("/api/<slug>/admin/reservations/<int:res_id>/approve")
    @place_admin_required
    def place_approve(slug, res_id):
        return jsonify(reservation_service.approve(get_place_db(slug), res_id))

    @app.post("/api/<slug>/admin/reservations/<int:res_id>/reject")
    @place_admin_required
    def place_reject(slug, res_id):
        d = request.get_json(silent=True) or {}
        return jsonify(reservation_service.reject(
            get_place_db(slug), res_id, d.get("reject_reason")))

    # ---------------- 관리자 — 시설 관리 ----------------
    @app.get("/api/<slug>/admin/facilities")
    @place_admin_required
    def place_admin_facilities(slug):
        return jsonify(facility_service.get_facilities(get_place_db(slug)))

    @app.post("/api/<slug>/admin/facilities")
    @place_admin_required
    def place_admin_facility_add(slug):
        result = facility_service.add_facility(
            get_place_db(slug), request.get_json(silent=True) or {})
        return jsonify({"ok": True, "facility": result}), 201

    @app.put("/api/<slug>/admin/facilities/<int:facility_id>")
    @place_admin_required
    def place_admin_facility_update(slug, facility_id):
        result = facility_service.update_facility(
            get_place_db(slug), facility_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, "facility": result})

    @app.delete("/api/<slug>/admin/facilities/<int:facility_id>")
    @place_admin_required
    def place_admin_facility_delete(slug, facility_id):
        facility_service.delete_facility(get_place_db(slug), facility_id)
        return jsonify({"ok": True, "message": "시설이 삭제되었습니다."})


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
