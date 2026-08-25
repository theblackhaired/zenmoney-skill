from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from ..errors import ToolError


EXCHANGE_DIFF = "EXCHANGE_DIFF"
_ZERO = Decimal(0)


def calculate_exchange_difference(
    *,
    opening_holdings: Iterable[Mapping[str, Any]],
    target_holdings: Iterable[Mapping[str, Any]],
    excluded_transfer_income: Iterable[Mapping[str, Any]],
    excluded_transfer_expense: Iterable[Mapping[str, Any]],
    income_facts: Iterable[Mapping[str, Any]],
    expense_facts: Iterable[Mapping[str, Any]],
    target_instrument: Any,
    period_end_date: str,
    exclude_opening_balance: bool,
    convert: Callable[[Decimal, Any, Any, str], Decimal],
) -> dict[str, Any]:
    """Calculate the ZenMoney Plans exchange-difference pseudo-row."""
    _instrument(target_instrument, "target_instrument")
    target_currency = target_instrument
    end_date = _date(period_end_date, "period_end_date")
    if type(exclude_opening_balance) is not bool:
        raise _input_error("exclude_opening_balance must be a boolean")
    if not callable(convert):
        raise _input_error("convert must be callable")

    opening = _holdings(opening_holdings, "opening_holdings")
    target = _holdings(target_holdings, "target_holdings")

    if exclude_opening_balance:
        missing_targets = opening.keys() - target.keys()
        if missing_targets:
            raise _input_error(
                "target_holdings must include a dated balance for every opening instrument"
            )
        fact_opening: dict[str, dict[str, Any]] = {}
        fact_target = {
            key: {
                **row,
                "balance": row["balance"] - opening.get(key, {}).get("balance", _ZERO),
            }
            for key, row in target.items()
        }
    else:
        fact_opening = opening
        fact_target = target

    opening_balance = _value_holdings(fact_opening, target_currency, convert)
    target_balance = _value_holdings(fact_target, target_currency, convert)
    excluded_income = _value_flows(
        excluded_transfer_income,
        "excluded_transfer_income",
        target_currency,
        convert,
    )
    excluded_expense = _value_flows(
        excluded_transfer_expense,
        "excluded_transfer_expense",
        target_currency,
        convert,
    )
    income = _value_flows(income_facts, "income_facts", target_currency, convert)
    expense = _value_flows(expense_facts, "expense_facts", target_currency, convert)

    period_end_opening = _value_holdings(
        fact_opening,
        target_currency,
        convert,
        on_date=end_date,
    )
    period_end_target = _value_holdings(
        fact_target,
        target_currency,
        convert,
        on_date=end_date,
    )
    fact = (
        target_balance
        - opening_balance
        - excluded_income
        + excluded_expense
        - income
        + expense
    )

    return {
        "id": EXCHANGE_DIFF,
        "fact": fact,
        "factExtra": _ZERO,
        "budget": period_end_opening - opening_balance,
        "residue": period_end_target - target_balance,
        "planned": _ZERO,
        "expired": _ZERO,
        "processed": _ZERO,
        "components": {
            "targetBalance": target_balance,
            "openingBalance": opening_balance,
            "excludedTransferIncome": excluded_income,
            "excludedTransferExpense": excluded_expense,
            "incomeFacts": income,
            "expenseFacts": expense,
            "periodEndOpeningBalance": period_end_opening,
            "periodEndTargetBalance": period_end_target,
        },
    }


def _holdings(
    rows: Iterable[Mapping[str, Any]],
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_rows(rows, label)):
        instrument = _required(raw, "instrument", label, index)
        key = _instrument(instrument, f"{label}[{index}].instrument")
        balance = _number(
            _required(raw, "balance", label, index),
            f"{label}[{index}].balance",
        )
        on_date = _date(
            _required(raw, "date", label, index),
            f"{label}[{index}].date",
        )
        existing = result.get(key)
        if existing is not None:
            if existing["date"] != on_date:
                raise _input_error(
                    f"{label} rows for instrument {key} must use one snapshot date"
                )
            existing["balance"] += balance
        else:
            result[key] = {
                "instrument": instrument,
                "balance": balance,
                "date": on_date,
            }
    return result


def _value_holdings(
    holdings: Mapping[str, Mapping[str, Any]],
    target_instrument: Any,
    convert: Callable[[Decimal, Any, Any, str], Decimal],
    *,
    on_date: str | None = None,
) -> Decimal:
    return sum(
        (
            _converted(
                row["balance"],
                row["instrument"],
                target_instrument,
                on_date or row["date"],
                convert,
            )
            for row in holdings.values()
        ),
        _ZERO,
    )


def _value_flows(
    rows: Iterable[Mapping[str, Any]],
    label: str,
    target_instrument: Any,
    convert: Callable[[Decimal, Any, Any, str], Decimal],
) -> Decimal:
    total = _ZERO
    for index, raw in enumerate(_rows(rows, label)):
        instrument = _required(raw, "instrument", label, index)
        _instrument(instrument, f"{label}[{index}].instrument")
        amount = _number(
            _required(raw, "amount", label, index),
            f"{label}[{index}].amount",
        )
        if amount < 0:
            raise _input_error(f"{label}[{index}].amount must be non-negative")
        on_date = _date(
            _required(raw, "date", label, index),
            f"{label}[{index}].date",
        )
        total += _converted(amount, instrument, target_instrument, on_date, convert)
    return total


def _converted(
    amount: Decimal,
    source_instrument: Any,
    target_instrument: Any,
    on_date: str,
    convert: Callable[[Decimal, Any, Any, str], Decimal],
) -> Decimal:
    return _number(
        convert(amount, source_instrument, target_instrument, on_date),
        "convert result",
    )


def _rows(
    rows: Iterable[Mapping[str, Any]],
    label: str,
) -> Iterable[Mapping[str, Any]]:
    if rows is None or isinstance(rows, (str, bytes, Mapping)):
        raise _input_error(f"{label} must be an iterable of objects")
    try:
        iterator = iter(rows)
    except TypeError as exc:
        raise _input_error(f"{label} must be an iterable of objects") from exc
    for index, row in enumerate(iterator):
        if not isinstance(row, Mapping):
            raise _input_error(f"{label}[{index}] must be an object")
        yield row


def _required(row: Mapping[str, Any], field: str, label: str, index: int) -> Any:
    if field not in row or row[field] is None:
        raise _input_error(f"{label}[{index}].{field} is required")
    return row[field]


def _instrument(value: Any, field: str) -> str:
    if isinstance(value, bool) or value is None or not str(value).strip():
        raise _input_error(f"{field} is required")
    return str(value)


def _date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise _input_error(f"{field} must use yyyy-MM-dd")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _input_error(f"{field} must use yyyy-MM-dd") from exc
    if parsed.isoformat() != value:
        raise _input_error(f"{field} must use yyyy-MM-dd")
    return value


def _number(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise _input_error(f"{field} must be a finite number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise _input_error(f"{field} must be a finite number") from exc
    if not number.is_finite():
        raise _input_error(f"{field} must be a finite number")
    return number


def _input_error(message: str) -> ToolError:
    return ToolError("INVALID_EXCHANGE_DIFFERENCE_INPUT", message)
