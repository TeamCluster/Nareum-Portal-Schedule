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

from flask import Flask, g, jsonify, redirect, request, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import db
from config import is_valid_slug
from db import get_place_db, get_super_db
from services import (
    facility_service,
    holiday_service,
    image_service,
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

    # 전송 구간 보안(HTTPS) 설정 — CORS 보다 먼저 걸어 리다이렉트가 앞서게 한다.
    _configure_transport_security(app)

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
#  전송 구간 보안 (HTTPS / 보안 헤더)
# ======================================================================
def _configure_transport_security(app):
    """비밀번호 등 전 구간을 TLS 로 보호하기 위한 설정.

    저장(비밀번호)은 이미 pbkdf2 해시라 안전하며, 여기서는 "전송 중" 노출을
    막는 세 가지를 건다:
      1) ProxyFix   — TLS 종료 리버스 프록시 뒤에서 실제 스킴/호스트 인식
      2) HTTPS 강제 — HTTP 요청을 301 로 HTTPS 리다이렉트 (FORCE_HTTPS)
      3) 보안 헤더  — HSTS / X-Content-Type-Options / X-Frame-Options 등
    """
    # 1) 리버스 프록시 신뢰: X-Forwarded-Proto/Host 를 반영해 request.is_secure
    #    와 Secure 쿠키가 프록시 뒤에서도 올바로 동작하게 한다.
    if config.TRUSTED_PROXY_HOPS > 0:
        hops = config.TRUSTED_PROXY_HOPS
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    # 2) HTTP -> HTTPS 리다이렉트 (운영 스위치). 헬스체크는 프록시/모니터가
    #    HTTP 로 두드릴 수 있으므로 예외로 둔다.
    @app.before_request
    def _force_https():
        if not config.FORCE_HTTPS or request.is_secure:
            return None
        if request.method in ("GET", "HEAD") and request.path == "/api/health":
            return None
        return redirect(request.url.replace("http://", "https://", 1), code=301)

    # 3) 공통 보안 응답 헤더
    @app.after_request
    def _security_headers(resp):
        if not config.ENABLE_SECURITY_HEADERS:
            return resp
        # MIME 스니핑/클릭재킹/레퍼러 누출 방지 (스킴과 무관하게 항상)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        # HSTS 는 실제 HTTPS 응답일 때만 — HTTP 개발 중 브라우저가 HTTPS 를
        # 강제로 기억해버리는 사고를 막는다.
        if config.HSTS_MAX_AGE > 0 and request.is_secure:
            value = f"max-age={config.HSTS_MAX_AGE}"
            if config.HSTS_INCLUDE_SUBDOMAINS:
                value += "; includeSubDomains"
            resp.headers.setdefault("Strict-Transport-Security", value)
        return resp


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

    @app.post("/api/super/places/<slug>/header")
    @super_required
    def super_place_header(slug):
        """기관 헤더 로고 업로드(multipart, field=image) → static/<slug>/header.<ext>."""
        place = place_service.get_place(slug)
        if not place:
            raise ApiError("기관을 찾을 수 없습니다.", 404)
        file = request.files.get("image")
        if not file or not file.filename:
            raise ApiError("이미지 파일을 선택해주세요.")
        url = image_service.save_header_image(slug, file, old_url=place.get("header_image"))
        place_service.set_header_image(slug, url)
        return jsonify({"ok": True, "header_image": url})

    @app.delete("/api/super/places/<slug>/header")
    @super_required
    def super_place_header_delete(slug):
        place = place_service.get_place(slug)
        if not place:
            raise ApiError("기관을 찾을 수 없습니다.", 404)
        image_service.delete_image_file(place.get("header_image"))
        place_service.set_header_image(slug, None)
        return jsonify({"ok": True})

    # --- 공통 휴무일/공휴일 (전 기관 공유) ---
    @app.get("/api/super/holidays")
    @super_required
    def super_holidays_list():
        return jsonify({"holidays": holiday_service.list_common_holidays()})

    @app.post("/api/super/holidays")
    @super_required
    def super_holidays_add():
        d = request.get_json(silent=True) or {}
        holiday_service.add_common_holiday(
            d.get("date"), d.get("name", ""), d.get("type", "holiday"))
        return jsonify({"ok": True}), 201

    @app.delete("/api/super/holidays/<int:holiday_id>")
    @super_required
    def super_holidays_delete(holiday_id):
        return jsonify(holiday_service.delete_common_holiday(holiday_id))

    @app.post("/api/super/holidays/sync")
    @super_required
    def super_holidays_sync():
        """해당 연도 한국 공휴일 자동 채우기(대체공휴일 포함)."""
        d = request.get_json(silent=True) or {}
        return jsonify(holiday_service.sync_korea_holidays(d.get("year")))


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
            "email": p.get("email", ""), "header_image": p.get("header_image", ""),
            "operating_hours": reservation_service.get_operating_hours(get_place_db(slug)),
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

    @app.get("/api/<slug>/day-config")
    @place_required
    def place_day_config(slug):
        date_str = request.args.get("date")
        if not date_str:
            return jsonify({"is_open": True, "open_hour": config.OPEN_HOUR,
                            "close_hour": config.CLOSE_HOUR, "closed_reason": ""})
        target = reservation_service.parse_date(date_str)
        return jsonify(reservation_service.day_config(get_place_db(slug), target))

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
    @app.put("/api/<slug>/admin/info")
    @place_admin_required
    def place_admin_update_info(slug):
        """기관 관리자가 자기 기관의 표시명/연락처를 수정 (slug·비밀번호는 불가)."""
        d = request.get_json(silent=True) or {}
        ok, msg = place_service.update_place_info(
            slug,
            full_name=d.get("full_name"), short_name=d.get("short_name"),
            address=d.get("address"), phone=d.get("phone"), email=d.get("email"),
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.get("/api/<slug>/admin/dashboard")
    @place_admin_required
    def place_dashboard(slug):
        return jsonify(reservation_service.dashboard(
            get_place_db(slug), request.args.get("date")))

    @app.get("/api/<slug>/admin/day-grid")
    @place_admin_required
    def place_day_grid(slug):
        return jsonify(reservation_service.day_grid(
            get_place_db(slug), request.args.get("date")))

    @app.get("/api/<slug>/admin/week-grid")
    @place_admin_required
    def place_week_grid(slug):
        return jsonify(reservation_service.week_grid(
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
        """관리자용: 예약(reserved, 하드 차단)과 정기활동(blocked, 경고)을 구분 반환."""
        facility_id = request.args.get("facility_id", type=int)
        date_str = request.args.get("date")
        if not facility_id or not date_str:
            return jsonify({"reserved": [], "blocked": []})
        conn = get_place_db(slug)
        target_date = reservation_service.parse_date(date_str)
        exclude = request.args.get("exclude_res_id", type=int)
        reserved = reservation_service.booked_hours_for(
            conn, facility_id, target_date, exclude, include_blocks=False)
        blocked = reservation_service.block_hours_for(conn, facility_id, target_date)
        return jsonify({"reserved": sorted(reserved), "blocked": sorted(blocked)})

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

    # ---------------- 관리자 — 운영 설정 ----------------
    @app.get("/api/<slug>/admin/operating-hours")
    @place_admin_required
    def place_operating_hours_get(slug):
        return jsonify({"operating_hours": reservation_service.get_operating_hours(get_place_db(slug))})

    @app.put("/api/<slug>/admin/operating-hours")
    @place_admin_required
    def place_operating_hours_set(slug):
        d = request.get_json(silent=True) or {}
        items = reservation_service.set_operating_hours(get_place_db(slug), d.get("operating_hours"))
        return jsonify({"ok": True, "operating_hours": items})

    # 휴무일/공휴일: 공통(슈퍼) + 기관 지정 + 제외 + 공휴일 운영 설정
    @app.get("/api/<slug>/admin/holidays")
    @place_admin_required
    def place_holidays_view(slug):
        return jsonify(reservation_service.org_holidays_view(get_place_db(slug)))

    @app.get("/api/<slug>/admin/closures")
    @place_admin_required
    def place_closures_list(slug):
        return jsonify({"closures": reservation_service.list_closures(get_place_db(slug))})

    @app.post("/api/<slug>/admin/closures")
    @place_admin_required
    def place_closures_add(slug):
        d = request.get_json(silent=True) or {}
        reservation_service.add_closure(
            get_place_db(slug), d.get("date"),
            d.get("name", d.get("reason", "")), d.get("type", "closure"))
        return jsonify({"ok": True}), 201

    @app.delete("/api/<slug>/admin/closures/<int:closure_id>")
    @place_admin_required
    def place_closures_delete(slug, closure_id):
        return jsonify(reservation_service.delete_closure(get_place_db(slug), closure_id))

    @app.post("/api/<slug>/admin/holiday-excludes")
    @place_admin_required
    def place_holiday_exclude(slug):
        d = request.get_json(silent=True) or {}
        return jsonify(reservation_service.add_exclude(get_place_db(slug), d.get("date")))

    @app.delete("/api/<slug>/admin/holiday-excludes/<date_str>")
    @place_admin_required
    def place_holiday_include(slug, date_str):
        return jsonify(reservation_service.delete_exclude(get_place_db(slug), date_str))

    @app.put("/api/<slug>/admin/holiday-setting")
    @place_admin_required
    def place_holiday_setting(slug):
        d = request.get_json(silent=True) or {}
        return jsonify(reservation_service.set_holiday_operates(
            get_place_db(slug), bool(d.get("holiday_operates"))))

    @app.get("/api/<slug>/admin/recurring-blocks")
    @place_admin_required
    def place_blocks_list(slug):
        return jsonify({"blocks": reservation_service.list_recurring_blocks(get_place_db(slug))})

    @app.post("/api/<slug>/admin/recurring-blocks")
    @place_admin_required
    def place_blocks_add(slug):
        reservation_service.add_recurring_block(get_place_db(slug), request.get_json(silent=True) or {})
        return jsonify({"ok": True}), 201

    @app.delete("/api/<slug>/admin/recurring-blocks/<int:block_id>")
    @place_admin_required
    def place_blocks_delete(slug, block_id):
        return jsonify(reservation_service.delete_recurring_block(get_place_db(slug), block_id))

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

    @app.post("/api/<slug>/admin/facilities/<int:facility_id>/image")
    @place_admin_required
    def place_admin_facility_image(slug, facility_id):
        """시설 이미지 업로드(multipart, field=image). 기존 이미지는 삭제 후 교체."""
        conn = get_place_db(slug)
        fac = facility_service.get_facility(conn, facility_id)
        if fac is None:
            raise ApiError("시설을 찾을 수 없습니다.", 404)
        file = request.files.get("image")
        if not file or not file.filename:
            raise ApiError("이미지 파일을 선택해주세요.")
        url = image_service.save_facility_image(slug, facility_id, file, old_url=fac.get("image_url"))
        facility_service.set_image_url(conn, facility_id, url)
        return jsonify({"ok": True, "image_url": url})

    @app.delete("/api/<slug>/admin/facilities/<int:facility_id>/image")
    @place_admin_required
    def place_admin_facility_image_delete(slug, facility_id):
        conn = get_place_db(slug)
        fac = facility_service.get_facility(conn, facility_id)
        if fac is None:
            raise ApiError("시설을 찾을 수 없습니다.", 404)
        image_service.delete_image_file(fac.get("image_url"))
        facility_service.set_image_url(conn, facility_id, None)
        return jsonify({"ok": True})


app = create_app()

if __name__ == "__main__":
    # 로컬에서 HTTPS 를 테스트하려면 DEV_HTTPS=true 로 실행하면 임시 자체서명
    # 인증서로 https://127.0.0.1:8000 서빙(브라우저 경고는 무시). 'adhoc' 은
    # cryptography 패키지가 필요: pip install cryptography
    ssl_context = "adhoc" if config._env_bool("DEV_HTTPS", False) else None
    app.run(debug=True, host="127.0.0.1", port=8000, ssl_context=ssl_context)
