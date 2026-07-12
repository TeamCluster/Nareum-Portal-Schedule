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
    def test_defaults(self):
        assert rs.parse_participants({"middle": 3}) == {
            "elementary": 0, "middle": 3, "high": 0, "teen": 0, "adult": 0
        }

    def test_empty(self):
        assert sum(rs.parse_participants({}).values()) == 0

    def test_invalid(self):
        with pytest.raises(ApiError, match="참가 인원"):
            rs.parse_participants({"middle": "많이"})


class TestBookingWindow:
    def test_uses_config(self):
        min_date, max_date = rs.booking_window()
        assert min_date == date.today() + timedelta(days=config.BOOKING_MIN_DAYS)
        assert max_date == date.today() + timedelta(days=config.BOOKING_MAX_DAYS)
