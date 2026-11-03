"""Calendar behavior at real DST boundaries, leap days and missed intervals."""

from datetime import UTC, datetime

import pytest

from app.errors import APIError
from app.schedule_clock import Calendar, instant, next_due


def test_zone_conversion_and_shanghai_weekdays():
    assert instant(datetime(2026, 11, 2, 9), "Asia/Shanghai") == datetime(2026, 11, 2, 1, tzinfo=UTC)
    calendar = Calendar.parse("0 9 * * 1-5")
    assert calendar.next(datetime(2026, 10, 30, 2, tzinfo=UTC), "Asia/Shanghai") == datetime(2026, 11, 2, 1, tzinfo=UTC)


def test_dst_gap_is_skipped_and_ambiguous_minute_runs_only_once():
    assert Calendar.parse("30 2 * * *").next(datetime(2026, 3, 8, tzinfo=UTC), "America/New_York") == datetime(
        2026, 3, 9, 6, 30, tzinfo=UTC
    )
    calendar = Calendar.parse("30 1 * * *")
    assert calendar.next(datetime(2026, 11, 1, tzinfo=UTC), "America/New_York") == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )
    assert calendar.next(datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "America/New_York") == datetime(
        2026, 11, 2, 6, 30, tzinfo=UTC
    )
    assert instant(datetime(2026, 11, 1, 1, 30), "America/New_York") == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    with pytest.raises(APIError) as gap:
        instant(datetime(2026, 3, 8, 2, 30), "America/New_York")
    assert gap.value.code == "LOCAL_TIME_MISSING"


def test_leap_day_or_weekday_and_sunday_alias():
    assert Calendar.parse("0 0 29 2 *").next(datetime(2026, 1, 1, tzinfo=UTC), "UTC") == datetime(
        2028, 2, 29, tzinfo=UTC
    )
    assert Calendar.parse("0 9 1 * 1").next(datetime(2026, 11, 2, tzinfo=UTC), "UTC") == datetime(
        2026, 11, 2, 9, tzinfo=UTC
    )
    assert Calendar.parse("0 9 * * 7").next(datetime(2026, 11, 1, tzinfo=UTC), "UTC") == datetime(
        2026, 11, 1, 9, tzinfo=UTC
    )


def test_ranges_lists_steps_and_interval_catchup_keep_anchor():
    calendar = Calendar.parse("5/15 9-10 * * 1,3,5")
    assert calendar.next(datetime(2026, 11, 2, 9, 5, tzinfo=UTC), "UTC") == datetime(2026, 11, 2, 9, 20, tzinfo=UTC)
    assert next_due(
        "interval",
        datetime(2026, 11, 1, 10, 3, 29, tzinfo=UTC),
        start=datetime(2026, 11, 1, 10, tzinfo=UTC),
        timezone="UTC",
        interval_seconds=60,
    ) == datetime(2026, 11, 1, 10, 4, tzinfo=UTC)
    assert (
        next_due("once", datetime(2026, 11, 2, tzinfo=UTC), start=datetime(2026, 11, 1, tzinfo=UTC), timezone="UTC")
        is None
    )


@pytest.mark.parametrize(
    "expression",
    ["* * * *", "61 * * * *", "0 25 * * *", "*/0 * * * *", "0 0 L * *", "0 0 * * MON", "0 0 * 13 *", "0 0 * * 9"],
)
def test_bad_calendars_are_rejected(expression):
    with pytest.raises(APIError):
        Calendar.parse(expression)


def test_impossible_date_and_unknown_zone_fail_without_hanging():
    with pytest.raises(APIError) as no_date:
        Calendar.parse("0 0 31 2 *").next(datetime(2026, 1, 1, tzinfo=UTC), "UTC")
    assert no_date.value.code == "CRON_NO_DATE"
    with pytest.raises(APIError) as no_zone:
        instant(datetime(2026, 1, 1), "Not/A_Timezone")
    assert no_zone.value.code == "TIMEZONE_INVALID"
