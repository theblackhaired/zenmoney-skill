from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from ..errors import InvalidArgumentError


EXCLUDE_OPENING_BALANCE = "EXCLUDE_OPENING_BALANCE"
INCLUDE_OPENING_BALANCE = "INCLUDE_OPENING_BALANCE"
BALANCE = "BALANCE"
ZERO = Decimal(0)


def _date(value: str | datetime.date, field: str) -> datetime.date:
    if isinstance(value, datetime.datetime):
        raise InvalidArgumentError(f"{field} must be a date, not a datetime")
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be an ISO date") from exc


def _is_deleted(item: Mapping[str, Any]) -> bool:
    return (
        item.get("deleted") is True
        or item.get("isDeleted") is True
        or item.get("state") == "deleted"
        or item.get("status") == "deleted"
    )


def _money(value: Any, field: str, *, non_negative: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidArgumentError(f"{field} must be a finite number")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be a finite number") from exc
    if not amount.is_finite():
        raise InvalidArgumentError(f"{field} must be a finite number")
    if non_negative and amount < 0:
        raise InvalidArgumentError(f"{field} must be a finite non-negative number")
    return amount


def _transaction_amount(transaction: Mapping[str, Any], side: str) -> Decimal:
    if side not in transaction and f"{side}Account" not in transaction:
        return ZERO
    return _money(
        transaction.get(side),
        f"transaction.{side}",
        non_negative=True,
    )


def _validate_in_balance_accounts(accounts: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    account_items = list(accounts)
    for account in account_items:
        if account.get("inBalance") is not True:
            continue
        account_id = account.get("id")
        if account_id is None:
            raise InvalidArgumentError("An inBalance account must have an id")
        if account.get("instrument") is None:
            raise InvalidArgumentError(
                "An inBalance account must have a native instrument",
                {"account_id": account_id},
            )
        if "balance" not in account:
            raise InvalidArgumentError(
                "An inBalance account must have a native balance",
                {"account_id": account_id},
            )
        _money(account.get("balance"), "account.balance")
    return account_items


def reconstruct_native_opening(
    *,
    accounts: Iterable[Mapping[str, Any]],
    transactions: Iterable[Mapping[str, Any]],
    start_date: str | datetime.date,
) -> dict[str, dict[Any, Any]]:
    """Reverse current native balances to the beginning of ``start_date``.

    Archive state is deliberately ignored: ``inBalance`` alone defines the
    account perimeter. Amounts stay in their account's native instrument; this
    function never converts or sums unlike instruments.
    """
    start = _date(start_date, "start_date")
    account_items = _validate_in_balance_accounts(accounts)
    known_accounts: dict[str, Mapping[str, Any]] = {}
    by_account: dict[Any, dict[str, Any]] = {}
    for account in sorted(account_items, key=lambda item: str(item.get("id"))):
        account_id = account.get("id")
        if account_id is not None:
            normalized_id = str(account_id)
            if normalized_id in known_accounts:
                raise InvalidArgumentError(
                    "Account ids must be unique",
                    {"account_id": account_id},
                )
            known_accounts[normalized_id] = account
        if account.get("inBalance") is True:
            by_account[account_id] = {
                "instrument": account["instrument"],
                "amount": _money(account["balance"], "account.balance"),
            }

    for transaction in transactions:
        if _is_deleted(transaction):
            continue
        if _date(transaction.get("date"), "transaction.date") < start:
            continue
        for side in ("income", "outcome"):
            amount = _transaction_amount(transaction, side)
            if not amount:
                continue
            account_id = transaction.get(f"{side}Account")
            account = known_accounts.get(str(account_id)) if account_id is not None else None
            if account is None:
                raise InvalidArgumentError(
                    "A nonzero transaction side references a missing or unknown account",
                    {
                        "transaction_id": transaction.get("id"),
                        "side": side,
                        "account_id": account_id,
                    },
                )
            if account.get("inBalance") is True:
                native_id = account["id"]
                by_account[native_id]["amount"] += -amount if side == "income" else amount

    by_instrument: dict[Any, Decimal] = {}
    for bucket in by_account.values():
        instrument = bucket["instrument"]
        by_instrument[instrument] = by_instrument.get(instrument, ZERO) + bucket["amount"]

    return {"by_account": by_account, "by_instrument": by_instrument}


def _native_balance(value: Any, field: str) -> dict[str, dict[Any, Any]]:
    if not isinstance(value, Mapping):
        raise InvalidArgumentError(f"{field} must be native holdings")
    by_account = value.get("by_account")
    by_instrument = value.get("by_instrument")
    if not isinstance(by_account, Mapping) or not isinstance(by_instrument, Mapping):
        raise InvalidArgumentError(f"{field} must contain by_account and by_instrument")

    normalized_accounts: dict[Any, dict[str, Any]] = {}
    computed_by_instrument: dict[Any, Decimal] = {}
    for account_id, row in by_account.items():
        if (
            not isinstance(row, Mapping)
            or row.get("instrument") is None
            or "amount" not in row
        ):
            raise InvalidArgumentError(f"{field}.by_account rows must contain instrument and amount")
        amount = _money(row["amount"], f"{field}.by_account.amount")
        instrument = row["instrument"]
        normalized_accounts[account_id] = {"instrument": instrument, "amount": amount}
        computed_by_instrument[instrument] = (
            computed_by_instrument.get(instrument, ZERO) + amount
        )

    normalized_instruments = {
        instrument: _money(amount, f"{field}.by_instrument.amount")
        for instrument, amount in by_instrument.items()
    }
    if normalized_instruments != computed_by_instrument:
        raise InvalidArgumentError(f"{field}.by_instrument must match by_account totals")
    return {
        "by_account": normalized_accounts,
        "by_instrument": normalized_instruments,
    }


def _summary_balance(summary: Any) -> dict[str, dict[Any, Any]]:
    if not isinstance(summary, Mapping) or "balance" not in summary:
        raise InvalidArgumentError("previous_day_summary result must contain balance")
    return _native_balance(summary["balance"], "previous_day_summary.balance")


def _future_recursion_policy(mode: str, settings: frozenset[str]) -> tuple[str, frozenset[str]]:
    has_transfer_exclusions = any(
        setting.startswith("EXCLUDE_TRANSFER_") for setting in settings
    )
    if (
        mode == EXCLUDE_OPENING_BALANCE
        and INCLUDE_OPENING_BALANCE in settings
        and has_transfer_exclusions
    ):
        return BALANCE, frozenset()
    return mode, settings


def resolve_opening_balance(
    *,
    accounts: Iterable[Mapping[str, Any]],
    transactions: Iterable[Mapping[str, Any]],
    start_date: str | datetime.date,
    today: str | datetime.date,
    plan_balance_mode: str,
    plan_settings: Iterable[str],
    previous_day_summary: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Resolve an included, excluded, historical, or recursive future opening.

    The callback contract is intentionally narrow: it receives the previous ISO
    date plus the recursion policy and must return a mapping containing native
    ``balance`` holdings. No refund, currency-conversion, or transfer math is
    inferred here; those remain responsibilities of the summary engine.
    """
    start = _date(start_date, "start_date")
    anchor = _date(today, "today")
    settings = frozenset(plan_settings)
    included = (
        plan_balance_mode != EXCLUDE_OPENING_BALANCE
        or INCLUDE_OPENING_BALANCE in settings
    )
    if not included:
        return {
            "included": False,
            "source": "excluded",
            "balance": {"by_account": {}, "by_instrument": {}},
            "recursion_policy": None,
        }
    if start <= anchor:
        return {
            "included": True,
            "source": "reversed_current_balance",
            "balance": reconstruct_native_opening(
                accounts=accounts,
                transactions=transactions,
                start_date=start,
            ),
            "recursion_policy": None,
        }

    if previous_day_summary is None:
        raise InvalidArgumentError("Future opening requires previous_day_summary")
    _validate_in_balance_accounts(accounts)
    recursion_mode, recursion_settings = _future_recursion_policy(
        plan_balance_mode,
        settings,
    )
    previous_day = (start - datetime.timedelta(days=1)).isoformat()
    summary = previous_day_summary(
        previous_day,
        plan_balance_mode=recursion_mode,
        plan_settings=recursion_settings,
    )
    return {
        "included": True,
        "source": "previous_day_summary",
        "balance": _summary_balance(summary),
        "recursion_policy": {
            "plan_balance_mode": recursion_mode,
            "plan_settings": sorted(recursion_settings),
        },
    }
