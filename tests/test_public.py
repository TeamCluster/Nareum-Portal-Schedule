"""Integration tests for the public API (api/public.py)."""

from conftest import (
    reservation_payload,
    too_far_date,
    too_soon_date,
    valid_date,
    week_days,
)


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_config_exposes_booking_window(client):
    data = client.get("/api/config").get_json()
    assert data["booking_min_days"] == 2
    assert data["booking_max_days"] == 60


def test_facilities_seeded(client):
    data = client.get("/api/facilities").get_json()
    assert len(data) == 2
    assert data[0]["name"] == "연습터"


class TestAvailability:
    def test_no_date_returns_facilities_without_hours(self, client):
        data = client.get("/api/availability").get_json()
        assert data["facilities"][0]["hours"] == {}

    def test_valid_date_reservable(self, client):
        data = client.get(f"/api/availability?date={valid_date()}").get_json()
        assert data["is_reservable"] is True
        assert data["facilities"][0]["hours"]["10"] == "available"

    def test_out_of_window_not_reservable(self, client):
        data = client.get(f"/api/availability?date={too_far_date()}").get_json()
        assert data["is_reservable"] is False

    def test_booked_hour_reflected(self, client):
        client.post("/api/reservations", json=reservation_payload())
        data = client.get(f"/api/availability?date={valid_date()}").get_json()
        hours = data["facilities"][0]["hours"]
        assert hours["10"] == "booked"
        assert hours["11"] == "booked"
        assert hours["12"] == "available"


class TestCreateReservation:
    def test_happy_path(self, client):
        res = client.post("/api/reservations", json=reservation_payload())
        assert res.status_code == 201
        assert "access_id" in res.get_json()

    def test_unknown_facility(self, client):
        res = client.post("/api/reservations", json=reservation_payload(facility_id=999))
        assert res.status_code == 404

    def test_missing_name(self, client):
        res = client.post("/api/reservations", json=reservation_payload(name=""))
        assert res.status_code == 400
        assert "신청인" in res.get_json()["error"]

    def test_zero_participants_rejected(self, client):
        res = client.post("/api/reservations", json=reservation_payload(participants={}))
        assert res.status_code == 400
        assert "참가 인원" in res.get_json()["error"]

    def test_too_soon_rejected(self, client):
        res = client.post("/api/reservations", json=reservation_payload(date=too_soon_date()))
        assert res.status_code == 400

    def test_too_far_rejected(self, client):
        res = client.post("/api/reservations", json=reservation_payload(date=too_far_date()))
        assert res.status_code == 400

    def test_over_two_hours_rejected(self, client):
        res = client.post("/api/reservations", json=reservation_payload(hours=[10, 11, 12]))
        assert res.status_code == 400
        assert "2시간" in res.get_json()["error"]

    def test_non_contiguous_rejected(self, client):
        res = client.post("/api/reservations", json=reservation_payload(hours=[10, 12]))
        assert res.status_code == 400

    def test_same_person_same_day_blocked(self, client):
        client.post("/api/reservations", json=reservation_payload(hours=[10]))
        res = client.post("/api/reservations", json=reservation_payload(hours=[13]))
        assert res.status_code == 400
        assert "하루 1회" in res.get_json()["error"]

    def test_weekly_limit_blocks_third(self, client):
        d1, d2, d3 = week_days()
        assert client.post("/api/reservations", json=reservation_payload(date=d1, hours=[10])).status_code == 201
        assert client.post("/api/reservations", json=reservation_payload(date=d2, hours=[10])).status_code == 201
        third = client.post("/api/reservations", json=reservation_payload(date=d3, hours=[10]))
        assert third.status_code == 400
        assert "주간" in third.get_json()["error"]

    def test_overlap_blocked_for_other_person(self, client):
        client.post("/api/reservations", json=reservation_payload(hours=[10, 11]))
        other = reservation_payload(name="김철수", contact="010-9999-8888", hours=[11])
        res = client.post("/api/reservations", json=other)
        assert res.status_code == 400
        assert "진행 중" in res.get_json()["error"]


class TestLookupAndCancel:
    def _create(self, client):
        return client.post("/api/reservations", json=reservation_payload()).get_json()["access_id"]

    def test_get_by_access_id(self, client):
        access_id = self._create(client)
        res = client.get(f"/api/reservations/{access_id}")
        assert res.status_code == 200
        assert res.get_json()["applicant_name"] == "홍길동"

    def test_get_unknown_access_id(self, client):
        assert client.get("/api/reservations/nope").status_code == 404

    def test_lookup_by_name_contact(self, client):
        self._create(client)
        res = client.post(
            "/api/reservations/lookup",
            json={"name": "홍길동", "contact": "010-1234-5678"},
        )
        data = res.get_json()
        assert len(data) == 1
        assert data[0]["status"] == "pending"

    def test_cancel_soft_deletes(self, client):
        self._create(client)
        found = client.post(
            "/api/reservations/lookup",
            json={"name": "홍길동", "contact": "010-1234-5678"},
        ).get_json()
        res_id = found[0]["id"]

        res = client.post(f"/api/reservations/{res_id}/cancel")
        assert res.status_code == 200

        # Slot is freed after cancel.
        avail = client.get(f"/api/availability?date={valid_date()}").get_json()
        assert avail["facilities"][0]["hours"]["10"] == "available"

    def test_double_cancel_rejected(self, client):
        self._create(client)
        found = client.post(
            "/api/reservations/lookup",
            json={"name": "홍길동", "contact": "010-1234-5678"},
        ).get_json()
        res_id = found[0]["id"]
        client.post(f"/api/reservations/{res_id}/cancel")
        res = client.post(f"/api/reservations/{res_id}/cancel")
        assert res.status_code == 400
