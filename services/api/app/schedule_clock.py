"""Minute-resolution calendars with explicit IANA timezone and DST rules."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.errors import fail


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        fail(422, "TIMEZONE_INVALID", "请选择有效时区，例如北京时间 Asia/Shanghai")


def instant(value: datetime, timezone: str) -> datetime:
    """Naive form values refer to the selected zone; ambiguous values use the first occurrence."""
    selected = zone(timezone)
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    local = value.replace(tzinfo=selected, fold=0)
    result = local.astimezone(UTC)
    if result.astimezone(selected).replace(tzinfo=None) != value:
        fail(422, "LOCAL_TIME_MISSING", "该本地时间因夏令时调整不存在，请选择其他时刻")
    return result


def field(expression: str, minimum: int, maximum: int) -> frozenset[int]:
    result = set()
    try:
        for part in expression.split(","):
            pieces = part.split("/")
            if len(pieces) > 2:
                raise ValueError
            base, step = pieces[0], int(pieces[1]) if len(pieces) == 2 else 1
            if step < 1 or step > maximum - minimum + 1:
                raise ValueError
            if base == "*":
                start, end = minimum, maximum
            elif "-" in base:
                bounds = base.split("-")
                if len(bounds) != 2:
                    raise ValueError
                start, end = map(int, bounds)
            else:
                start = int(base)
                end = maximum if len(pieces) == 2 else start
            if not minimum <= start <= end <= maximum:
                raise ValueError
            result.update(range(start, end + 1, step))
    except (ValueError, TypeError):
        fail(422, "CRON_INVALID", "Cron 支持五字段的整数、*、列表、范围和步长，请检查取值")
    if not result:
        fail(422, "CRON_INVALID", "Cron 字段不能为空")
    return frozenset(result)


@dataclass(frozen=True)
class Calendar:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, expression: str) -> "Calendar":
        pieces = expression.split()
        if len(pieces) != 5 or len(expression) > 120:
            fail(422, "CRON_INVALID", "请输入五字段 Cron：分 时 日 月 星期，例如 0 9 * * 1-5")
        values = [
            field(value, low, high)
            for value, (low, high) in zip(pieces, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)], strict=True)
        ]
        return cls(*values[:4], frozenset(day % 7 for day in values[4]), pieces[2] == "*", pieces[4] == "*")

    def next(self, after: datetime, timezone: str) -> datetime:
        if after.tzinfo is None:
            fail(422, "TIMEZONE_REQUIRED", "计算执行时间需要明确时区")
        selected = zone(timezone)
        first = after.astimezone(selected).date()
        for offset in range(366 * 8):
            try:
                day = first + timedelta(days=offset)
            except OverflowError:
                break
            if day.month not in self.months:
                continue
            dom, dow = day.day in self.days, (day.weekday() + 1) % 7 in self.weekdays
            matches = dom and dow if self.any_day or self.any_weekday else dom or dow
            if not matches:
                continue
            for hour in sorted(self.hours):
                for minute in sorted(self.minutes):
                    local = datetime.combine(day, time(hour, minute), selected).replace(fold=0)
                    candidate = local.astimezone(UTC)
                    # Round-trip rejects spring gaps. Only fold=0 is considered in autumn.
                    if candidate > after and candidate.astimezone(selected).replace(tzinfo=None) == local.replace(
                        tzinfo=None
                    ):
                        return candidate
        fail(422, "CRON_NO_DATE", "未来八年内没有符合此规则的时刻，请检查日期组合")


def next_due(
    kind: str, now: datetime, *, start: datetime, timezone: str, interval_seconds: int | None = None, cron: str = ""
) -> datetime | None:
    if kind == "once":
        return start if start > now else None
    if kind == "interval":
        if interval_seconds is None or not 60 <= interval_seconds <= 366 * 86400:
            fail(422, "INTERVAL_INVALID", "执行间隔须在一分钟到一年之间")
        steps = max(0, int((now - start).total_seconds() // interval_seconds) + 1)
        return start + timedelta(seconds=steps * interval_seconds)
    if kind == "cron":
        return Calendar.parse(cron).next(max(now, start - timedelta(microseconds=1)), timezone)
    fail(422, "SCHEDULE_KIND_INVALID", "请选择一次性、间隔或 Cron 调度")
