"""기관 공개 API 통합 테스트 (/api/<slug>/...)."""
from datetime import date, timedelta

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

    def test_missing_activity(self, client):
        # 종이 서식의 '활동내용' 은 공개 신청 필수 항목.
        r = client.post(f"{BASE}/reservations", json=reservation_payload(activity=""))
        assert r.status_code == 400 and "활동내용" in r.get_json()["error"]

    def test_form_fields_round_trip(self, client):
        """활동내용·나이·주소·성별 인원·수량 물품이 그대로 저장/조회된다."""
        payload = reservation_payload(
            activity="보드게임", age=17, address="a@b.com",
            participants={"middle": {"male": 2, "female": 1}},
            equipment=[{"name": "마이크", "qty": 3}],
        )
        access_id = client.post(f"{BASE}/reservations", json=payload).get_json()["access_id"]
        d = client.get(f"{BASE}/reservations/{access_id}").get_json()
        assert d["activity"] == "보드게임"
        assert d["applicant_age"] == 17 and d["applicant_address"] == "a@b.com"
        assert d["participant_info"]["middle"] == {"male": 2, "female": 1, "unspecified": 0}
        assert d["requested_equipment"] == [{"name": "마이크", "qty": 3}]

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


class TestFrontendPayloadShape:
    """프론트가 ApplicantState 를 그대로 펼쳐 보내는 형태를 그대로 받아낸다."""

    def test_untouched_optional_inputs(self, client):
        # 나이 입력칸을 건드리지 않으면 빈 문자열이 오고, 인원표는 5구분이 모두 채워져 온다.
        r = client.post(f"{BASE}/reservations", json={
            "facility_id": 1, "date": valid_date(), "hours": [10, 11],
            "name": "홍길동", "age": "", "contact": "010-1234-5678", "address": "",
            "school": "나름중", "club": "", "activity": "밴드합주",
            "participants": {
                "elementary": {"male": 0, "female": 0, "unspecified": 0},
                "middle": {"male": 2, "female": 1, "unspecified": 0},
                "high": {"male": 0, "female": 0, "unspecified": 0},
                "teen": {"male": 0, "female": 0, "unspecified": 0},
                "adult": {"male": 0, "female": 0, "unspecified": 0},
            },
            "equipment": [{"name": "드럼", "qty": 1}, {"name": "마이크", "qty": 2}],
        })
        assert r.status_code == 201
        d = client.get(f"{BASE}/reservations/{r.get_json()['access_id']}").get_json()
        assert d["applicant_age"] is None and d["applicant_address"] is None
        assert d["applicant_club"] is None
        assert d["requested_equipment"] == [
            {"name": "드럼", "qty": 1}, {"name": "마이크", "qty": 2}
        ]


class TestFormConfig:
    def test_defaults_seeded(self, client):
        d = client.get(f"{BASE}/form-config").get_json()
        titles = [g["title"] for g in d["equipment_catalog"]]
        assert "음악" in titles
        assert any("마이크" == i["name"] and i["qty"]
                   for g in d["equipment_catalog"] for i in g["items"])
        assert d["notice"] and d["rules"]

    def test_filtered_by_facility_type(self, client):
        # '음악' 분류는 연습실 계열에만 노출된다.
        d = client.get(f"{BASE}/form-config?facility_type=회의실").get_json()
        assert "음악" not in [g["title"] for g in d["equipment_catalog"]]
        d = client.get(f"{BASE}/form-config?facility_type=연습실").get_json()
        assert "음악" in [g["title"] for g in d["equipment_catalog"]]


class TestCancelDeadline:
    def test_can_cancel_flag_exposed(self, client):
        access_id = client.post(f"{BASE}/reservations",
                                json=reservation_payload()).get_json()["access_id"]
        d = client.get(f"{BASE}/reservations/{access_id}").get_json()
        assert d["can_cancel"] is True
        # 이용일 하루 전이 마감(기본 cancel_deadline_days=1).
        assert d["cancel_deadline"] == (date.today() + timedelta(days=2)).isoformat()

    def test_cancel_within_window(self, client):
        access_id = client.post(f"{BASE}/reservations",
                                json=reservation_payload()).get_json()["access_id"]
        res_id = client.get(f"{BASE}/reservations/{access_id}").get_json()["id"]
        assert client.post(f"{BASE}/reservations/{res_id}/cancel").status_code == 200

    def test_day_before_still_cancellable(self, admin_client):
        """'대관 1일 전까지' 이므로 하루 전날에는 아직 취소할 수 있다."""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        res_id = admin_client.post(f"{BASE}/admin/reservations",
                                   json=reservation_payload(date=tomorrow)).get_json()["id"]
        assert admin_client.get(f"{BASE}/admin/reservations/{res_id}").get_json()["can_cancel"]
        assert admin_client.post(f"{BASE}/reservations/{res_id}/cancel").status_code == 200

    def test_cancel_after_deadline_blocked(self, admin_client):
        """마감(이용 1일 전)을 지난 당일 예약은 신청자가 직접 취소할 수 없다."""
        today = date.today().isoformat()
        res_id = admin_client.post(f"{BASE}/admin/reservations",
                                   json=reservation_payload(date=today)).get_json()["id"]
        d = admin_client.get(f"{BASE}/admin/reservations/{res_id}").get_json()
        assert d["can_cancel"] is False
        r = admin_client.post(f"{BASE}/reservations/{res_id}/cancel")
        assert r.status_code == 400 and "직접 취소" in r.get_json()["error"]

    def test_admin_can_still_cancel_after_deadline(self, admin_client):
        """마감 이후에도 담당자는 상태 변경으로 취소할 수 있다."""
        today = date.today().isoformat()
        res_id = admin_client.post(f"{BASE}/admin/reservations",
                                   json=reservation_payload(date=today)).get_json()["id"]
        r = admin_client.put(f"{BASE}/admin/reservations/{res_id}", json=reservation_payload(
            date=today, status="cancelled"))
        assert r.status_code == 200


class TestPenalty:
    def _past_no_show(self, admin_client, **overrides):
        """30일 전 예약을 만들어 노쇼로 기록 → 6개월 재대관 제한 발생."""
        past = (date.today() - timedelta(days=30)).isoformat()
        res_id = admin_client.post(f"{BASE}/admin/reservations",
                                   json=reservation_payload(date=past, **overrides)).get_json()["id"]
        admin_client.post(f"{BASE}/admin/reservations/{res_id}/attendance",
                          json={"attendance": "no_show"})
        return res_id

    def test_no_show_blocks_new_booking(self, admin_client):
        self._past_no_show(admin_client)
        r = admin_client.post(f"{BASE}/reservations", json=reservation_payload())
        assert r.status_code == 400
        assert "부터 대관하실 수 있습니다" in r.get_json()["error"]

    def test_unverified_blocks_new_booking(self, admin_client):
        past = (date.today() - timedelta(days=30)).isoformat()
        res_id = admin_client.post(f"{BASE}/admin/reservations",
                                   json=reservation_payload(date=past)).get_json()["id"]
        admin_client.post(f"{BASE}/admin/reservations/{res_id}/attendance",
                          json={"attendance": "unverified"})
        assert admin_client.post(f"{BASE}/reservations",
                                 json=reservation_payload()).status_code == 400

    def test_attended_does_not_block(self, admin_client):
        past = (date.today() - timedelta(days=30)).isoformat()
        res_id = admin_client.post(f"{BASE}/admin/reservations",
                                   json=reservation_payload(date=past)).get_json()["id"]
        admin_client.post(f"{BASE}/admin/reservations/{res_id}/attendance",
                          json={"attendance": "attended"})
        assert admin_client.post(f"{BASE}/reservations",
                                 json=reservation_payload()).status_code == 201

    def test_other_applicant_unaffected(self, admin_client):
        self._past_no_show(admin_client)
        assert admin_client.post(f"{BASE}/reservations", json=reservation_payload(
            name="다른사람", contact="010-9999-8888")).status_code == 201

    def test_clearing_attendance_lifts_block(self, admin_client):
        res_id = self._past_no_show(admin_client)
        admin_client.post(f"{BASE}/admin/reservations/{res_id}/attendance",
                          json={"attendance": ""})
        assert admin_client.post(f"{BASE}/reservations",
                                 json=reservation_payload()).status_code == 201

    def test_expired_penalty_does_not_block(self, admin_client):
        # 7개월 전 노쇼 → 6개월 제한이 이미 끝났다.
        long_ago = (date.today() - timedelta(days=220)).isoformat()
        res_id = admin_client.post(f"{BASE}/admin/reservations",
                                   json=reservation_payload(date=long_ago)).get_json()["id"]
        admin_client.post(f"{BASE}/admin/reservations/{res_id}/attendance",
                          json={"attendance": "no_show"})
        assert admin_client.post(f"{BASE}/reservations",
                                 json=reservation_payload()).status_code == 201
