from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Literal

from ..errors import InvalidArgumentError


CurrencyFilter = Literal["USER", "POPULAR"]
InsightType = Literal["INCREASED", "NOT_CHANGED", "DECREASED", "RESERVE"]

_ZERO = Decimal(0)
_CURRENCY_FILTERS: tuple[CurrencyFilter, ...] = ("USER", "POPULAR")
INSIGHT_TYPES: tuple[InsightType, ...] = (
    "INCREASED",
    "NOT_CHANGED",
    "DECREASED",
    "RESERVE",
)


def render_balance_trend(
    balance_points: list[dict[str, Any]],
    *,
    selected_account_ids: list[str],
    history: bool,
    current_date: str,
    current_balance: int | str | Decimal | None = None,
    currency_filter: CurrencyFilter = "USER",
    currency: Any = None,
) -> dict[str, Any]:
    filter_mode = _currency_filter(currency_filter)
    if not selected_account_ids:
        return _empty("NO_SELECTED_ACCOUNTS", filter_mode, currency)
    if not history:
        return _empty("HISTORY_DISABLED", filter_mode, currency)

    points = [_point(point) for point in balance_points]
    today = _date(current_date, "current_date")
    if current_balance is not None:
        points = _with_current_point(points, today, _money(current_balance, "current_balance"))
    points.sort(key=lambda point: point["date"])

    if not points:
        return _result([], filter_mode, currency, "NOT_CHANGED")

    start_balance = points[0]["balance"]
    balances = [point["balance"] for point in points]
    min_y = min([_ZERO, *balances])
    max_y = max([_ZERO, *balances])
    span = max_y - min_y

    rendered = []
    for point in points:
        balance = point["balance"]
        item = {
            "date": point["date"].isoformat(),
            "balance": balance,
            "diff_from_start": balance - start_balance,
            "normalized_y": _ZERO if span == 0 else (balance - min_y) / span,
        }
        if start_balance > 0:
            item["relative_diff"] = (balance - start_balance) / start_balance
        rendered.append(item)

    return _result(rendered, filter_mode, currency, _insight(start_balance, points[-1]["balance"]), min_y, max_y)


def _with_current_point(
    points: list[dict[str, Any]],
    today: datetime.date,
    current_balance: Decimal,
) -> list[dict[str, Any]]:
    updated = []
    replaced = False
    for point in points:
        if point["date"] == today:
            updated.append({"date": today, "balance": current_balance})
            replaced = True
        else:
            updated.append(point)
    if not replaced:
        updated.append({"date": today, "balance": current_balance})
    return updated


def _point(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": _date(raw.get("date"), "point.date"),
        "balance": _money(raw.get("balance", 0), "point.balance"),
    }


def _result(
    points: list[dict[str, Any]],
    currency_filter: CurrencyFilter,
    currency: Any,
    insight_type: InsightType,
    min_y: Decimal = _ZERO,
    max_y: Decimal = _ZERO,
) -> dict[str, Any]:
    return {
        "points": points,
        "y_axis": {"min": min_y, "max": max_y},
        "metadata": {
            "currency_filter": {"mode": currency_filter, "currency": currency},
            "insight_types": list(INSIGHT_TYPES),
        },
        "insight_type": insight_type,
    }


def _empty(reason: str, currency_filter: CurrencyFilter, currency: Any) -> dict[str, Any]:
    result = _result([], currency_filter, currency, "NOT_CHANGED")
    result["status"] = "empty"
    result["reason"] = reason
    result.pop("insight_type")
    return result


def _insight(start: Decimal, end: Decimal) -> InsightType:
    if end > start:
        return "INCREASED"
    if end < start:
        return "DECREASED"
    return "NOT_CHANGED"


def _currency_filter(value: Any) -> CurrencyFilter:
    if value not in _CURRENCY_FILTERS:
        raise InvalidArgumentError("currency_filter must be USER or POPULAR")
    return value


def _date(value: Any, field: str) -> datetime.date:
    if isinstance(value, datetime.datetime):
        raise InvalidArgumentError(f"{field} must be a date, not a datetime")
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be an ISO date") from exc


def _money(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number") from exc
    if not amount.is_finite():
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number")
    return amount
