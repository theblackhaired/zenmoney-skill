from __future__ import annotations

from decimal import Decimal
from numbers import Integral
from typing import Any, Literal

from ..errors import InvalidArgumentError, UnsupportedCalculationError


ComparisonMode = Literal["WHOLE_PERIOD", "AVERAGE_VALUES"]

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
_MODES: tuple[ComparisonMode, ...] = ("WHOLE_PERIOD", "AVERAGE_VALUES")


def build_income_outcome_comparison(
    periods: list[dict[str, Any]],
    *,
    period_days: int,
    mode: ComparisonMode = "WHOLE_PERIOD",
) -> dict[str, Any]:
    days = _days(period_days)
    selected_mode = _mode(mode)
    expose_mode = days > 31
    effective_mode: ComparisonMode = selected_mode if expose_mode else "WHOLE_PERIOD"

    result: dict[str, Any] = {
        "items": [_period_item(period, effective_mode) for period in periods],
    }
    if expose_mode:
        result["mode"] = effective_mode
        result["available_modes"] = list(_MODES)
    return result


def _period_item(period: dict[str, Any], mode: ComparisonMode) -> dict[str, Any]:
    income = _money(period.get("income", 0), "income")
    outcome = _money(period.get("outcome", 0), "outcome")
    residue = _money(period.get("residue", 0), "residue")
    if mode == "AVERAGE_VALUES":
        if "days" in period:
            raise UnsupportedCalculationError(
                "AVERAGE_VALUES raw period averaging is not implemented; pass already calculated values"
            )

    denominator = max(abs(income), abs(outcome))
    return {
        "key": period.get("key"),
        "title": period.get("title"),
        "income": income,
        "outcome": outcome,
        "residue": residue,
        "percentage": _percentage(income, outcome),
        "chart": {
            "denominator": denominator,
            "income_weight": _weight(income, denominator),
            "outcome_weight": _weight(outcome, denominator),
            "residue_weight": _weight(residue, denominator),
        },
    }


def _percentage(income: Decimal, outcome: Decimal) -> Decimal:
    if income > 0:
        return outcome / income * _HUNDRED
    if outcome > 0:
        return _HUNDRED
    return _ZERO


def _weight(value: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return _ZERO
    return abs(value) / denominator


def _mode(value: Any) -> ComparisonMode:
    if value not in _MODES:
        raise InvalidArgumentError("comparison mode must be WHOLE_PERIOD or AVERAGE_VALUES")
    return value


def _days(value: Any, *, field: str = "period_days") -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise InvalidArgumentError(f"{field} must be a positive integer")
    if value <= 0:
        raise InvalidArgumentError(f"{field} must be a positive integer")
    return Decimal(value)


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
