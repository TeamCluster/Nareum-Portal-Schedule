"""기관 관리자 API 통합 테스트 (/api/<slug>/admin/...)."""
from conftest import SLUG, PLACE_PW, reservation_payload, valid_date

BASE = f"/api/{SLUG}"


class TestAuth:
    def test_login_wrong(self, client):
        assert client.post(f"{BASE}/admin/login", json={"password": "x"}).status_code == 401

    def test_login_ok(self, client):
        assert client.post(f"{BASE}/admin/login", json={"password": PLACE_PW}).status_code == 200

    def test_session(self, admin_client):
        assert admin_client.get(f"{BASE}/admin/session").get_json()["logged_in"] is True

    def test_logout(self, admin_client):
        admin_client.post(f"{BASE}/admin/logout")
        assert admin_client.get(f"{BASE}/admin/session").get_json()["logged_in"] is False

    def test_guard(self, client):
        assert client.get(f"{BASE}/admin/dashboard").status_code == 401
        assert client.get(f"{BASE}/admin/requests").status_code == 401

    def test_login_unknown_slug(self, client):
        assert client.post("/api/nope/admin/login", json={"password": PLACE_PW}).status_code == 404


class TestCrossTenantAuth:
    def test_login_one_place_not_authed_for_another(self, client, super_client):
        super_client.post("/api/super/places", json={
            "slug": "other", "full_name": "다른", "short_name": "다른", "password": "abcdef"})
        # nareum 로그인
        client.post(f"{BASE}/admin/login", json={"password": PLACE_PW})
        # other 기관 관리자 API 는 여전히 401
        assert client.get("/api/other/admin/dashboard").status_code == 401
        # 하지만 nareum 은 통과
        assert client.get(f"{BASE}/admin/dashboard").status_code == 200


class TestAdminCreate:
    def test_confirmed(self, admin_client):
        r = admin_client.post(f"{BASE}/admin/reservations", json=reservation_payload())
        assert r.status_code == 201
        rid = r.get_json()["id"]
        assert admin_client.get(f"{BASE}/admin/reservations/{rid}").get_json()["status"] == "confirmed"

    def test_zero_participants_allowed(self, admin_client):
        assert admin_client.post(f"{BASE}/admin/reservations",
                                 json=reservation_payload(participants={})).status_code == 201

    def test_over_two_hours_allowed(self, admin_client):
        assert admin_client.post(f"{BASE}/admin/reservations",
                                 json=reservation_payload(hours=[10, 11, 12, 13])).status_code == 201

    def test_overlap_blocked(self, admin_client):
        admin_client.post(f"{BASE}/admin/reservations", json=reservation_payload(hours=[10, 11]))
        assert admin_client.post(f"{BASE}/admin/reservations",
                                 json=reservation_payload(hours=[11])).status_code == 400


class TestApproveReject:
    def test_approve(self, client, admin_client):
        client.post(f"{BASE}/reservations", json=reservation_payload())
        rid = admin_client.get(f"{BASE}/admin/requests").get_json()[0]["id"]
        assert admin_client.post(f"{BASE}/admin/reservations/{rid}/approve").status_code == 200
        assert admin_client.get(f"{BASE}/admin/reservations/{rid}").get_json()["status"] == "confirmed"
        assert admin_client.get(f"{BASE}/admin/requests").get_json() == []

    def test_reject_frees_slot(self, client, admin_client):
        client.post(f"{BASE}/reservations", json=reservation_payload())
        rid = admin_client.get(f"{BASE}/admin/requests").get_json()[0]["id"]
        r = admin_client.post(f"{BASE}/admin/reservations/{rid}/reject",
                              json={"reject_reason": "점검"})
        assert r.status_code == 200
        detail = admin_client.get(f"{BASE}/admin/reservations/{rid}").get_json()
        assert detail["status"] == "rejected" and detail["reject_reason"] == "점검"
        avail = client.get(f"{BASE}/availability?date={valid_date()}").get_json()
        assert avail["facilities"][0]["hours"]["10"] == "available"


class TestUpdateDashboard:
    def _pending(self, client, admin_client):
        client.post(f"{BASE}/reservations", json=reservation_payload())
        return admin_client.get(f"{BASE}/admin/requests").get_json()[0]["id"]

    def test_update_status(self, client, admin_client):
        rid = self._pending(client, admin_client)
        r = admin_client.put(f"{BASE}/admin/reservations/{rid}",
                             json=reservation_payload(status="confirmed"))
        assert r.status_code == 200
        assert admin_client.get(f"{BASE}/admin/reservations/{rid}").get_json()["status"] == "confirmed"

    def test_update_invalid_status(self, client, admin_client):
        rid = self._pending(client, admin_client)
        assert admin_client.put(f"{BASE}/admin/reservations/{rid}",
                                json=reservation_payload(status="bogus")).status_code == 400

    def test_dashboard_count(self, client, admin_client):
        client.post(f"{BASE}/reservations", json=reservation_payload())
        assert admin_client.get(f"{BASE}/admin/dashboard").get_json()["pending_count"] == 1

    def test_calendar_color(self, client, admin_client):
        client.post(f"{BASE}/reservations", json=reservation_payload())
        events = admin_client.get(f"{BASE}/admin/calendar-events").get_json()
        assert len(events) == 1 and events[0]["color"] == "#ff9800"


class TestInfoUpdate:
    def test_admin_updates_own_info(self, client, admin_client):
        r = admin_client.put(f"{BASE}/admin/info", json={
            "full_name": "나름청소년활동센터(수정)", "phone": "031-123-4567",
            "address": "경기도 나름시 청소년로 1", "email": "hello@nareum.kr",
        })
        assert r.status_code == 200
        info = client.get(f"{BASE}/info").get_json()
        assert info["full_name"] == "나름청소년활동센터(수정)"
        assert info["phone"] == "031-123-4567"
        assert info["address"] == "경기도 나름시 청소년로 1"
        assert info["email"] == "hello@nareum.kr"

    def test_update_requires_login(self, client):
        assert client.put(f"{BASE}/admin/info", json={"phone": "x"}).status_code == 401

    def test_empty_full_name_rejected(self, admin_client):
        assert admin_client.put(f"{BASE}/admin/info", json={"full_name": "  "}).status_code == 400


class TestDayGrid:
    def test_shape(self, admin_client):
        d = admin_client.get(f"{BASE}/admin/day-grid?date={valid_date()}").get_json()
        assert d["open_hour"] == 9 and d["close_hour"] == 18
        assert len(d["facilities"]) == 5
        # 예약 없으면 전부 free 한 구간(9~18)
        f0 = d["facilities"][0]
        assert f0["segments"] == [{"type": "free", "from_hour": 9, "to_hour": 18}]

    def test_reservation_segment(self, client, admin_client):
        client.post(f"{BASE}/reservations", json=reservation_payload(facility_id=1, hours=[10, 11]))
        d = admin_client.get(f"{BASE}/admin/day-grid?date={valid_date()}").get_json()
        fac = next(f for f in d["facilities"] if f["id"] == 1)
        res_segs = [s for s in fac["segments"] if s["type"] == "res"]
        assert len(res_segs) == 1
        seg = res_segs[0]
        assert seg["from_hour"] == 10 and seg["to_hour"] == 12
        assert seg["status"] == "pending" and seg["name"] == "홍길동"
        # free + res + free 로 전체 9~18 을 덮어야 함
        assert fac["segments"][0]["from_hour"] == 9
        assert fac["segments"][-1]["to_hour"] == 18
        assert sum(s["to_hour"] - s["from_hour"] for s in fac["segments"]) == 9

    def test_requires_login(self, client):
        assert client.get(f"{BASE}/admin/day-grid").status_code == 401


class TestWeekGrid:
    def test_shape(self, admin_client):
        d = admin_client.get(f"{BASE}/admin/week-grid?date={valid_date()}").get_json()
        assert len(d["days"]) == 7
        assert len(d["facilities"]) == 5
        assert d["hour_min"] == 9 and d["hour_max"] == 18
        # 각 날짜는 day_grid 구조
        assert all("segments" in day["facilities"][0] for day in d["days"] if day["is_open"])

    def test_reservation_appears_in_week(self, client, admin_client):
        client.post(f"{BASE}/reservations", json=reservation_payload(facility_id=1, hours=[10, 11]))
        d = admin_client.get(f"{BASE}/admin/week-grid?date={valid_date()}").get_json()
        found = False
        for day in d["days"]:
            for fac in day["facilities"]:
                if fac["id"] == 1 and any(s["type"] == "res" for s in fac["segments"]):
                    found = True
        assert found

    def test_requires_login(self, client):
        assert client.get(f"{BASE}/admin/week-grid").status_code == 401


class TestFacilityCrud:
    def test_add_update_delete(self, admin_client):
        # add
        r = admin_client.post(f"{BASE}/admin/facilities",
                              json={"name": "새시설", "type": "회의실", "capacity": 10})
        assert r.status_code == 201
        fid = r.get_json()["facility"]["id"]
        assert len(admin_client.get(f"{BASE}/admin/facilities").get_json()) == 6
        # update
        r = admin_client.put(f"{BASE}/admin/facilities/{fid}",
                             json={"name": "고친시설", "type": "활동실"})
        assert r.status_code == 200 and r.get_json()["facility"]["name"] == "고친시설"
        # delete
        assert admin_client.delete(f"{BASE}/admin/facilities/{fid}").status_code == 200
        assert len(admin_client.get(f"{BASE}/admin/facilities").get_json()) == 5

    def test_add_requires_name(self, admin_client):
        assert admin_client.post(f"{BASE}/admin/facilities",
                                 json={"type": "회의실"}).status_code == 400
