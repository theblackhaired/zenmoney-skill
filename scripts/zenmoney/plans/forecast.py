from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from ..errors import InvalidArgumentError


def _date(value: str | datetime.date, field: str) -> datetime.date:
    if isinstance(value, datetime.datetime):
        raise InvalidArgumentError(f"{field} must be a date, not a datetime")
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be an ISO date") from exc


def _dates(start: datetime.date, end: datetime.date):
    current = start
    while current <= end:
        yield current
        current += datetime.timedelta(days=1)


def _is_planned(item: Mapping[str, Any]) -> bool:
    if item.get("deleted") is True or item.get("isDeleted") is True:
        return False
    return item.get("state", item.get("status")) == "planned"


def build_daily_forecast(
    *,
    residue: int | float | Decimal | str,
    planned_operations: Iterable[Mapping[str, Any]],
    start_date: str | datetime.date,
    end_date: str | datetime.date,
    cutoff_date: str | datetime.date,
    show_calendar: bool = False,
) -> dict[str, Any]:
    """Build a deterministic cumulative residue forecast over an inclusive range.

    Days before ``cutoff_date`` have zero forecast. Planned entries count only
    when their explicit state/status is ``planned``; processed and deleted
    entries are ignored. Negative planned amounts are rejected because refund
    allocation is not part of the recovered contract. ``show_calendar`` is
    accepted only to make its presentation-only nature testable.
    """
    del show_calendar
    start = _date(start_date, "start_date")
    end = _date(end_date, "end_date")
    cutoff = _date(cutoff_date, "cutoff_date")
    if end < start:
        raise InvalidArgumentError("end_date must be on or after start_date")

    operation_items = list(planned_operations)
    zero = Decimal(0)
    normalized_residue = _money(residue, "residue")
    normalized_residue = max(zero, normalized_residue)

    forecast_start = max(start, cutoff)
    has_future = forecast_start <= end
    planned_by_date: dict[datetime.date, Decimal] = defaultdict(lambda: zero)
    if has_future:
        for item in operation_items:
            if not _is_planned(item):
                continue
            operation_date = _date(item.get("date"), "planned_operation.date")
            if not forecast_start <= operation_date <= end:
                continue
            amount = _money(item.get("amount"), "planned_operation.amount")
            if amount < 0:
                raise InvalidArgumentError(
                    "Negative planned amounts require an explicit refund policy"
                )
            planned_by_date[operation_date] += amount

    planned_remaining = sum(planned_by_date.values(), zero)
    remaining_days = (end - forecast_start).days + 1 if has_future else 0
    base_daily_amount = (
        max(zero, normalized_residue - planned_remaining) / remaining_days
        if remaining_days
        else zero
    )

    cumulative = zero
    points: list[dict[str, Any]] = []
    for day in _dates(start, end):
        planned_amount = planned_by_date.get(day, zero)
        amount = zero
        if day >= forecast_start and has_future:
            remaining = max(zero, normalized_residue - cumulative)
            candidate = base_daily_amount + planned_amount
            amount = min(candidate, remaining)
            if day == end:
                amount = remaining
            cumulative += amount
        points.append(
            {
                "date": day.isoformat(),
                "base_amount": base_daily_amount if day >= forecast_start and has_future else zero,
                "planned_amount": planned_amount,
                "amount": amount,
                "cumulative": cumulative,
            }
        )

    return {
        "residue": normalized_residue,
        "planned_remaining": planned_remaining,
        "remaining_days": remaining_days,
        "base_daily_amount": base_daily_amount,
        "points": points,
    }


def _money(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number") from exc
    if not amount.is_finite():
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number")
    return amount
