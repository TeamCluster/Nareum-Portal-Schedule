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


class TestFormConfig:
    def test_requires_login(self, client):
        assert client.get(f"{BASE}/admin/form-config").status_code == 401
        assert client.put(f"{BASE}/admin/form-config", json={}).status_code == 401

    def test_update_catalog_and_texts(self, admin_client):
        r = admin_client.put(f"{BASE}/admin/form-config", json={
            "equipment_catalog": [{
                "title": "음향", "facility_types": ["회의실"], "allow_other": False,
                "items": ["빔 프로젝터", {"name": "마이크", "qty": True}, "  "],
            }],
            "notice": ["당일 대관 불가", ""],
            "rules": "첫째 줄\n둘째 줄",
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d["equipment_catalog"] == [{
            "title": "음향", "facility_types": ["회의실"], "allow_other": False,
            "items": [{"name": "빔 프로젝터", "qty": False}, {"name": "마이크", "qty": True}],
        }]
        assert d["notice"] == ["당일 대관 불가"]
        assert d["rules"] == ["첫째 줄", "둘째 줄"]
        # 공개 엔드포인트에도 즉시 반영된다.
        pub = admin_client.get(f"{BASE}/form-config?facility_type=연습실").get_json()
        assert pub["equipment_catalog"] == []

    def test_partial_update_keeps_others(self, admin_client):
        before = admin_client.get(f"{BASE}/admin/form-config").get_json()
        admin_client.put(f"{BASE}/admin/form-config", json={"notice": ["하나만 수정"]})
        after = admin_client.get(f"{BASE}/admin/form-config").get_json()
        assert after["notice"] == ["하나만 수정"]
        assert after["rules"] == before["rules"]
        assert after["equipment_catalog"] == before["equipment_catalog"]

    def test_rejects_bad_shape(self, admin_client):
        assert admin_client.put(f"{BASE}/admin/form-config",
                                json={"equipment_catalog": "장비"}).status_code == 400


class TestAttendance:
    def _make(self, admin_client, **kw):
        return admin_client.post(f"{BASE}/admin/reservations",
                                 json=reservation_payload(**kw)).get_json()["id"]

    def test_requires_login(self, client):
        assert client.post(f"{BASE}/admin/reservations/1/attendance",
                           json={"attendance": "attended"}).status_code == 401

    def test_records_and_reports_block(self, admin_client):
        rid = self._make(admin_client)
        r = admin_client.post(f"{BASE}/admin/reservations/{rid}/attendance",
                              json={"attendance": "no_show"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["attendance"] == "no_show" and d["blocked_until"]
        assert admin_client.get(
            f"{BASE}/admin/reservations/{rid}").get_json()["attendance"] == "no_show"

    def test_attended_has_no_block(self, admin_client):
        rid = self._make(admin_client)
        d = admin_client.post(f"{BASE}/admin/reservations/{rid}/attendance",
                              json={"attendance": "attended"}).get_json()
        assert d["blocked_until"] is None

    def test_invalid_value(self, admin_client):
        rid = self._make(admin_client)
        assert admin_client.post(f"{BASE}/admin/reservations/{rid}/attendance",
                                 json={"attendance": "결석"}).status_code == 400

    def test_unknown_reservation(self, admin_client):
        assert admin_client.post(f"{BASE}/admin/reservations/999/attendance",
                                 json={"attendance": "attended"}).status_code == 404

    def test_survives_admin_edit(self, admin_client):
        rid = self._make(admin_client)
        admin_client.post(f"{BASE}/admin/reservations/{rid}/attendance",
                          json={"attendance": "no_show"})
        admin_client.put(f"{BASE}/admin/reservations/{rid}",
                         json=reservation_payload(status="confirmed"))
        assert admin_client.get(
            f"{BASE}/admin/reservations/{rid}").get_json()["attendance"] == "no_show"


class TestExtend:
    def _make(self, admin_client, **kw):
        return admin_client.post(f"{BASE}/admin/reservations",
                                 json=reservation_payload(**kw)).get_json()["id"]

    def test_requires_login(self, client):
        assert client.post(f"{BASE}/admin/reservations/1/extend").status_code == 401

    def test_extends_one_hour(self, admin_client):
        rid = self._make(admin_client, hours=[10, 11])
        r = admin_client.post(f"{BASE}/admin/reservations/{rid}/extend")
        assert r.status_code == 200
        assert r.get_json()["end_time"].endswith("T13:00:00")

    def test_blocked_by_next_reservation(self, admin_client):
        rid = self._make(admin_client, hours=[10, 11])
        self._make(admin_client, hours=[12], name="다음팀", contact="010-1111-2222")
        r = admin_client.post(f"{BASE}/admin/reservations/{rid}/extend")
        assert r.status_code == 400 and "다른 대관예약" in r.get_json()["error"]

    def test_blocked_by_closing_hour(self, admin_client):
        # 운영 종료 18시 → 17시 종료 예약은 연장 가능하지만 18시 종료는 불가.
        rid = self._make(admin_client, hours=[16, 17])
        r = admin_client.post(f"{BASE}/admin/reservations/{rid}/extend")
        assert r.status_code == 400 and "운영 종료" in r.get_json()["error"]

    def test_pending_not_extendable(self, admin_client):
        access_id = admin_client.post(f"{BASE}/reservations",
                                      json=reservation_payload()).get_json()["access_id"]
        rid = admin_client.get(f"{BASE}/reservations/{access_id}").get_json()["id"]
        r = admin_client.post(f"{BASE}/admin/reservations/{rid}/extend")
        assert r.status_code == 400 and "확정된 예약" in r.get_json()["error"]

    def test_unknown_reservation(self, admin_client):
        assert admin_client.post(f"{BASE}/admin/reservations/999/extend").status_code == 404


class TestBookingRules:
    def test_requires_login(self, client):
        assert client.get(f"{BASE}/admin/booking-rules").status_code == 401
        assert client.put(f"{BASE}/admin/booking-rules", json={}).status_code == 401

    def test_defaults_seeded(self, admin_client):
        d = admin_client.get(f"{BASE}/admin/booking-rules").get_json()["booking_rules"]
        assert d["cancel_deadline_days"] == 1
        assert d["penalty_months"] == 6
        assert d["extension_hours"] == 1

    def test_partial_update(self, admin_client):
        r = admin_client.put(f"{BASE}/admin/booking-rules", json={"penalty_months": 3})
        assert r.status_code == 200
        rules = r.get_json()["booking_rules"]
        assert rules["penalty_months"] == 3 and rules["cancel_deadline_days"] == 1

    def test_out_of_range(self, admin_client):
        assert admin_client.put(f"{BASE}/admin/booking-rules",
                                json={"penalty_months": 999}).status_code == 400

    def test_non_numeric(self, admin_client):
        assert admin_client.put(f"{BASE}/admin/booking-rules",
                                json={"cancel_deadline_days": "하루"}).status_code == 400

    def test_min_cannot_exceed_max(self, admin_client):
        assert admin_client.put(f"{BASE}/admin/booking-rules", json={
            "booking_min_days": 30, "booking_max_days": 10}).status_code == 400

    def test_affects_booking_window(self, admin_client):
        admin_client.put(f"{BASE}/admin/booking-rules", json={"booking_max_days": 3})
        d = admin_client.get(f"{BASE}/availability").get_json()
        from datetime import date, timedelta
        assert d["max_date"] == (date.today() + timedelta(days=3)).isoformat()
        # 범위를 벗어난 날짜는 신청도 막힌다.
        assert admin_client.post(f"{BASE}/reservations", json=reservation_payload(
            date=(date.today() + timedelta(days=10)).isoformat())).status_code == 400

    def test_zero_deadline_allows_same_day_cancel(self, admin_client):
        from datetime import date
        admin_client.put(f"{BASE}/admin/booking-rules", json={"cancel_deadline_days": 0})
        rid = admin_client.post(f"{BASE}/admin/reservations", json=reservation_payload(
            date=date.today().isoformat())).get_json()["id"]
        assert admin_client.post(f"{BASE}/reservations/{rid}/cancel").status_code == 200

    def test_zero_penalty_disables_block(self, admin_client):
        from datetime import date, timedelta
        admin_client.put(f"{BASE}/admin/booking-rules", json={"penalty_months": 0})
        past = (date.today() - timedelta(days=30)).isoformat()
        rid = admin_client.post(f"{BASE}/admin/reservations",
                                json=reservation_payload(date=past)).get_json()["id"]
        admin_client.post(f"{BASE}/admin/reservations/{rid}/attendance",
                          json={"attendance": "no_show"})
        assert admin_client.post(f"{BASE}/reservations",
                                 json=reservation_payload()).status_code == 201


class TestClubs:
    """동아리 목록 프록시 — 외부 서비스가 죽어도 화면이 살아있어야 한다."""

    def test_requires_login(self, client):
        assert client.get(f"{BASE}/admin/clubs").status_code == 401

    def test_normalizes_and_sorts(self, admin_client, monkeypatch):
        from services import club_service
        club_service.clear_cache()
        monkeypatch.setattr(club_service, "_request", lambda url: {
            "club_dict": {"무시됨": "무시"},
            "clubs": [
                {"category": "밴드", "name": "하이라이트"},
                {"category": "과학", "name": "클러스터"},
                {"category": "밴드", "name": " 보히 "},
                {"category": "밴드", "name": "보히"},   # 중복
                {"category": "밴드", "name": "  "},     # 빈 이름
            ],
        })
        d = admin_client.get(f"{BASE}/admin/clubs").get_json()
        assert d["available"] is True
        assert d["clubs"] == [
            {"name": "클러스터", "category": "과학"},
            {"name": "보히", "category": "밴드"},
            {"name": "하이라이트", "category": "밴드"},
        ]

    def test_falls_back_to_club_dict(self, admin_client, monkeypatch):
        from services import club_service
        club_service.clear_cache()
        monkeypatch.setattr(club_service, "_request",
                            lambda url: {"club_dict": {"블리스": "댄스"}})
        d = admin_client.get(f"{BASE}/admin/clubs").get_json()
        assert d["clubs"] == [{"name": "블리스", "category": "댄스"}]

    def test_caches_between_calls(self, admin_client, monkeypatch):
        from services import club_service
        club_service.clear_cache()
        calls = []

        def fake(url):
            calls.append(url)
            return {"clubs": [{"name": "필락", "category": "미술"}]}

        monkeypatch.setattr(club_service, "_request", fake)
        admin_client.get(f"{BASE}/admin/clubs")
        assert admin_client.get(f"{BASE}/admin/clubs").get_json()["cached"] is True
        assert len(calls) == 1
        # refresh=1 이면 캐시를 무시하고 다시 부른다.
        admin_client.get(f"{BASE}/admin/clubs?refresh=1")
        assert len(calls) == 2

    def test_degrades_when_service_down(self, admin_client, monkeypatch):
        import urllib.error
        from services import club_service
        club_service.clear_cache()

        def boom(url):
            raise urllib.error.URLError("연결 실패")

        monkeypatch.setattr(club_service, "_request", boom)
        r = admin_client.get(f"{BASE}/admin/clubs")
        # 200 으로 내려야 프론트가 '직접 입력' 으로 계속 진행할 수 있다.
        assert r.status_code == 200
        d = r.get_json()
        assert d["available"] is False and d["clubs"] == [] and d["error"]

    def test_degrades_on_404(self, admin_client, monkeypatch):
        import urllib.error
        from services import club_service
        club_service.clear_cache()

        def missing(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

        monkeypatch.setattr(club_service, "_request", missing)
        d = admin_client.get(f"{BASE}/admin/clubs").get_json()
        assert d["available"] is False and "등록되어 있지 않" in d["error"]


class TestClubShortReservation:
    """동아리 단기대관 — 신청인 정보 없이 동아리명만으로 추가."""

    def test_club_name_used_as_display_name(self, admin_client):
        r = admin_client.post(f"{BASE}/admin/reservations", json={
            "facility_id": 1, "date": valid_date(), "hours": [14, 15],
            "club": "하이라이트", "activity": "공연 준비", "participants": {}, "equipment": [],
        })
        assert r.status_code == 201
        d = admin_client.get(f"{BASE}/admin/reservations/{r.get_json()['id']}").get_json()
        assert d["applicant_name"] == "하이라이트"
        assert d["applicant_club"] == "하이라이트"
        assert d["activity"] == "공연 준비"
        assert d["status"] == "confirmed"

    def test_explicit_name_wins(self, admin_client):
        r = admin_client.post(f"{BASE}/admin/reservations", json={
            "facility_id": 1, "date": valid_date(), "hours": [14],
            "name": "김담당", "club": "하이라이트", "participants": {},
        })
        d = admin_client.get(f"{BASE}/admin/reservations/{r.get_json()['id']}").get_json()
        assert d["applicant_name"] == "김담당"

    def test_still_blocks_overlap(self, admin_client):
        payload = {"facility_id": 1, "date": valid_date(), "hours": [14, 15],
                   "club": "하이라이트", "participants": {}}
        assert admin_client.post(f"{BASE}/admin/reservations", json=payload).status_code == 201
        assert admin_client.post(f"{BASE}/admin/reservations", json={
            **payload, "club": "블리스", "hours": [15]}).status_code == 400
