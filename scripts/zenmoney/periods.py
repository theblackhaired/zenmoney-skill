from __future__ import annotations

import calendar
import datetime
from typing import Any


NAMED_PERIODS = {"billing_period", "week", "month", "year"}


class InvalidPeriodSelectorError(ValueError):
    """Raised when a request does not select exactly one period form."""


class InvalidPeriodValueError(ValueError):
    """Raised when a selected period contains an invalid value."""


def _as_date(value: str | datetime.date, field: str) -> datetime.date:
    if isinstance(value, datetime.datetime) or not isinstance(value, (str, datetime.date)):
        raise InvalidPeriodValueError(f"{field} must be an ISO date")
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidPeriodValueError(f"{field} must be an ISO date") from exc


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    ordinal = year * 12 + month - 1 + offset
    shifted_year, shifted_month = divmod(ordinal, 12)
    return shifted_year, shifted_month + 1


def _billing_boundary(year: int, month: int, start_day: int) -> datetime.date:
    days_in_month = calendar.monthrange(year, month)[1]
    if start_day <= days_in_month:
        return datetime.date(year, month, start_day)
    next_year, next_month = _shift_month(year, month, 1)
    return datetime.date(next_year, next_month, 1)


def _logical_billing_month(value: datetime.date, start_day: int) -> tuple[int, int]:
    if value.day >= start_day:
        return value.year, value.month
    return _shift_month(value.year, value.month, -1)


def _result(
    period: str,
    period_offset: int,
    start: datetime.date,
    end_exclusive: datetime.date,
    **metadata: Any,
) -> dict[str, Any]:
    if end_exclusive <= start:
        raise InvalidPeriodValueError("period end must be after period start")
    result: dict[str, Any] = {
        "period": period,
        "period_offset": period_offset,
        "end_exclusive": end_exclusive.isoformat(),
        "start_date": start.isoformat(),
        "end_date": (end_exclusive - datetime.timedelta(days=1)).isoformat(),
    }
    result.update(metadata)
    return result


def resolve_period(
    args: dict[str, Any],
    *,
    today: str | datetime.date,
    billing_start_day: int = 1,
    first_weekday: int | None = None,
) -> dict[str, Any]:
    """Resolve a strict period request to half-open and public inclusive bounds."""
    has_named = "period" in args
    has_start = "start_date" in args
    has_end = "end_date" in args

    if has_named and (has_start or has_end):
        raise InvalidPeriodSelectorError("period cannot be combined with start_date or end_date")
    if not has_named and not has_start and not has_end:
        raise InvalidPeriodSelectorError("select a named period or provide start_date and end_date")
    if not has_named and has_start != has_end:
        raise InvalidPeriodSelectorError("start_date and end_date must be provided together")

    anchor = _as_date(today, "today")
    if not has_named:
        if "period_offset" in args:
            raise InvalidPeriodSelectorError("period_offset is valid only with a named period")
        start = _as_date(args["start_date"], "start_date")
        end = _as_date(args["end_date"], "end_date")
        if end < start:
            raise InvalidPeriodValueError("start_date must be on or before end_date")
        return _result("custom", 0, start, end + datetime.timedelta(days=1))

    period = args["period"]
    if period not in NAMED_PERIODS:
        raise InvalidPeriodValueError(
            f"period must be one of: {', '.join(sorted(NAMED_PERIODS))}"
        )
    period_offset = args.get("period_offset", 0)
    if type(period_offset) is not int:
        raise InvalidPeriodValueError("period_offset must be an integer")

    if period == "billing_period":
        if type(billing_start_day) is not int or not 1 <= billing_start_day <= 31:
            raise InvalidPeriodValueError("billing_start_day must be an integer from 1 to 31")
        logical_year, logical_month = _logical_billing_month(anchor, billing_start_day)
        logical_year, logical_month = _shift_month(
            logical_year,
            logical_month,
            period_offset,
        )
        next_year, next_month = _shift_month(logical_year, logical_month, 1)
        return _result(
            period,
            period_offset,
            _billing_boundary(logical_year, logical_month, billing_start_day),
            _billing_boundary(next_year, next_month, billing_start_day),
            billing_start_day=billing_start_day,
            budget_month_anchor=f"{logical_year:04d}-{logical_month:02d}-01",
        )

    if period == "week":
        if type(first_weekday) is not int or not 0 <= first_weekday <= 6:
            raise InvalidPeriodValueError("first_weekday must be an explicit integer from 0 to 6")
        start = anchor - datetime.timedelta(days=(anchor.weekday() - first_weekday) % 7)
        start += datetime.timedelta(weeks=period_offset)
        return _result(
            period,
            period_offset,
            start,
            start + datetime.timedelta(days=7),
            first_weekday=first_weekday,
        )

    if period == "month":
        year, month = _shift_month(anchor.year, anchor.month, period_offset)
        next_year, next_month = _shift_month(year, month, 1)
        return _result(
            period,
            period_offset,
            datetime.date(year, month, 1),
            datetime.date(next_year, next_month, 1),
        )

    year = anchor.year + period_offset
    return _result(
        period,
        period_offset,
        datetime.date(year, 1, 1),
        datetime.date(year + 1, 1, 1),
    )


def public_period(resolved: dict[str, Any]) -> dict[str, Any]:
    result = {
        "period": resolved["period"],
        "offset": resolved["period_offset"],
        "start_date": resolved["start_date"],
        "end_date": resolved["end_date"],
        "end_exclusive": resolved["end_exclusive"],
    }
    for key in ("billing_start_day", "first_weekday"):
        if key in resolved:
            result[key] = resolved[key]
    return result
