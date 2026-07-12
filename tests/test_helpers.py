"""Unit tests for the pure business-rule helpers (api/helpers.py)."""

import pytest

from api.helpers import (
    ApiError,
    booking_window,
    parse_participants,
    resolve_hours,
)


class TestResolveHours:
    def test_single_hour(self):
        assert resolve_hours([10]) == (10, 11)

    def test_contiguous_range(self):
        assert resolve_hours([10, 11, 12]) == (10, 13)

    def test_unsorted_input_is_sorted(self):
        assert resolve_hours([12, 10, 11]) == (10, 13)

    def test_empty_raises(self):
        with pytest.raises(ApiError, match="하나 이상"):
            resolve_hours([])

    def test_non_contiguous_raises(self):
        with pytest.raises(ApiError, match="연속된"):
            resolve_hours([10, 12])

    def test_exceeds_max_hours_raises(self):
        with pytest.raises(ApiError, match="최대 2시간"):
            resolve_hours([10, 11, 12], max_hours=2)

    def test_within_max_hours_ok(self):
        assert resolve_hours([10, 11], max_hours=2) == (10, 12)

    def test_outside_operating_hours_raises(self):
        with pytest.raises(ApiError, match="운영 시간"):
            resolve_hours([8])
        with pytest.raises(ApiError, match="운영 시간"):
            resolve_hours([18])

    def test_last_bookable_hour_is_17(self):
        assert resolve_hours([17]) == (17, 18)

    def test_non_numeric_raises(self):
        with pytest.raises(ApiError, match="올바르지 않"):
            resolve_hours(["abc"])


class TestParseParticipants:
    def test_defaults_missing_to_zero(self):
        result = parse_participants({"middle": 3})
        assert result == {"elementary": 0, "middle": 3, "high": 0, "teen": 0, "adult": 0}

    def test_all_keys(self):
        result = parse_participants(
            {"elementary": 1, "middle": 2, "high": 3, "teen": 4, "adult": 5}
        )
        assert sum(result.values()) == 15

    def test_empty_is_all_zero(self):
        assert sum(parse_participants({}).values()) == 0

    def test_invalid_value_raises(self):
        with pytest.raises(ApiError, match="참가 인원"):
            parse_participants({"middle": "many"})


class TestBookingWindow:
    def test_window_uses_config(self, app):
        from datetime import date, timedelta

        with app.app_context():
            min_date, max_date = booking_window()
            assert min_date == date.today() + timedelta(days=2)  # TestConfig MIN
            assert max_date == date.today() + timedelta(days=60)  # TestConfig MAX
