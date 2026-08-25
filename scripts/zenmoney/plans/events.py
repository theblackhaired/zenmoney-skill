from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import InvalidArgumentError, UnsupportedCalculationError
from .models import AccountSide, EventSource, PlanEvent, decimal_amount


def _source_value(
    source: Mapping[str, Any], fallback: Mapping[str, Any], key: str
) -> Any:
    return source[key] if key in source else fallback.get(key)


def _amount(source: Mapping[str, Any], fallback: Mapping[str, Any], key: str) -> Any:
    value = _source_value(source, fallback, key)
    if value is None:
        value = 0
    return decimal_amount(value, key, non_negative=True)


def _side(
    source: Mapping[str, Any],
    fallback: Mapping[str, Any],
    accounts: Mapping[str, Mapping[str, Any]],
    prefix: str,
    amount: Any,
) -> AccountSide | None:
    account_id = _source_value(source, fallback, f"{prefix}Account")
    if account_id is None:
        if amount:
            raise UnsupportedCalculationError(
                f"A balance-changing event has no {prefix} account",
                {"reason": "missing_account", "side": prefix},
            )
        return None

    account = accounts.get(str(account_id))
    if account is None:
        if amount:
            raise UnsupportedCalculationError(
                f"A balance-changing event references an unknown account: {account_id}",
                {"reason": "unknown_account", "account_id": account_id},
            )
        return AccountSide(
            account_id=str(account_id),
            amount=amount,
            currency=_source_value(source, fallback, f"{prefix}Instrument"),
            in_balance=False,
            archived=False,
            known_account=False,
        )

    currency = _source_value(source, fallback, f"{prefix}Instrument")
    if currency is None:
        currency = account.get("instrument")
    return AccountSide(
        account_id=str(account_id),
        amount=amount,
        currency=currency,
        in_balance=account.get("inBalance") is True,
        archived=account.get("archive") is True,
        known_account=True,
        account_type=account.get("type"),
        account_subtype=account.get("subtype"),
        credit_limit=account.get("creditLimit", 0) or 0,
        savings=account.get("savings") is True,
    )


def _event(
    source_type: EventSource,
    source: Mapping[str, Any],
    fallback: Mapping[str, Any],
    accounts: Mapping[str, Mapping[str, Any]],
) -> PlanEvent:
    source_id = source.get("id")
    date = source.get("date")
    if source_id is None or not date:
        raise InvalidArgumentError(f"{source_type} requires id and date")

    outcome = _amount(source, fallback, "outcome")
    income = _amount(source, fallback, "income")
    if outcome and income:
        kind = "transfer"
    elif outcome:
        kind = "outcome"
    elif income:
        kind = "income"
    else:
        raise InvalidArgumentError(f"{source_type} has no monetary side")

    raw_category_ids = _source_value(source, fallback, "tag")
    if raw_category_ids is None:
        category_ids: tuple[str, ...] = ()
    elif isinstance(raw_category_ids, (list, tuple)) and all(
        isinstance(category_id, str) for category_id in raw_category_ids
    ):
        category_ids = tuple(raw_category_ids)
    else:
        raise InvalidArgumentError(f"{source_type} tag must be an array of strings")

    marker_state = None
    if source_type == "reminder_marker":
        marker_state = source.get("state")
        if not isinstance(marker_state, str) or not marker_state:
            raise InvalidArgumentError("reminder_marker requires an explicit state")

    return PlanEvent(
        source_id=str(source_id),
        source_type=source_type,
        date=str(date),
        kind=kind,
        outcome_side=_side(source, fallback, accounts, "outcome", outcome),
        income_side=_side(source, fallback, accounts, "income", income),
        category_ids=category_ids,
        marker_state=marker_state,
        is_forecast=source.get("isForecast") is True,
    )


def event_from_transaction(
    transaction: Mapping[str, Any],
    accounts: Mapping[str, Mapping[str, Any]],
) -> PlanEvent:
    return _event("transaction", transaction, {}, accounts)


def event_from_reminder_marker(
    reminder: Mapping[str, Any],
    marker: Mapping[str, Any],
    accounts: Mapping[str, Mapping[str, Any]],
) -> PlanEvent:
    return _event("reminder_marker", marker, reminder, accounts)
