from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import ToolError


CURRENT_RATE_METADATA = {
    "policy": "current_synced_instrument_rate",
    "source": "v8_diff.instrument.rate",
    "historical_exchange_difference": "not_measurable_without_rate_history",
}


def current_rate_converter(
    instruments: Iterable[Mapping[str, Any]],
) -> Callable[[Decimal, Any, Any, str], Decimal]:
    """Convert with current Instrument.rate values synced by the public diff API."""
    by_id: dict[str, dict[str, Any]] = {}
    for raw in instruments:
        if not isinstance(raw, Mapping):
            raise _rate_error("instrument must be an object")
        row = dict(raw)
        by_id[_instrument_id(row.get("id"), field="instrument.id")] = row

    def convert(
        amount: Decimal,
        source_instrument: Any,
        target_instrument: Any,
        _on_date: str,
    ) -> Decimal:
        decimal_amount = _decimal(amount, label="amount")
        source_id = _instrument_id(source_instrument, field="sourceInstrument")
        target_id = _instrument_id(target_instrument, field="targetInstrument")
        if source_id == target_id:
            return decimal_amount

        source = by_id.get(source_id)
        target = by_id.get(target_id)
        if source is None or target is None:
            missing_id = source_id if source is None else target_id
            raise _rate_error(
                f"instrument {missing_id} is missing from the synced instruments"
            )
        source_rate = _positive_rate(
            source.get("rate"), label="current source instrument"
        )
        target_rate = _positive_rate(
            target.get("rate"), label="current target instrument"
        )
        return decimal_amount * source_rate / target_rate

    return convert


def _instrument_id(value: Any, *, field: str) -> str:
    if isinstance(value, bool):
        raise _rate_error(f"{field} must be a positive integer instrument ID")
    text = str(value).strip() if value is not None else ""
    if not text.isascii() or not text.isdigit() or int(text) <= 0:
        raise _rate_error(f"{field} must be a positive integer instrument ID")
    return str(int(text))


def _positive_rate(value: Any, *, label: str) -> Decimal:
    rate = _decimal(value, label=label)
    if not rate.is_finite() or rate <= 0:
        raise _rate_error(f"{label} rate must be finite and greater than zero")
    return rate


def _decimal(value: Any, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise _rate_error(f"{label} must be numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise _rate_error(f"{label} must be numeric") from exc


def _rate_error(message: str) -> ToolError:
    return ToolError("INVALID_INSTRUMENT_RATE", message)
