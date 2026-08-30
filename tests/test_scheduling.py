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


def month_of(d):
    """date → 'YYYY-MM' (동아리 정기활동의 적용 월)."""
    return f"{d.year:04d}-{d.month:02d}"


def block_payload(**over):
    """정기활동 등록 payload — 유형별 기간 키를 모두 담아 둔다.

    서버는 kind 에 따라 club 이면 month 만, program/etc 면 start_date·end_date 만
    보므로 한 payload 에 다 넣어도 안전하다. 기본 기간은 넉넉히 잡아
    date_with_weekday() 가 다음 달로 넘어가도 덮이게 한다.
    """
    today = date.today()
    payload = {"facility_id": 1, "weekday": 1, "start_hour": 10, "end_hour": 12,
               "title": "밴드 정기연습", "kind": "etc",
               "month": month_of(today),
               "start_date": today.isoformat(),
               "end_date": (today + timedelta(days=120)).isoformat()}
    payload.update(over)
    return payload


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
                          json=block_payload(weekday=3, start_hour=15, end_hour=16,
                                             title="목요 프로그램", kind="program"))
        blocks = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"]
        assert len(blocks) == 1 and blocks[0]["facility_name"]
        bid = blocks[0]["id"]
        assert admin_client.delete(f"{BASE}/admin/recurring-blocks/{bid}").status_code == 200
        assert admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"] == []

    def test_reservation_takes_priority_over_block_in_grid(self, admin_client):
        """예약이 정기활동 시간대를 덮으면 그리드에서 예약이 우선 표시된다."""
        wd = 2
        wed = date_with_weekday(wd).isoformat()
        # 정기활동 10~14
        admin_client.post(f"{BASE}/admin/recurring-blocks",
                          json={"facility_id": 1, "weekday": wd, "start_hour": 10, "end_hour": 14, "title": "정기"})
        # 관리자 직접 추가로 그 사이 11~12 예약(정기활동 무시)
        r = admin_client.post(f"{BASE}/admin/reservations",
                              json=reservation_payload(date=wed, facility_id=1, hours=[11]))
        assert r.status_code == 201
        grid = admin_client.get(f"{BASE}/admin/day-grid?date={wed}").get_json()
        fac = next(f for f in grid["facilities"] if f["id"] == 1)
        segs = fac["segments"]
        res_segs = [s for s in segs if s["type"] == "res"]
        block_segs = [s for s in segs if s["type"] == "block"]
        # 예약 세그먼트가 11~12 로 정확히 보임
        assert len(res_segs) == 1 and res_segs[0]["from_hour"] == 11 and res_segs[0]["to_hour"] == 12
        # 정기활동은 예약 앞뒤로 쪼개져 보임(10~11, 12~14)
        assert {(s["from_hour"], s["to_hour"]) for s in block_segs} == {(10, 11), (12, 14)}

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
                          json=block_payload(weekday=wd, kind="club",
                                             month=month_of(date_with_weekday(wd))))
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
                          json=block_payload(weekday=wd, kind="club",
                                             month=month_of(date_with_weekday(wd))))
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

    def test_reject_disguised_extension(self, admin_client):
        """확장자만 .png 인 비이미지 파일은 내용 검사에서 걸러진다."""
        r = admin_client.post(f"{BASE}/admin/facilities/1/image",
                              data={"image": (io.BytesIO(b"#!/bin/sh\necho hi\n"), "evil.png")},
                              content_type="multipart/form-data")
        assert r.status_code == 400

    def test_stored_extension_follows_content(self, admin_client):
        """저장 확장자는 파일명이 아니라 실제 형식을 따른다."""
        r = admin_client.post(f"{BASE}/admin/facilities/1/image",
                              data={"image": (io.BytesIO(_PNG), "photo.jpeg")},
                              content_type="multipart/form-data")
        assert r.status_code == 200
        assert r.get_json()["image_url"].endswith(".png")

    def test_oversized_request_rejected(self, admin_client):
        """MAX_CONTENT_LENGTH 초과 요청은 413 + JSON 으로 끊긴다."""
        import config
        blob = b"\x89PNG\r\n\x1a\n" + b"0" * (config.MAX_CONTENT_LENGTH + 1024)
        r = admin_client.post(f"{BASE}/admin/facilities/1/image",
                              data={"image": (io.BytesIO(blob), "big.png")},
                              content_type="multipart/form-data")
        assert r.status_code == 413
        assert "error" in r.get_json()

    def test_requires_login(self, client):
        assert client.post(f"{BASE}/admin/facilities/1/image").status_code == 401

    def test_delete_rejects_path_traversal(self):
        """image_url 에 '..' 이 섞여도 STATIC_ROOT 밖 파일은 건드리지 않는다."""
        import os
        import config
        from services import image_service
        outside = os.path.join(os.path.dirname(os.path.realpath(config.STATIC_ROOT)),
                               "keep-me.txt")
        with open(outside, "wb") as f:
            f.write(b"important")
        try:
            image_service.delete_image_file("/static/../keep-me.txt")
            assert os.path.isfile(outside)  # 삭제되지 않아야 함
        finally:
            os.remove(outside)

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


class TestRecurringBlockKinds:
    """정기 고정활동은 동아리 정기활동 외에 프로그램·점검 등도 담는다."""

    def _add(self, admin_client, **over):
        defaults = dict(weekday=3, start_hour=15, end_hour=17,
                        title="방송댄스반", kind="program")
        return admin_client.post(f"{BASE}/admin/recurring-blocks",
                                 json=block_payload(**{**defaults, **over}))

    def test_kind_stored_with_label(self, admin_client):
        assert self._add(admin_client).status_code == 201
        b = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"][0]
        assert b["kind"] == "program" and b["kind_label"] == "프로그램"

    def test_club_kind(self, admin_client):
        self._add(admin_client, kind="club", title="하이라이트")
        b = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"][0]
        assert b["kind"] == "club" and b["kind_label"] == "동아리"

    def test_defaults_to_etc(self, admin_client):
        self._add(admin_client, kind=None, title="정기 시설 점검")
        b = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"][0]
        assert b["kind"] == "etc" and b["kind_label"] == "기타"

    def test_rejects_unknown_kind(self, admin_client):
        assert self._add(admin_client, kind="동아리").status_code == 400

    def test_title_required(self, admin_client):
        r = self._add(admin_client, title="   ")
        assert r.status_code == 400 and "프로그램명" in r.get_json()["error"]

    def test_title_message_matches_kind(self, admin_client):
        r = self._add(admin_client, kind="club", title="")
        assert "동아리명" in r.get_json()["error"]

    def test_kind_reaches_day_grid(self, admin_client):
        wd = 2
        wed_date = date_with_weekday(wd)
        wed = wed_date.isoformat()
        self._add(admin_client, weekday=wd, start_hour=14, end_hour=16,
                  kind="club", title="하이라이트", month=month_of(wed_date))
        grid = admin_client.get(f"{BASE}/admin/day-grid?date={wed}").get_json()
        fac = next(f for f in grid["facilities"] if f["id"] == 1)
        seg = next(s for s in fac["segments"] if s["type"] == "block")
        assert seg["title"] == "하이라이트" and seg["kind"] == "club"

    def test_update(self, admin_client):
        bid = self._add(admin_client).get_json()["id"]
        r = admin_client.put(f"{BASE}/admin/recurring-blocks/{bid}", json=block_payload(
            facility_id=2, weekday=4, start_hour=10, end_hour=12,
            title="하이라이트", kind="club"))
        assert r.status_code == 200
        b = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"][0]
        assert (b["facility_id"], b["weekday"], b["start_hour"], b["end_hour"]) == (2, 4, 10, 12)
        assert b["kind"] == "club" and b["title"] == "하이라이트"

    def test_update_validates(self, admin_client):
        bid = self._add(admin_client).get_json()["id"]
        assert admin_client.put(f"{BASE}/admin/recurring-blocks/{bid}", json=block_payload(
            weekday=3, start_hour=15, end_hour=17, title="", kind="program")).status_code == 400

    def test_update_unknown(self, admin_client):
        assert admin_client.put(f"{BASE}/admin/recurring-blocks/999", json=block_payload(
            weekday=3, start_hour=15, end_hour=17, title="x", kind="etc")).status_code == 404

    def test_update_requires_login(self, client):
        assert client.put(f"{BASE}/admin/recurring-blocks/1", json={}).status_code == 401


class TestRecurringBlockPeriod:
    """정기활동은 유형마다 적용 기간이 다르다.

    동아리는 매달 회의로 그 달치를 정하고(월 단위), 프로그램은 기수마다 시작일·
    종료일이 정해진다. 기간을 벗어난 일정은 요일이 같아도 더 이상 시간을 막지 않아야
    한다 — 지난달 동아리 일정이 이번 달 예약을 막으면 안 된다.
    """

    def _post(self, admin_client, **over):
        return admin_client.post(f"{BASE}/admin/recurring-blocks", json=block_payload(**over))

    def _only(self, admin_client):
        return admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"][0]

    # --- 월(동아리) → 1일~말일 -------------------------------------------
    def test_club_month_expands_to_whole_month(self, admin_client):
        assert self._post(admin_client, kind="club", month="2026-02").status_code == 201
        b = self._only(admin_client)
        assert b["effective_from"] == "2026-02-01" and b["effective_to"] == "2026-02-28"
        # 월 선택으로 되돌릴 수 있어야 수정 화면이 원래 입력을 복원한다.
        assert b["month"] == "2026-02" and b["period_label"] == "2026년 2월"

    def test_club_month_handles_31_day_month(self, admin_client):
        self._post(admin_client, kind="club", month="2026-01")
        assert self._only(admin_client)["effective_to"] == "2026-01-31"

    def test_club_month_required(self, admin_client):
        r = self._post(admin_client, kind="club", month="")
        assert r.status_code == 400 and "월" in r.get_json()["error"]

    def test_club_month_format_checked(self, admin_client):
        assert self._post(admin_client, kind="club", month="2026년 2월").status_code == 400

    # --- 시작·종료일(프로그램) -------------------------------------------
    def test_program_requires_both_dates(self, admin_client):
        r = self._post(admin_client, kind="program", start_date="2026-03-02", end_date="")
        assert r.status_code == 400 and "종료일" in r.get_json()["error"]

    def test_program_rejects_reversed_range(self, admin_client):
        r = self._post(admin_client, kind="program",
                       start_date="2026-05-01", end_date="2026-04-01")
        assert r.status_code == 400

    def test_program_keeps_given_range(self, admin_client):
        self._post(admin_client, kind="program",
                   start_date="2026-03-02", end_date="2026-06-30")
        b = self._only(admin_client)
        assert (b["effective_from"], b["effective_to"]) == ("2026-03-02", "2026-06-30")
        # 한 달과 정확히 겹치지 않으므로 월 표기로 접히면 안 된다.
        assert b["month"] == "" and b["period_label"] == "2026-03-02 ~ 2026-06-30"

    # --- 기타: 비우면 무기한 (기존 동작 유지) ------------------------------
    def test_etc_without_dates_is_open_ended(self, admin_client):
        assert self._post(admin_client, kind="etc",
                          start_date="", end_date="").status_code == 201
        b = self._only(admin_client)
        assert (b["effective_from"], b["effective_to"]) == ("", "")
        assert b["period_label"] == "기간 제한 없음" and b["status"] == "active"

    # --- 실제 점유: 기간 밖이면 막지 않는다 -------------------------------
    def test_expired_block_does_not_block_booking(self, client, admin_client):
        wd = 1
        tue = date_with_weekday(wd)
        last_month = (tue.replace(day=1) - timedelta(days=1))
        # 지난달로 끝난 동아리 일정 — 요일·시간은 그대로 겹친다.
        self._post(admin_client, weekday=wd, kind="club", month=month_of(last_month))
        assert client.get(
            f"{BASE}/facilities/1/booked-times?date={tue.isoformat()}").get_json() == []
        r = client.post(f"{BASE}/reservations",
                        json=reservation_payload(date=tue.isoformat(), facility_id=1, hours=[10, 11]))
        assert r.status_code == 201

    def test_active_month_block_still_blocks(self, client, admin_client):
        wd = 1
        tue = date_with_weekday(wd)
        self._post(admin_client, weekday=wd, kind="club", month=month_of(tue))
        assert 10 in client.get(
            f"{BASE}/facilities/1/booked-times?date={tue.isoformat()}").get_json()
        r = client.post(f"{BASE}/reservations",
                        json=reservation_payload(date=tue.isoformat(), facility_id=1, hours=[10]))
        assert r.status_code == 400 and "정기" in r.get_json()["error"]

    def test_program_outside_range_does_not_block(self, client, admin_client):
        wd = 2
        wed = date_with_weekday(wd)
        # 대상 날짜 하루 전에 끝나는 프로그램
        self._post(admin_client, weekday=wd, kind="program",
                   start_date=(wed - timedelta(days=30)).isoformat(),
                   end_date=(wed - timedelta(days=1)).isoformat())
        r = client.post(f"{BASE}/reservations",
                        json=reservation_payload(date=wed.isoformat(), facility_id=1, hours=[10]))
        assert r.status_code == 201

    def test_upcoming_block_does_not_block_dates_before_its_period(self, client, admin_client):
        """'예정'이라서가 아니라 날짜가 기간 밖이라서 안 막는다 (위 테스트와 짝)."""
        wd = 3
        thu = date_with_weekday(wd)
        next_month = (thu.replace(day=28) + timedelta(days=7))
        self._post(admin_client, weekday=wd, kind="club", month=month_of(next_month))
        r = client.post(f"{BASE}/reservations",
                        json=reservation_payload(date=thu.isoformat(), facility_id=1, hours=[10]))
        assert r.status_code == 201
        assert self._only(admin_client)["status"] == "upcoming"

    def test_upcoming_block_already_applies_inside_its_period(self, client, admin_client):
        """'예정'은 표시용 상태일 뿐 — 그 기간 안의 날짜는 지금도 막는다.

        다음 달 예약을 이번 달에 받으므로, 다음 달 동아리 일정이 '아직 시작 전'이라는
        이유로 뚫리면 안 된다. 기간 판정은 오늘이 아니라 '예약하려는 날짜' 기준이다.
        """
        wd = 1
        today = date.today()
        next_first = (today.replace(day=28) + timedelta(days=7)).replace(day=1)
        d = next_first
        while d.weekday() != wd:
            d += timedelta(days=1)
        if (d - today).days < 3:      # 예약창 최소 기한에 걸리지 않게 한 주 뒤로
            d += timedelta(days=7)

        self._post(admin_client, weekday=wd, kind="club", month=month_of(d))
        assert self._only(admin_client)["status"] == "upcoming"   # 목록에선 '예정'

        assert 10 in client.get(
            f"{BASE}/facilities/1/booked-times?date={d.isoformat()}").get_json()
        r = client.post(f"{BASE}/reservations",
                        json=reservation_payload(date=d.isoformat(), facility_id=1, hours=[10]))
        assert r.status_code == 400 and "정기" in r.get_json()["error"]

    def test_expired_block_hidden_from_day_grid(self, admin_client):
        wd = 2
        wed = date_with_weekday(wd)
        last_month = (wed.replace(day=1) - timedelta(days=1))
        self._post(admin_client, weekday=wd, kind="club", month=month_of(last_month))
        grid = admin_client.get(f"{BASE}/admin/day-grid?date={wed.isoformat()}").get_json()
        fac = next(f for f in grid["facilities"] if f["id"] == 1)
        assert [s for s in fac["segments"] if s["type"] == "block"] == []

    # --- 목록: 끝난 일정은 아래로 -----------------------------------------
    def test_ended_blocks_sort_last(self, admin_client):
        today = date.today()
        last_month = today.replace(day=1) - timedelta(days=1)
        self._post(admin_client, weekday=0, kind="club", title="지난달",
                   month=month_of(last_month))
        self._post(admin_client, weekday=6, kind="club", title="이번달",
                   month=month_of(today))
        blocks = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"]
        assert [b["title"] for b in blocks] == ["이번달", "지난달"]
        assert [b["status"] for b in blocks] == ["active", "ended"]

    # --- 수정으로 기간만 바꾸기 -------------------------------------------
    def test_update_moves_period_to_next_month(self, admin_client):
        bid = self._post(admin_client, kind="club", month="2026-03").get_json()["id"]
        r = admin_client.put(f"{BASE}/admin/recurring-blocks/{bid}",
                             json=block_payload(kind="club", month="2026-04"))
        assert r.status_code == 200
        b = self._only(admin_client)
        assert (b["effective_from"], b["effective_to"]) == ("2026-04-01", "2026-04-30")


class TestCopyMonthBlocks:
    """동아리 정기활동은 매달 다시 등록해야 한다 — 한 달치를 통째로 다음 달로 복제."""

    URL = f"{BASE}/admin/recurring-blocks/copy-month"

    def _club(self, admin_client, month, **over):
        return admin_client.post(f"{BASE}/admin/recurring-blocks",
                                 json=block_payload(kind="club", month=month, **over))

    def _titles(self, admin_client, month):
        blocks = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"]
        return sorted(b["title"] for b in blocks if b["month"] == month)

    def test_copies_all_club_blocks(self, admin_client):
        self._club(admin_client, "2026-03", weekday=1, title="하이라이트")
        self._club(admin_client, "2026-03", weekday=3, start_hour=14, end_hour=16, title="글로우")
        r = admin_client.post(self.URL, json={"from_month": "2026-03", "to_month": "2026-04"})
        assert r.status_code == 200 and r.get_json()["copied"] == 2
        assert self._titles(admin_client, "2026-04") == ["글로우", "하이라이트"]
        # 원본은 그대로 남는다.
        assert self._titles(admin_client, "2026-03") == ["글로우", "하이라이트"]

    def test_copied_block_keeps_slot_and_facility(self, admin_client):
        self._club(admin_client, "2026-03", facility_id=2, weekday=5,
                   start_hour=13, end_hour=15, title="하이라이트")
        admin_client.post(self.URL, json={"from_month": "2026-03", "to_month": "2026-04"})
        blocks = admin_client.get(f"{BASE}/admin/recurring-blocks").get_json()["blocks"]
        new = next(b for b in blocks if b["month"] == "2026-04")
        assert (new["facility_id"], new["weekday"], new["start_hour"], new["end_hour"]) == (2, 5, 13, 15)
        assert new["kind"] == "club" and new["period_label"] == "2026년 4월"

    def test_second_run_skips_duplicates(self, admin_client):
        self._club(admin_client, "2026-03", title="하이라이트")
        admin_client.post(self.URL, json={"from_month": "2026-03", "to_month": "2026-04"})
        r = admin_client.post(self.URL, json={"from_month": "2026-03", "to_month": "2026-04"})
        body = r.get_json()
        assert body["copied"] == 0 and body["skipped"] == 1
        assert self._titles(admin_client, "2026-04") == ["하이라이트"]

    def test_only_copies_club_kind(self, admin_client):
        self._club(admin_client, "2026-03", title="하이라이트")
        # 같은 기간을 가진 프로그램 — 기수 단위라 달 복제 대상이 아니다.
        admin_client.post(f"{BASE}/admin/recurring-blocks", json=block_payload(
            kind="program", weekday=4, title="방송댄스반",
            start_date="2026-03-01", end_date="2026-03-31"))
        r = admin_client.post(self.URL, json={"from_month": "2026-03", "to_month": "2026-04"})
        assert r.get_json()["copied"] == 1
        assert self._titles(admin_client, "2026-04") == ["하이라이트"]

    def test_copied_block_actually_blocks_booking(self, client, admin_client):
        wd = 1
        tue = date_with_weekday(wd)
        prev = tue.replace(day=1) - timedelta(days=1)      # 지난달
        self._club(admin_client, month_of(prev), weekday=wd)
        # 지난달 일정이라 이 날짜는 아직 비어 있다.
        booked = f"{BASE}/facilities/1/booked-times?date={tue.isoformat()}"
        assert client.get(booked).get_json() == []
        # 이번 달로 복제하면 그 시간이 막힌다.
        admin_client.post(self.URL, json={"from_month": month_of(prev),
                                          "to_month": month_of(tue)})
        assert client.get(booked).get_json() == [10, 11]
        r = client.post(f"{BASE}/reservations", json=reservation_payload(
            date=tue.isoformat(), facility_id=1, hours=[10]))
        assert r.status_code == 400 and "정기" in r.get_json()["error"]

    def test_empty_source_month(self, admin_client):
        r = admin_client.post(self.URL, json={"from_month": "2026-03", "to_month": "2026-04"})
        assert r.status_code == 400 and "없습니다" in r.get_json()["error"]

    def test_same_month_rejected(self, admin_client):
        self._club(admin_client, "2026-03")
        r = admin_client.post(self.URL, json={"from_month": "2026-03", "to_month": "2026-03"})
        assert r.status_code == 400

    def test_bad_month_format(self, admin_client):
        r = admin_client.post(self.URL, json={"from_month": "2026년 3월", "to_month": "2026-04"})
        assert r.status_code == 400

    def test_requires_login(self, client):
        assert client.post(self.URL, json={"from_month": "2026-03",
                                           "to_month": "2026-04"}).status_code == 401
