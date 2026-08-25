from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from ..errors import InvalidArgumentError
from ..transfer_classifier import classify_transfer_event


Bucket = Literal[
    "INCOMES",
    "INCOME_TRANSFERS",
    "LOANS",
    "EXPENSES",
    "LOAN_PAYMENTS",
    "DEBTS",
    "DEPOSITS",
    "OUTCOME_TRANSFERS",
    "DIFF",
]
Result = Literal["RESIDUE", "OVERSPENDING", "NO_DATA"]
Side = Literal["income", "outcome"]

BUCKETS: tuple[Bucket, ...] = (
    "INCOMES",
    "INCOME_TRANSFERS",
    "LOANS",
    "EXPENSES",
    "LOAN_PAYMENTS",
    "DEBTS",
    "DEPOSITS",
    "OUTCOME_TRANSFERS",
    "DIFF",
)

_ZERO = Decimal(0)
_MIN_POSITIVE_WEIGHT = Decimal("0.01")


def build_money_flow(
    transactions: Iterable[Mapping[str, Any]],
    *,
    accounts: Mapping[str, Mapping[str, Any]],
    instruments: Mapping[Any, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    totals: dict[str, dict[Side, dict[Bucket, Decimal]]] = defaultdict(_currency_totals)

    for tx in transactions:
        if tx.get("deleted") is True:
            continue
        income = _amount(tx.get("income", 0), "transaction.income")
        outcome = _amount(tx.get("outcome", 0), "transaction.outcome")
        if income == 0 and outcome == 0:
            continue
        if income and outcome:
            _add_transfer(totals, tx, accounts, instruments or {})
        elif income:
            currency = _currency(tx, "income", accounts, instruments or {})
            totals[currency]["income"]["INCOMES"] += income
        else:
            currency = _currency(tx, "outcome", accounts, instruments or {})
            totals[currency]["outcome"]["EXPENSES"] += outcome

    return {"currencies": {currency: _render_currency(rows) for currency, rows in sorted(totals.items())}}


def _currency_totals() -> dict[Side, dict[Bucket, Decimal]]:
    return {
        "income": {bucket: _ZERO for bucket in BUCKETS},
        "outcome": {bucket: _ZERO for bucket in BUCKETS},
    }


def _add_transfer(
    totals: dict[str, dict[Side, dict[Bucket, Decimal]]],
    tx: Mapping[str, Any],
    accounts: Mapping[str, Mapping[str, Any]],
    instruments: Mapping[Any, Mapping[str, Any]],
) -> None:
    event = classify_transfer_event(
        {
            "id": tx.get("id"),
            "date": tx.get("date"),
            "status": tx.get("state") or tx.get("status") or "completed",
            "comment": tx.get("comment"),
            "outcome_side": _transfer_side(tx, "outcome", accounts, instruments),
            "income_side": _transfer_side(tx, "income", accounts, instruments),
        }
    )
    direction = event["direction"]
    if direction == "balance_to_balance":
        return
    if direction == "off_balance_to_off_balance":
        return

    if direction == "off_balance_to_balance":
        bucket = _income_transfer_bucket(event["outcome_axis"])
        income_currency = event["income_side"]["currency"]
        income = _amount(tx.get("income", 0), "transaction.income")
        totals[income_currency]["income"][bucket] += income
        return

    bucket = _outcome_transfer_bucket(event["income_axis"])
    outcome_currency = event["outcome_side"]["currency"]
    outcome = _amount(tx.get("outcome", 0), "transaction.outcome")
    totals[outcome_currency]["outcome"][bucket] += outcome


def _transfer_side(
    tx: Mapping[str, Any],
    side: Side,
    accounts: Mapping[str, Mapping[str, Any]],
    instruments: Mapping[Any, Mapping[str, Any]],
) -> dict[str, Any]:
    account_id = tx.get(f"{side}Account")
    account = accounts.get(str(account_id)) if account_id is not None else None
    return {
        "account_id": account_id,
        "known_account": account is not None,
        "account_type": account.get("type") if account else None,
        "account_subtype": _subtype(account) if account else None,
        "credit_limit": account.get("creditLimit", 0) if account else 0,
        "savings": account.get("savings") is True if account else False,
        "in_balance": account.get("inBalance") is True if account else False,
        "amount": _amount(tx.get(side, 0), f"transaction.{side}"),
        "currency": _currency(tx, side, accounts, instruments),
    }


def _subtype(account: Mapping[str, Any] | None) -> str | None:
    if not account:
        return None
    account_type = account.get("type")
    if account_type == "ccard" and (account.get("creditLimit", 0) or 0) > 0:
        return "credit"
    if account_type == "checking" and account.get("savings") is True:
        return "savings"
    return account.get("subtype")


def _income_transfer_bucket(axis: str) -> Bucket:
    if axis == "loans":
        return "LOANS"
    if axis == "debts":
        return "DEBTS"
    return "INCOME_TRANSFERS"


def _outcome_transfer_bucket(axis: str) -> Bucket:
    if axis == "loans":
        return "LOAN_PAYMENTS"
    if axis == "savings":
        return "DEPOSITS"
    if axis == "debts":
        return "DEBTS"
    return "OUTCOME_TRANSFERS"


def _render_currency(rows: dict[Side, dict[Bucket, Decimal]]) -> dict[str, Any]:
    income_total = sum(rows["income"].values(), _ZERO)
    outcome_total = sum(rows["outcome"].values(), _ZERO)
    denominator = max(income_total, outcome_total)
    result = _result(income_total, outcome_total)
    result_amount = abs(income_total - outcome_total) if result != "NO_DATA" else _ZERO
    rendered_rows = {
        "income": dict(rows["income"]),
        "outcome": dict(rows["outcome"]),
    }
    if result == "RESIDUE":
        rendered_rows["outcome"]["DIFF"] += result_amount
    elif result == "OVERSPENDING":
        rendered_rows["income"]["DIFF"] += result_amount
    return {
        "result": result,
        "result_amount": result_amount,
        "income_total": income_total,
        "outcome_total": outcome_total,
        "denominator": denominator,
        "income": _render_side(rendered_rows["income"], denominator),
        "outcome": _render_side(rendered_rows["outcome"], denominator),
    }


def _render_side(rows: dict[Bucket, Decimal], denominator: Decimal) -> list[dict[str, Any]]:
    cumulative = _ZERO
    rendered = []
    nonzero = [(bucket, amount) for bucket, amount in rows.items() if amount > 0]
    for index, (bucket, amount) in enumerate(nonzero):
        if denominator == 0:
            weight = _ZERO
        else:
            weight = amount / denominator
            if _ZERO < weight < _MIN_POSITIVE_WEIGHT:
                weight = _MIN_POSITIVE_WEIGHT
        weight = min(weight, Decimal(1) - cumulative)
        cumulative += weight
        rendered.append({"bucket": bucket, "amount": amount, "weight": weight})
    return rendered


def _result(income_total: Decimal, outcome_total: Decimal) -> Result:
    if income_total == 0 and outcome_total == 0:
        return "NO_DATA"
    if income_total >= outcome_total:
        return "RESIDUE"
    return "OVERSPENDING"


def _currency(
    tx: Mapping[str, Any],
    side: Side,
    accounts: Mapping[str, Mapping[str, Any]],
    instruments: Mapping[Any, Mapping[str, Any]],
) -> str:
    instrument_id = tx.get(f"{side}Instrument")
    instrument = instruments.get(str(instrument_id)) or instruments.get(instrument_id)
    if instrument is None:
        account_id = tx.get(f"{side}Account")
        account = accounts.get(str(account_id)) if account_id is not None else None
        if account is not None:
            instrument_id = account.get("instrument")
            instrument = instruments.get(str(instrument_id)) or instruments.get(instrument_id)
    currency = instrument.get("shortTitle") if instrument else None
    if not currency:
        raise InvalidArgumentError("money flow requires a known transaction currency")
    return str(currency)


def _amount(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number") from exc
    if not amount.is_finite() or amount < 0:
        raise InvalidArgumentError(f"{field} must be a finite non-negative number")
    return amount
