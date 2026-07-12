"""공통 휴무일/공휴일(슈퍼) + 기관 정책(공휴일 운영/제외) 통합 테스트."""
from conftest import SLUG, reservation_payload
from test_scheduling import date_with_weekday, default_hours

BASE = f"/api/{SLUG}"


def weekday_hours_split():
    """평일(월~금) 09~20, 주말(토·일) 09~18 로 설정한 payload."""
    items = default_hours()
    for it in items:
        it["close_hour"] = 20 if it["weekday"] <= 4 else 18
    return items


class TestSuperCommonHolidays:
    def test_crud(self, super_client):
        d = date_with_weekday(2).isoformat()
        assert super_client.post("/api/super/holidays",
                                 json={"date": d, "name": "설날", "type": "holiday"}).status_code == 201
        hs = super_client.get("/api/super/holidays").get_json()["holidays"]
        assert any(h["date"] == d and h["type"] == "holiday" for h in hs)
        hid = next(h["id"] for h in hs if h["date"] == d)
        assert super_client.delete(f"/api/super/holidays/{hid}").status_code == 200

    def test_duplicate(self, super_client):
        d = date_with_weekday(3).isoformat()
        super_client.post("/api/super/holidays", json={"date": d, "type": "holiday"})
        assert super_client.post("/api/super/holidays", json={"date": d, "type": "holiday"}).status_code == 400

    def test_guard(self, client):
        assert client.get("/api/super/holidays").status_code == 401


class TestCommonHolidayAppliesToOrg:
    def test_closure_type_closes_org(self, super_client, admin_client, client):
        d = date_with_weekday(2).isoformat()
        super_client.post("/api/super/holidays", json={"date": d, "name": "임시휴관", "type": "closure"})
        cfg = client.get(f"{BASE}/day-config?date={d}").get_json()
        assert cfg["is_open"] is False and "임시휴관" in cfg["closed_reason"]

    def test_holiday_type_closed_by_default(self, super_client, client):
        d = date_with_weekday(2).isoformat()
        super_client.post("/api/super/holidays", json={"date": d, "name": "삼일절", "type": "holiday"})
        cfg = client.get(f"{BASE}/day-config?date={d}").get_json()
        # 기본 holiday_operates=0 → 공휴일 휴무
        assert cfg["is_open"] is False and "삼일절" in cfg["closed_reason"]


class TestHolidayOperatesWeekendHours:
    def test_operates_uses_sunday_hours(self, super_client, admin_client, client):
        # 평일 09~20, 주말 09~18
        admin_client.put(f"{BASE}/admin/operating-hours", json={"operating_hours": weekday_hours_split()})
        # 공휴일 운영 on
        assert admin_client.put(f"{BASE}/admin/holiday-setting",
                                json={"holiday_operates": True}).status_code == 200
        wed = date_with_weekday(2).isoformat()  # 평일(수) → 원래 09~20
        super_client.post("/api/super/holidays", json={"date": wed, "name": "공휴일", "type": "holiday"})
        cfg = client.get(f"{BASE}/day-config?date={wed}").get_json()
        # 공휴일이라 주말(일요일) 시간 09~18 적용, 운영 유지
        assert cfg["is_open"] is True
        assert cfg["open_hour"] == 9 and cfg["close_hour"] == 18
        assert "공휴일" in cfg["note"]
        # 19시 예약은 불가(주말 18시 마감)
        r = client.post(f"{BASE}/reservations", json=reservation_payload(date=wed, hours=[19]))
        assert r.status_code == 400
        # 17시는 가능
        r = client.post(f"{BASE}/reservations", json=reservation_payload(date=wed, hours=[17]))
        assert r.status_code == 201


class TestOrgExclude:
    def test_exclude_ignores_common_holiday(self, super_client, admin_client, client):
        d = date_with_weekday(2).isoformat()
        super_client.post("/api/super/holidays", json={"date": d, "name": "공휴일", "type": "holiday"})
        # 기본은 휴무
        assert client.get(f"{BASE}/day-config?date={d}").get_json()["is_open"] is False
        # 이 기관에서 제외
        assert admin_client.post(f"{BASE}/admin/holiday-excludes", json={"date": d}).status_code == 200
        assert client.get(f"{BASE}/day-config?date={d}").get_json()["is_open"] is True
        # 제외 해제 → 다시 휴무
        assert admin_client.delete(f"{BASE}/admin/holiday-excludes/{d}").status_code == 200
        assert client.get(f"{BASE}/day-config?date={d}").get_json()["is_open"] is False

    def test_holidays_view_marks_excluded(self, super_client, admin_client):
        d = date_with_weekday(2).isoformat()
        super_client.post("/api/super/holidays", json={"date": d, "name": "공휴일", "type": "holiday"})
        admin_client.post(f"{BASE}/admin/holiday-excludes", json={"date": d})
        view = admin_client.get(f"{BASE}/admin/holidays").get_json()
        assert view["holiday_operates"] is False
        common = next(c for c in view["common"] if c["date"] == d)
        assert common["excluded"] is True


class TestOrgSpecificClosure:
    def test_add_with_type_and_block(self, admin_client, client):
        d = date_with_weekday(3).isoformat()
        assert admin_client.post(f"{BASE}/admin/closures",
                                 json={"date": d, "name": "센터 행사", "type": "closure"}).status_code == 201
        view = admin_client.get(f"{BASE}/admin/holidays").get_json()
        assert any(p["date"] == d and p["type"] == "closure" for p in view["place"])
        assert client.get(f"{BASE}/day-config?date={d}").get_json()["is_open"] is False
