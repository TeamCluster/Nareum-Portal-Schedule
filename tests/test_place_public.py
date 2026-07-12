"""기관 공개 API 통합 테스트 (/api/<slug>/...)."""
from conftest import (
    SLUG,
    reservation_payload,
    too_far_date,
    too_soon_date,
    valid_date,
    week_days,
)

BASE = f"/api/{SLUG}"


def test_unknown_slug_404(client):
    assert client.get("/api/nope/facilities").status_code == 404
    assert client.get("/api/nope/info").status_code == 404


def test_invalid_slug_404(client):
    assert client.get("/api/Bad_Slug/facilities").status_code == 404


def test_info(client):
    d = client.get(f"{BASE}/info").get_json()
    assert d["slug"] == SLUG and d["full_name"]


def test_facilities_seeded(client):
    d = client.get(f"{BASE}/facilities").get_json()
    assert len(d) == 5


class TestAvailability:
    def test_no_date(self, client):
        d = client.get(f"{BASE}/availability").get_json()
        assert d["facilities"][0]["hours"] == {}

    def test_valid_date(self, client):
        d = client.get(f"{BASE}/availability?date={valid_date()}").get_json()
        assert d["is_reservable"] is True
        assert d["facilities"][0]["hours"]["10"] == "available"

    def test_out_of_window(self, client):
        d = client.get(f"{BASE}/availability?date={too_far_date()}").get_json()
        assert d["is_reservable"] is False

    def test_booked_reflected(self, client):
        client.post(f"{BASE}/reservations", json=reservation_payload())
        d = client.get(f"{BASE}/availability?date={valid_date()}").get_json()
        h = d["facilities"][0]["hours"]
        assert h["10"] == "booked" and h["11"] == "booked" and h["12"] == "available"


class TestCreate:
    def test_happy(self, client):
        r = client.post(f"{BASE}/reservations", json=reservation_payload())
        assert r.status_code == 201 and "access_id" in r.get_json()

    def test_unknown_facility(self, client):
        assert client.post(f"{BASE}/reservations",
                           json=reservation_payload(facility_id=999)).status_code == 404

    def test_missing_name(self, client):
        assert client.post(f"{BASE}/reservations",
                           json=reservation_payload(name="")).status_code == 400

    def test_zero_participants(self, client):
        assert client.post(f"{BASE}/reservations",
                           json=reservation_payload(participants={})).status_code == 400

    def test_too_soon(self, client):
        assert client.post(f"{BASE}/reservations",
                           json=reservation_payload(date=too_soon_date())).status_code == 400

    def test_over_two_hours(self, client):
        assert client.post(f"{BASE}/reservations",
                           json=reservation_payload(hours=[10, 11, 12])).status_code == 400

    def test_non_contiguous(self, client):
        assert client.post(f"{BASE}/reservations",
                           json=reservation_payload(hours=[10, 12])).status_code == 400

    def test_same_day_block(self, client):
        client.post(f"{BASE}/reservations", json=reservation_payload(hours=[10]))
        r = client.post(f"{BASE}/reservations", json=reservation_payload(hours=[13]))
        assert r.status_code == 400 and "하루 1회" in r.get_json()["error"]

    def test_weekly_limit(self, client):
        d1, d2, d3 = week_days()
        assert client.post(f"{BASE}/reservations", json=reservation_payload(date=d1, hours=[10])).status_code == 201
        assert client.post(f"{BASE}/reservations", json=reservation_payload(date=d2, hours=[10])).status_code == 201
        r = client.post(f"{BASE}/reservations", json=reservation_payload(date=d3, hours=[10]))
        assert r.status_code == 400 and "주간" in r.get_json()["error"]

    def test_overlap(self, client):
        client.post(f"{BASE}/reservations", json=reservation_payload(hours=[10, 11]))
        other = reservation_payload(name="김철수", contact="010-9999-8888", hours=[11])
        assert client.post(f"{BASE}/reservations", json=other).status_code == 400


class TestLookupCancel:
    def _create(self, client):
        return client.post(f"{BASE}/reservations", json=reservation_payload()).get_json()["access_id"]

    def test_get_by_access(self, client):
        aid = self._create(client)
        r = client.get(f"{BASE}/reservations/{aid}")
        assert r.status_code == 200 and r.get_json()["applicant_name"] == "홍길동"

    def test_unknown_access(self, client):
        assert client.get(f"{BASE}/reservations/nope").status_code == 404

    def test_lookup(self, client):
        self._create(client)
        r = client.post(f"{BASE}/reservations/lookup",
                        json={"name": "홍길동", "contact": "010-1234-5678"})
        d = r.get_json()
        assert len(d) == 1 and d[0]["status"] == "pending"

    def test_cancel_frees_slot(self, client):
        self._create(client)
        rid = client.post(f"{BASE}/reservations/lookup",
                          json={"name": "홍길동", "contact": "010-1234-5678"}).get_json()[0]["id"]
        assert client.post(f"{BASE}/reservations/{rid}/cancel").status_code == 200
        d = client.get(f"{BASE}/availability?date={valid_date()}").get_json()
        assert d["facilities"][0]["hours"]["10"] == "available"


class TestTenantIsolation:
    def test_reservation_not_visible_in_other_place(self, client, super_client):
        # 다른 기관 생성
        super_client.post("/api/super/places", json={
            "slug": "other", "full_name": "다른센터", "short_name": "다른", "password": "abcdef"})
        # nareum 에 예약 생성
        client.post(f"{BASE}/reservations", json=reservation_payload())
        # other 기관의 조회에는 잡히지 않아야 함
        d = client.post("/api/other/reservations/lookup",
                        json={"name": "홍길동", "contact": "010-1234-5678"}).get_json()
        assert d == []
