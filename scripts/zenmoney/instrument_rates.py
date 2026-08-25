from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from . import transport
from .errors import ToolError


class InstrumentRateCache:
    """In-memory historical rates keyed by base, quote, and calendar date."""

    def __init__(self) -> None:
        self._rates: dict[tuple[str, str, str], Decimal] = {}

    def add_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = [_normalize_row(row) for row in rows]
        for row in normalized:
            key = (
                str(row["baseInstrument"]),
                str(row["quoteInstrument"]),
                row["date"],
            )
            self._rates[key] = row["rate"]
        return normalized

    def get(
        self,
        base_instrument: int | str,
        quote_instrument: int | str,
        on_date: str,
    ) -> Decimal | None:
        key = (
            _instrument_id(base_instrument, field="baseInstrument"),
            _instrument_id(quote_instrument, field="quoteInstrument"),
            _iso_date(on_date, field="date"),
        )
        return self._rates.get(key)


def instrument_rate_predicate(
    base_instrument: int | str,
    quote_instrument: int | str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, str]:
    predicate = {
        "baseInstrument": _instrument_id(base_instrument, field="baseInstrument"),
        "quoteInstrument": _instrument_id(quote_instrument, field="quoteInstrument"),
    }
    if from_date is not None:
        predicate["fromDate"] = _iso_date(from_date, field="fromDate")
    if to_date is not None:
        predicate["toDate"] = _iso_date(to_date, field="toDate")
    return predicate


async def fetch_instrument_rates(
    predicates: Iterable[dict[str, Any]],
    *,
    cache: InstrumentRateCache,
) -> list[dict[str, Any]]:
    request_predicates = [_normalize_predicate(predicate) for predicate in predicates]
    if not request_predicates:
        return []
    response = await transport._api_post(
        "/instrument-rates/",
        {"predicates": request_predicates},
    )
    if not isinstance(response, list):
        raise _rate_error("Instrument rates response must be a JSON array")
    return cache.add_rows(response)


def conversion_rate_on_date(
    *,
    source: dict[str, Any],
    target: dict[str, Any],
    main: dict[str, Any],
    on_date: str,
    cache: InstrumentRateCache,
) -> Decimal:
    source_id = _instrument_row_id(source, label="source")
    target_id = _instrument_row_id(target, label="target")
    if source_id == target_id:
        return Decimal(1)

    main_id = _instrument_row_id(main, label="main")
    normalized_date = _iso_date(on_date, field="date")
    source_rate = _historical_leg(cache, source_id, main_id, normalized_date)
    if source_rate is None:
        source_rate = _positive_rate(
            source.get("rate"), label="current source instrument"
        )
    target_rate = _historical_leg(cache, target_id, main_id, normalized_date)
    if target_rate is None:
        target_rate = _positive_rate(
            target.get("rate"), label="current target instrument"
        )
    return source_rate / target_rate


def convert_on_date(
    amount: Decimal | int | float | str,
    *,
    source: dict[str, Any],
    target: dict[str, Any],
    main: dict[str, Any],
    on_date: str,
    cache: InstrumentRateCache,
) -> Decimal:
    decimal_amount = _decimal(amount, label="amount")
    return decimal_amount * conversion_rate_on_date(
        source=source,
        target=target,
        main=main,
        on_date=on_date,
        cache=cache,
    )


def exchange_converter(
    *,
    instruments: Iterable[Mapping[str, Any]],
    main_instrument_id: int | str,
    cache: InstrumentRateCache,
) -> Callable[[Decimal, Any, Any, str], Decimal]:
    """Bridge Plans exchange rows to the historical-rate conversion contract."""
    by_id: dict[str, dict[str, Any]] = {}
    for raw in instruments:
        if not isinstance(raw, Mapping):
            raise _rate_error("instrument must be an object")
        row = dict(raw)
        instrument_id = _instrument_row_id(row, label="instrument")
        by_id[instrument_id] = row

    main_id = _instrument_id(main_instrument_id, field="mainInstrument")
    main = by_id.get(main_id)
    if main is None:
        raise _rate_error("main instrument is missing from the synced instruments")

    def convert(
        amount: Decimal,
        source_instrument: Any,
        target_instrument: Any,
        on_date: str,
    ) -> Decimal:
        source_id = _instrument_id(source_instrument, field="sourceInstrument")
        target_id = _instrument_id(target_instrument, field="targetInstrument")
        source = by_id.get(source_id)
        target = by_id.get(target_id)
        if source is None or target is None:
            missing_id = source_id if source is None else target_id
            raise _rate_error(
                f"instrument {missing_id} is missing from the synced instruments"
            )
        return convert_on_date(
            amount,
            source=source,
            target=target,
            main=main,
            on_date=on_date,
            cache=cache,
        )

    return convert


def _historical_leg(
    cache: InstrumentRateCache,
    instrument_id: str,
    main_id: str,
    on_date: str,
) -> Decimal | None:
    if instrument_id == main_id:
        return Decimal(1)
    return cache.get(instrument_id, main_id, on_date)


def _normalize_predicate(predicate: dict[str, Any]) -> dict[str, str]:
    if not isinstance(predicate, dict):
        raise _rate_error("Instrument rate predicate must be an object")
    return instrument_rate_predicate(
        predicate.get("baseInstrument"),
        predicate.get("quoteInstrument"),
        from_date=predicate.get("fromDate"),
        to_date=predicate.get("toDate"),
    )


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise _rate_error("Instrument rate row must be an object")
    base_id = _instrument_id(row.get("baseInstrument"), field="baseInstrument")
    quote_id = _instrument_id(row.get("quoteInstrument"), field="quoteInstrument")
    return {
        "baseInstrument": int(base_id),
        "quoteInstrument": int(quote_id),
        "date": _iso_date(row.get("date"), field="date"),
        "rate": _positive_rate(row.get("rate"), label="historical instrument rate"),
    }


def _instrument_row_id(instrument: dict[str, Any], *, label: str) -> str:
    if not isinstance(instrument, dict):
        raise _rate_error(f"{label} instrument must be an object")
    return _instrument_id(instrument.get("id"), field=f"{label}.id")


def _instrument_id(value: Any, *, field: str) -> str:
    if isinstance(value, bool):
        raise _rate_error(f"{field} must be a positive integer instrument ID")
    text = str(value).strip() if value is not None else ""
    if not text.isascii() or not text.isdigit() or int(text) <= 0:
        raise _rate_error(f"{field} must be a positive integer instrument ID")
    return str(int(text))


def _iso_date(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise _rate_error(f"{field} must use yyyy-MM-dd")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _rate_error(f"{field} must use yyyy-MM-dd") from exc
    normalized = parsed.isoformat()
    if normalized != value:
        raise _rate_error(f"{field} must use yyyy-MM-dd")
    return normalized


def _positive_rate(value: Any, *, label: str) -> Decimal:
    rate = _decimal(value, label=label)
    if rate <= 0:
        raise _rate_error(f"{label} must be greater than zero")
    return rate


def _decimal(value: Any, *, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise _rate_error(f"{label} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise _rate_error(f"{label} must be a finite number") from exc
    if not result.is_finite():
        raise _rate_error(f"{label} must be a finite number")
    return result


def _rate_error(message: str) -> ToolError:
    return ToolError("INVALID_INSTRUMENT_RATE", message)
