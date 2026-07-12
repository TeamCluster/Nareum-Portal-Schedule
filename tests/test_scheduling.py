"""운영시간 / 휴무일 / 정기 고정활동 / 시설 이미지 통합 테스트."""
import io
from datetime import date, timedelta

from conftest import SLUG, reservation_payload

BASE = f"/api/{SLUG}"


def date_with_weekday(wd, min_offset=3):
    """예약창 내(오늘+min_offset 이후)에서 주어진 요일(월=0..일=6)의 날짜."""
    d = date.today() + timedelta(days=min_offset)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d


def default_hours():
    return [{"weekday": wd, "is_open": True, "open_hour": 9, "close_hour": 18} for wd in range(7)]


class TestOperatingHours:
    def test_default(self, admin_client):
        oh = admin_client.get(f"{BASE}/admin/operating-hours").get_json()["operating_hours"]
        assert len(oh) == 7
        assert all(d["is_open"] and d["open_hour"] == 9 and d["close_hour"] == 18 for d in oh)

    def test_set_and_get(self, admin_client):
        items = default_hours()
        items[0]["is_open"] = False           # 월 휴무
        items[1]["close_hour"] = 20            # 화 09~20
        r = admin_client.put(f"{BASE}/admin/operating-hours", json={"operating_hours": items})
        assert r.status_code == 200
        oh = admin_client.get(f"{BASE}/admin/operating-hours").get_json()["operating_hours"]
        assert oh[0]["is_open"] is False
        assert oh[1]["close_hour"] == 20

    def test_invalid_range_rejected(self, admin_client):
        items = default_hours()
        items[2]["open_hour"] = 20
        items[2]["close_hour"] = 9  # 시작>종료
        assert admin_client.put(f"{BASE}/admin/operating-hours",
                                json={"operating_hours": items}).status_code == 400

    def test_requires_login(self, client):
        assert client.get(f"{BASE}/admin/operating-hours").status_code == 401


class TestClosedDayBlocksBooking:
    def test_closed_weekday(self, client, admin_client):
        items = default_hours()
        items[0]["is_open"] = False  # 월 휴무
        admin_client.put(f"{BASE}/admin/operating-hours", json={"operating_hours": items})
        monday = date_with_weekday(0).isoformat()
        # day-config 반영
        cfg = client.get(f"{BASE}/day-config?date={monday}").get_json()
        assert cfg["is_open"] is False
        # 예약 시도 → 거부
        r = client.post(f"{BASE}/reservations", json=reservation_payload(date=monday, hours=[10]))
        assert r.status_code == 400

    def test_extended_hours_allow_19(self, client, admin_client):
        items = default_hours()
        tue = date_with_weekday(1)
        items[1]["close_hour"] = 20  # 화 09~20
        admin_client.put(f"{BASE}/admin/operating-hours", json={"operating_hours": items})
        # 19시는 09~18 기본이면 불가했지만 20시까지면 가능
        r = client.post(f"{BASE}/reservations",
                        json=reservation_payload(date=tue.isoformat(), hours=[19], name="야간", contact="010-0000-0001"))
        assert r.status_code == 201


class TestClosures:
    def test_add_list_delete(self, admin_client):
        d = date_with_weekday(2).isoformat()  # 수요일(운영일)
        assert admin_client.post(f"{BASE}/admin/closures",
                                 json={"date": d, "reason": "행사"}).status_code == 201
        closures = admin_client.get(f"{BASE}/admin/closures").get_json()["closures"]
        assert any(c["date"] == d for c in closures)
        cid = next(c["id"] for c in closures if c["date"] == d)
        assert admin_client.delete(f"{BASE}/admin/closures/{cid}").status_code == 200

    def test_closure_blocks_booking(self, client, admin_client):
        d = date_with_weekday(2).isoformat()
        admin_client.post(f"{BASE}/admin/closures", json={"date": d, "reason": "공휴일"})
        cfg = client.get(f"{BASE}/day-config?date={d}").get_json()
        assert cfg["is_open"] is False and "공휴일" in cfg["closed_reason"]
        r = client.post(f"{BASE}/reservations", json=reservation_payload(date=d, hours=[10]))
        assert r.status_code == 400

    def test_duplicate_closure(self, admin_client):
        d = date_with_weekday(3).isoformat()
        admin_client.post(f"{BASE}/admin/closures", json={"date": d})
        assert admin_client.post(f"{BASE}/admin/closures", json={"date": d}).status_code == 400


class TestRecurringBlocks:
    def test_add_and_block_booking(self, client, admin_client):
        wd = 1  # 화요일
        tue = date_with_weekday(wd).isoformat()
        r = admin_client.post(f"{BASE}/admin/recurring-blocks", json={
            "facility_id": 1, "weekday": wd, "start_hour": 10, "end_hour": 12, "title": "밴드 정기연습"})
        assert r.status_code == 201
        # booked-times 에 10,11 포함
        bt = client.get(f"{BASE}/facilities/1/booked-times?date={tue}").get_json()
        assert 10 in bt and 11 in bt
        # 겹치는 예약 거부
        r = client.post(f"{BASE}/reservations", json=reservation_payload(date=tue, facility_id=1, hours=[11]))
        assert r.status_code == 400 and "정기" in r.get_json()["error"]
        # 겹치지 않는 시간은 가능
        r = client.post(f"{BASE}/reservations", json=reservation_payload(date=tue, facility_id=1, hours=[13, 14]))
        assert r.status_code == 201

    def test_list_and_delete(self, admin_client):
        admin_client.post(f"{BASE}/admin/recurring-blocks",
                          json={"facility_id": 1, "weekday": 3, "start_hour": 15, "end_hour": 16})
        blocks = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"]
        assert len(blocks) == 1 and blocks[0]["facility_name"]
        bid = blocks[0]["id"]
        assert admin_client.delete(f"{BASE}/admin/recurring-blocks/{bid}").status_code == 200
        assert admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"] == []

    def test_day_grid_shows_block(self, admin_client):
        wd = 2
        wed = date_with_weekday(wd).isoformat()
        admin_client.post(f"{BASE}/admin/recurring-blocks",
                          json={"facility_id": 1, "weekday": wd, "start_hour": 14, "end_hour": 16, "title": "수업"})
        grid = admin_client.get(f"{BASE}/admin/day-grid?date={wed}").get_json()
        fac = next(f for f in grid["facilities"] if f["id"] == 1)
        block_segs = [s for s in fac["segments"] if s["type"] == "block"]
        assert len(block_segs) == 1 and block_segs[0]["from_hour"] == 14 and block_segs[0]["to_hour"] == 16


class TestAdminBypass:
    """관리자 직접 추가/수정은 휴무일·정기활동을 무시(경고만), 실제 중복은 차단."""

    def test_add_on_closed_day_warns(self, admin_client):
        items = default_hours()
        items[0]["is_open"] = False  # 월 휴무
        admin_client.put(f"{BASE}/admin/operating-hours", json={"operating_hours": items})
        mon = date_with_weekday(0).isoformat()
        r = admin_client.post(f"{BASE}/admin/reservations",
                              json=reservation_payload(date=mon, hours=[10]))
        assert r.status_code == 201
        assert any("휴무" in w for w in r.get_json()["warnings"])

    def test_add_over_block_warns(self, admin_client):
        wd = 1
        tue = date_with_weekday(wd).isoformat()
        admin_client.post(f"{BASE}/admin/recurring-blocks",
                          json={"facility_id": 1, "weekday": wd, "start_hour": 10, "end_hour": 12})
        r = admin_client.post(f"{BASE}/admin/reservations",
                              json=reservation_payload(date=tue, facility_id=1, hours=[10, 11]))
        assert r.status_code == 201
        assert any("정기" in w for w in r.get_json()["warnings"])

    def test_add_normal_no_warning(self, admin_client):
        r = admin_client.post(f"{BASE}/admin/reservations", json=reservation_payload())
        assert r.status_code == 201 and r.get_json()["warnings"] == []

    def test_real_overlap_still_blocked(self, admin_client):
        admin_client.post(f"{BASE}/admin/reservations", json=reservation_payload(hours=[10, 11]))
        r = admin_client.post(f"{BASE}/admin/reservations", json=reservation_payload(hours=[11]))
        assert r.status_code == 400

    def test_admin_booked_times_splits_reserved_and_blocked(self, admin_client):
        wd = 1
        tue = date_with_weekday(wd).isoformat()
        admin_client.post(f"{BASE}/admin/recurring-blocks",
                          json={"facility_id": 1, "weekday": wd, "start_hour": 10, "end_hour": 12})
        d = admin_client.get(f"{BASE}/admin/booked-times?facility_id=1&date={tue}").get_json()
        assert d["reserved"] == [] and d["blocked"] == [10, 11]


class TestAvailabilityRange:
    def test_reflects_operating_hours(self, client, admin_client):
        items = default_hours()
        tue = date_with_weekday(1)
        items[1]["close_hour"] = 20
        admin_client.put(f"{BASE}/admin/operating-hours", json={"operating_hours": items})
        d = client.get(f"{BASE}/availability?date={tue.isoformat()}").get_json()
        assert d["open_hour"] == 9 and d["close_hour"] == 20
        assert "19" in d["facilities"][0]["hours"]

    def test_closed_day_not_reservable(self, client, admin_client):
        items = default_hours()
        items[0]["is_open"] = False
        admin_client.put(f"{BASE}/admin/operating-hours", json={"operating_hours": items})
        mon = date_with_weekday(0).isoformat()
        d = client.get(f"{BASE}/availability?date={mon}").get_json()
        assert d["is_reservable"] is False and d["is_open"] is False
        assert d["facilities"][0]["hours"] == {}


_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


class TestFacilityImage:
    def test_upload_and_replace(self, admin_client):
        r = admin_client.post(f"{BASE}/admin/facilities/1/image",
                              data={"image": (io.BytesIO(_PNG), "photo.png")},
                              content_type="multipart/form-data")
        assert r.status_code == 200
        url1 = r.get_json()["image_url"]
        assert url1.startswith(f"/static/{SLUG}/facility/")
        # 시설 조회에 반영
        fac = next(f for f in admin_client.get(f"{BASE}/admin/facilities").get_json() if f["id"] == 1)
        assert fac["image_url"] == url1
        # 재업로드 시 URL 이 바뀜(새 파일)
        r2 = admin_client.post(f"{BASE}/admin/facilities/1/image",
                               data={"image": (io.BytesIO(_PNG), "photo.png")},
                               content_type="multipart/form-data")
        assert r2.get_json()["image_url"] != url1

    def test_reject_non_image(self, admin_client):
        r = admin_client.post(f"{BASE}/admin/facilities/1/image",
                              data={"image": (io.BytesIO(b"hello"), "note.txt")},
                              content_type="multipart/form-data")
        assert r.status_code == 400

    def test_requires_login(self, client):
        assert client.post(f"{BASE}/admin/facilities/1/image").status_code == 401

    def test_delete_never_touches_shared_default_image(self):
        """delete_image_file 은 static/img/(공유 기본 이미지)를 삭제하지 않는다."""
        import os
        import config
        from services import image_service
        shared = os.path.join(config.STATIC_ROOT, "img")
        os.makedirs(shared, exist_ok=True)
        path = os.path.join(shared, "room001.jpg")
        with open(path, "wb") as f:
            f.write(b"default")
        image_service.delete_image_file("/static/img/room001.jpg")
        assert os.path.isfile(path)  # 삭제되지 않아야 함
