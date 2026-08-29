"""예약 도메인 규칙 단위 테스트 (reservation_service)."""
from datetime import date, timedelta

import pytest

import config
from services.errors import ApiError
from services import reservation_service as rs


class TestResolveHours:
    # resolve_hours(selected_hours, open_hour, close_hour, max_hours=None)
    def test_single(self):
        assert rs.resolve_hours([10], 9, 18) == (10, 11)

    def test_contiguous(self):
        assert rs.resolve_hours([10, 11, 12], 9, 18) == (10, 13)

    def test_unsorted(self):
        assert rs.resolve_hours([12, 10, 11], 9, 18) == (10, 13)

    def test_empty(self):
        with pytest.raises(ApiError, match="하나 이상"):
            rs.resolve_hours([], 9, 18)

    def test_non_contiguous(self):
        with pytest.raises(ApiError, match="연속된"):
            rs.resolve_hours([10, 12], 9, 18)

    def test_max_hours(self):
        with pytest.raises(ApiError, match="최대 2시간"):
            rs.resolve_hours([10, 11, 12], 9, 18, max_hours=2)

    def test_operating_hours(self):
        with pytest.raises(ApiError, match="운영 시간"):
            rs.resolve_hours([8], 9, 18)
        with pytest.raises(ApiError, match="운영 시간"):
            rs.resolve_hours([18], 9, 18)

    def test_extended_close_allows_19(self):
        # 운영 종료가 20시면 19시 예약 가능
        assert rs.resolve_hours([19], 9, 20) == (19, 20)

    def test_last_hour(self):
        assert rs.resolve_hours([17], 9, 18) == (17, 18)


class TestParticipants:
    def test_gender_split(self):
        # 종이 서식과 동일하게 연령대마다 남/여를 따로 저장한다.
        result = rs.parse_participants({"middle": {"male": 2, "female": 1}})
        assert result["middle"] == {"male": 2, "female": 1, "unspecified": 0}
        assert result["adult"] == {"male": 0, "female": 0, "unspecified": 0}
        assert set(result) == set(config.PARTICIPANT_BANDS)
        assert rs.total_participants(result) == 3

    def test_legacy_flat_value_preserved(self):
        # 성별 구분 도입 전 데이터({"middle": 3})는 unspecified 로 읽힌다.
        result = rs.parse_participants({"middle": 3})
        assert result["middle"] == {"male": 0, "female": 0, "unspecified": 3}
        assert rs.total_participants(result) == 3

    def test_empty(self):
        assert rs.total_participants(rs.parse_participants({})) == 0

    def test_invalid(self):
        with pytest.raises(ApiError, match="참가 인원"):
            rs.parse_participants({"middle": "많이"})

    def test_negative(self):
        with pytest.raises(ApiError, match="참가 인원"):
            rs.parse_participants({"middle": {"male": -1}})


class TestEquipment:
    def test_qty_item(self):
        assert rs.parse_equipment([{"name": "마이크", "qty": 3}]) == [
            {"name": "마이크", "qty": 3}
        ]

    def test_legacy_string_list(self):
        assert rs.parse_equipment(["앰프", "키보드"]) == [
            {"name": "앰프", "qty": 1}, {"name": "키보드", "qty": 1}
        ]

    def test_dedupe_and_blank(self):
        assert rs.parse_equipment(["드럼", " ", "드럼"]) == [{"name": "드럼", "qty": 1}]

    def test_empty(self):
        assert rs.parse_equipment(None) == []

    def test_invalid_qty(self):
        with pytest.raises(ApiError, match="수량"):
            rs.parse_equipment([{"name": "마이크", "qty": "두개"}])


class TestApplicant:
    def test_activity_required_for_public(self):
        with pytest.raises(ApiError, match="활동내용"):
            rs.parse_applicant({"name": "홍길동"}, require_activity=True)

    def test_activity_optional_for_admin(self):
        assert rs.parse_applicant({"name": "홍길동"})["activity"] == ""

    def test_optional_fields_normalized(self):
        a = rs.parse_applicant({"name": " 홍길동 ", "age": "17",
                                "address": " a@b.com ", "activity": "춤연습"})
        assert (a["name"], a["age"], a["address"]) == ("홍길동", 17, "a@b.com")
        assert a["school"] is None and a["club"] is None


class TestBookingWindow:
    def test_uses_config(self):
        min_date, max_date = rs.booking_window()
        assert min_date == date.today() + timedelta(days=config.BOOKING_MIN_DAYS)
        assert max_date == date.today() + timedelta(days=config.BOOKING_MAX_DAYS)


class TestBookingRuleHelpers:
    def test_add_months_normal(self):
        assert rs.add_months(date(2026, 3, 15), 6) == date(2026, 9, 15)

    def test_add_months_crosses_year(self):
        assert rs.add_months(date(2026, 10, 5), 6) == date(2027, 4, 5)

    def test_add_months_clamps_month_end(self):
        # 8/31 + 6개월 = 2월 → 말일로 보정
        assert rs.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)

    def test_cancel_deadline(self):
        rules = {"cancel_deadline_days": 1}
        assert rs.cancel_deadline("2026-09-10T10:00:00", rules) == date(2026, 9, 9)

    def test_cancel_deadline_same_day_allowed(self):
        rules = {"cancel_deadline_days": 0}
        assert rs.cancel_deadline("2026-09-10T10:00:00", rules) == date(2026, 9, 10)

    def test_rules_default_without_conn(self):
        assert rs.get_booking_rules() == config.DEFAULT_BOOKING_RULES

    def test_booking_window_uses_rules(self):
        rules = {**config.DEFAULT_BOOKING_RULES, "booking_min_days": 5, "booking_max_days": 9}
        today = date(2026, 1, 1)
        assert rs.booking_window(today=today, rules=rules) == (date(2026, 1, 6), date(2026, 1, 10))
