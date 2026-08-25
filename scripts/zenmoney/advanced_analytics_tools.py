from __future__ import annotations

import datetime
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from . import cache as _cache
from . import config, periods
from .analytics.balance_trend import render_balance_trend
from .analytics.category_difference import apply_category_difference
from .analytics.category_report import render_category_report
from .analytics.comparison import build_income_outcome_comparison
from .analytics.money_flow import build_money_flow
from .domain import _today
from .errors import InvalidArgumentError, UnsupportedCalculationError
from .instrument_rates import (
    InstrumentRateCache,
    exchange_converter,
    fetch_instrument_rates,
    instrument_rate_predicate,
)
from .plans.opening import reconstruct_native_opening
from .transfer_classifier import select_plan_user
from .validation import validate_tool_args


ZERO = Decimal(0)
UNCATEGORIZED = "__uncategorized__"
UNKNOWN_PAYEE = "Без места"


async def tool_get_category_report(args: dict) -> str:
    args = validate_tool_args("get_category_report", args)
    bounds = _comparison_bounds(args, args["comparison_periods"])
    accounts = _account_perimeter(args)
    convert, target = await _rate_context(bounds, accounts, args.get("currency"))
    difference_mode = args.get("difference_calculation_mode") or config._load_config().get(
        "difference_calculation_mode", "REFUNDS"
    )
    rows = _difference_rows(
        bounds[0],
        args["direction"],
        args["group_by"],
        accounts,
        convert,
        target,
        difference_mode,
    )
    comparison_rows = [
        _grouped_amounts(
            _difference_rows(
                bound,
                args["direction"],
                args["group_by"],
                accounts,
                convert,
                target,
                difference_mode,
            ),
            args["group_by"],
        )
        for bound in bounds[1:]
    ]
    categories = [dict(tag) for tag in _cache.CACHE.tags()]
    if args["group_by"] == "TAG":
        categories.append({"id": UNCATEGORIZED, "title": "Без категории", "parent": None})
    report = render_category_report(
        rows,
        categories=categories,
        budgets=_budget_amounts(args["resolved_period"], args["direction"]),
        comparison_periods=comparison_rows,
        group_by=args["group_by"],
        budget_method=args["budget_method"],
    )
    result = {
        "period": periods.public_period(args["resolved_period"]),
        "direction": args["direction"],
        "currency": _currency_code(target),
        "difference_calculation_mode": difference_mode,
        "comparison_periods": [periods.public_period(bound) for bound in bounds[1:]],
        **report,
    }
    return json.dumps(_jsonable(result), ensure_ascii=False)


async def tool_get_money_flow(args: dict) -> str:
    args = validate_tool_args("get_money_flow", args)
    accounts = _account_perimeter(args)
    transactions = _flow_transactions(args["resolved_period"], accounts)
    result = build_money_flow(
        [_decimalized_transaction(tx) for tx in transactions],
        accounts=accounts,
        instruments={str(item["id"]): item for item in _cache.CACHE.instruments()},
    )
    result = {
        "period": periods.public_period(args["resolved_period"]),
        "account_scope": args["account_scope"],
        **result,
    }
    return json.dumps(_jsonable(result), ensure_ascii=False)


async def tool_get_income_outcome_comparison(args: dict) -> str:
    args = validate_tool_args("get_income_outcome_comparison", args)
    bounds = _comparison_bounds(args, args["comparison_periods"])
    period_days = _period_days(bounds[0])
    if args["mode"] == "AVERAGE_VALUES" and period_days > 31:
        raise UnsupportedCalculationError(
            "AVERAGE_VALUES is visible in ZenMoney, but its raw averaging formula is not confirmed"
        )
    accounts = _account_perimeter(args)
    convert, target = await _rate_context(bounds, accounts, args.get("currency"))
    items = []
    for bound in bounds:
        income, outcome = _income_outcome_totals(bound, accounts, convert, target)
        items.append(
            {
                "key": bound["start_date"],
                "title": f"{bound['start_date']} — {bound['end_date']}",
                "income": income,
                "outcome": outcome,
                "residue": income - outcome,
            }
        )
    report = build_income_outcome_comparison(
        items,
        period_days=period_days,
        mode=args["mode"],
    )
    result = {
        "period": periods.public_period(args["resolved_period"]),
        "currency": _currency_code(target),
        "account_scope": args["account_scope"],
        **report,
    }
    return json.dumps(_jsonable(result), ensure_ascii=False)


async def tool_get_balance_trend(args: dict) -> str:
    args = validate_tool_args("get_balance_trend", args)
    bound = args["resolved_period"]
    accounts = _account_perimeter(args)
    convert, target = await _rate_context([bound], accounts, args.get("currency"))
    transactions = [tx for tx in _cache.CACHE.transactions() if not _is_deleted(tx)]
    relevant = _transactions_between(bound)
    point_dates = {
        datetime.date.fromisoformat(bound["start_date"]),
        datetime.date.fromisoformat(bound["end_date"]),
    }
    point_dates.update(datetime.date.fromisoformat(tx["date"]) for tx in relevant)
    points = []
    start = datetime.date.fromisoformat(bound["start_date"])
    for point_date in sorted(point_dates):
        opening_date = point_date if point_date == start else point_date + datetime.timedelta(days=1)
        holdings = reconstruct_native_opening(
            accounts=accounts.values(),
            transactions=transactions,
            start_date=opening_date,
        )
        points.append(
            {
                "date": point_date.isoformat(),
                "balance": _holdings_value(
                    holdings["by_instrument"],
                    target,
                    point_date.isoformat(),
                    convert,
                ),
            }
        )
    selected = [account_id for account_id, row in accounts.items() if row.get("inBalance") is True]
    report = render_balance_trend(
        points,
        selected_account_ids=selected,
        history=True,
        current_date=bound["end_date"],
        currency_filter=args["currency_filter"],
        currency=_currency_code(target),
    )
    report["period"] = periods.public_period(bound)
    report["metadata"]["history_source"] = "synced_transactions_reconstructed_from_current_balances"
    return json.dumps(_jsonable(report), ensure_ascii=False)


def _account_perimeter(args: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    scope = args["account_scope"]
    selected = set(args["account_ids"])
    result: dict[str, dict[str, Any]] = {}
    for raw in _cache.CACHE.accounts():
        account_id = raw.get("id")
        if account_id is None:
            continue
        row = dict(raw)
        if scope == "all":
            row["inBalance"] = True
        elif scope == "selected":
            row["inBalance"] = str(account_id) in selected
        result[str(account_id)] = row
    return result


def _transactions_between(bound: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        tx
        for tx in _cache.CACHE.transactions()
        if not _is_deleted(tx)
        and bound["start_date"] <= str(tx.get("date", "")) <= bound["end_date"]
    ]


def _flow_transactions(
    bound: Mapping[str, Any], accounts: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    for tx in _transactions_between(bound):
        income = _money(tx.get("income", 0), "transaction.income")
        outcome = _money(tx.get("outcome", 0), "transaction.outcome")
        sides = []
        if income:
            sides.append(tx.get("incomeAccount"))
        if outcome:
            sides.append(tx.get("outcomeAccount"))
        if any(
            account_id is not None
            and accounts.get(str(account_id), {}).get("inBalance") is True
            for account_id in sides
        ):
            result.append(tx)
    return result


def _category_rows(
    bound: Mapping[str, Any],
    direction: str,
    group_by: str,
    accounts: Mapping[str, Mapping[str, Any]],
    convert: Any,
    target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    side = "income" if direction == "INCOME" else "outcome"
    opposite = "outcome" if side == "income" else "income"
    rows = []
    for tx in _transactions_between(bound):
        amount = _money(tx.get(side, 0), f"transaction.{side}")
        if amount <= ZERO or _money(tx.get(opposite, 0), f"transaction.{opposite}") > ZERO:
            continue
        account_id = tx.get(f"{side}Account")
        account = accounts.get(str(account_id)) if account_id is not None else None
        if not account or account.get("inBalance") is not True:
            continue
        converted = _convert_transaction(amount, tx, side, account, target, convert)
        if group_by == "PAYEE":
            payee = tx.get("payee")
            rows.append({"payee": str(payee) if payee else UNKNOWN_PAYEE, "amount": converted})
        else:
            tags = tx.get("tag") or []
            rows.append({"category_id": str(tags[0]) if tags else UNCATEGORIZED, "amount": converted})
    return rows


def _difference_rows(
    bound: Mapping[str, Any],
    direction: str,
    group_by: str,
    accounts: Mapping[str, Mapping[str, Any]],
    convert: Any,
    target: Mapping[str, Any],
    mode: str,
) -> list[dict[str, Any]]:
    if mode == "REFUNDS":
        return _refund_rows(bound, direction, group_by, accounts, convert, target)
    income_rows = _category_rows(bound, "INCOME", group_by, accounts, convert, target)
    outcome_rows = _category_rows(bound, "OUTCOME", group_by, accounts, convert, target)
    income = _grouped_amounts(income_rows, group_by)
    outcome = _grouped_amounts(outcome_rows, group_by)
    categories = (
        {str(tag["id"]): tag for tag in _cache.CACHE.tags()}
        if group_by == "TAG"
        else {}
    )
    adjusted = apply_category_difference(
        income=income,
        outcome=outcome,
        categories=categories,
        mode=mode,
    )
    side = "income" if direction == "INCOME" else "outcome"
    key = "payee" if group_by == "PAYEE" else "category_id"
    return [
        {key: group_key, "amount": amount}
        for group_key, amount in adjusted[side].items()
    ]


def _refund_rows(
    bound: Mapping[str, Any],
    direction: str,
    group_by: str,
    accounts: Mapping[str, Mapping[str, Any]],
    convert: Any,
    target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected = "income" if direction == "INCOME" else "outcome"
    opposite = "outcome" if expected == "income" else "income"
    categories = {str(tag["id"]): tag for tag in _cache.CACHE.tags()}
    totals: dict[str, Decimal] = {}
    for tx in _transactions_between(bound):
        income = _money(tx.get("income", 0), "transaction.income")
        outcome = _money(tx.get("outcome", 0), "transaction.outcome")
        if income > ZERO and outcome > ZERO:
            continue
        side = "income" if income > ZERO else "outcome" if outcome > ZERO else None
        if side is None:
            continue
        account_id = tx.get(f"{side}Account")
        account = accounts.get(str(account_id)) if account_id is not None else None
        if not account or account.get("inBalance") is not True:
            continue
        sign = Decimal(1) if side == expected else ZERO
        if side == opposite:
            if tx.get("incomeAccount") != tx.get("outcomeAccount"):
                continue
            tags = tx.get("tag") or []
            category = categories.get(str(tags[0])) if tags else None
            if category is None:
                continue
            show_expected = category.get("showIncome" if expected == "income" else "showOutcome")
            show_opposite = category.get("showOutcome" if expected == "income" else "showIncome")
            if type(show_expected) is not bool or type(show_opposite) is not bool:
                raise UnsupportedCalculationError(
                    "REFUNDS requires synced Tag.showIncome and Tag.showOutcome",
                    {"category_id": category.get("id")},
                )
            sign = Decimal(-1) if show_expected and not show_opposite else ZERO
        if sign == ZERO:
            continue
        amount = income if side == "income" else outcome
        converted = _convert_transaction(amount, tx, side, account, target, convert)
        tags = tx.get("tag") or []
        group_key = (
            str(tx.get("payee") or UNKNOWN_PAYEE)
            if group_by == "PAYEE"
            else str(tags[0]) if tags else UNCATEGORIZED
        )
        totals[group_key] = totals.get(group_key, ZERO) + sign * converted
    key = "payee" if group_by == "PAYEE" else "category_id"
    return [{key: group_key, "amount": amount} for group_key, amount in totals.items()]


def _grouped_amounts(rows: Iterable[Mapping[str, Any]], group_by: str) -> dict[str, Decimal]:
    key = "payee" if group_by == "PAYEE" else "category_id"
    result: dict[str, Decimal] = {}
    for row in rows:
        value = str(row[key])
        result[value] = result.get(value, ZERO) + _signed_money(row["amount"], "row.amount")
    return result


def _budget_amounts(resolved: Mapping[str, Any], direction: str) -> dict[str, Decimal]:
    cfg = config._load_config()
    user = select_plan_user(_cache.CACHE.users(), cfg.get("plan_user_id"), allow_empty=True)
    user_id = user.get("id")
    if user_id is None:
        return {}
    month_anchor = resolved.get("budget_month_anchor") or f"{resolved['start_date'][:7]}-01"
    field = "income" if direction == "INCOME" else "outcome"
    result: dict[str, Decimal] = {}
    for budget in _cache.CACHE.budgets():
        category_id = budget.get("tag")
        if (
            category_id is None
            or str(budget.get("user")) != str(user_id)
            or budget.get("date") != month_anchor
        ):
            continue
        result[str(category_id)] = result.get(str(category_id), ZERO) + _money(
            budget.get(field, 0), f"budget.{field}"
        )
    return result


def _income_outcome_totals(
    bound: Mapping[str, Any],
    accounts: Mapping[str, Mapping[str, Any]],
    convert: Any,
    target: Mapping[str, Any],
) -> tuple[Decimal, Decimal]:
    totals = {"income": ZERO, "outcome": ZERO}
    for tx in _transactions_between(bound):
        income = _money(tx.get("income", 0), "transaction.income")
        outcome = _money(tx.get("outcome", 0), "transaction.outcome")
        if income > ZERO and outcome > ZERO:
            continue
        side = "income" if income > ZERO else "outcome" if outcome > ZERO else None
        if side is None:
            continue
        account_id = tx.get(f"{side}Account")
        account = accounts.get(str(account_id)) if account_id is not None else None
        if not account or account.get("inBalance") is not True:
            continue
        amount = income if side == "income" else outcome
        totals[side] += _convert_transaction(amount, tx, side, account, target, convert)
    return totals["income"], totals["outcome"]


def _comparison_bounds(args: Mapping[str, Any], count: int) -> list[dict[str, Any]]:
    current = args["resolved_period"]
    result = [dict(current)]
    if current["period"] == "custom":
        start = datetime.date.fromisoformat(current["start_date"])
        end = datetime.date.fromisoformat(current["end_date"])
        span = end - start + datetime.timedelta(days=1)
        for index in range(1, count + 1):
            previous_end = start - span * (index - 1) - datetime.timedelta(days=1)
            previous_start = previous_end - span + datetime.timedelta(days=1)
            result.append(
                periods.resolve_period(
                    {"start_date": previous_start, "end_date": previous_end},
                    today=_today(),
                )
            )
        return result

    for index in range(1, count + 1):
        selector: dict[str, Any] = {
            "period": current["period"],
            "period_offset": int(current["period_offset"]) - index,
        }
        if "first_weekday" in current:
            selector["first_weekday"] = current["first_weekday"]
        result.append(
            periods.resolve_period(
                selector,
                today=_today(),
                billing_start_day=int(current.get("billing_start_day", 1)),
                first_weekday=current.get("first_weekday"),
            )
        )
    return result


async def _rate_context(
    bounds: list[Mapping[str, Any]],
    accounts: Mapping[str, Mapping[str, Any]],
    currency: Any,
) -> tuple[Any, dict[str, Any]]:
    instruments = {
        str(item["id"]): dict(item)
        for item in _cache.CACHE.instruments()
        if item.get("id") is not None
    }
    main = _main_instrument(instruments.values())
    target = _target_instrument(currency, instruments, main)
    used = {
        str(account["instrument"])
        for account in accounts.values()
        if account.get("inBalance") is True and account.get("instrument") is not None
    }
    used.add(str(target["id"]))
    main_id = str(main["id"])
    from_date = min(bound["start_date"] for bound in bounds)
    to_date = max(bound["end_date"] for bound in bounds)
    rate_cache = InstrumentRateCache()
    predicates = [
        instrument_rate_predicate(instrument_id, main_id, from_date=from_date, to_date=to_date)
        for instrument_id in sorted(used)
        if instrument_id != main_id
    ]
    await fetch_instrument_rates(predicates, cache=rate_cache)
    return (
        exchange_converter(
            instruments=instruments.values(),
            main_instrument_id=main_id,
            cache=rate_cache,
        ),
        target,
    )


def _main_instrument(instruments: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    matches = [dict(row) for row in instruments if _money(row.get("rate"), "instrument.rate") == 1]
    if not matches:
        raise InvalidArgumentError("Synced instruments do not identify the user's main currency")
    return sorted(matches, key=lambda row: int(row["id"]))[0]


def _target_instrument(
    currency: Any,
    instruments: Mapping[str, dict[str, Any]],
    main: dict[str, Any],
) -> dict[str, Any]:
    if currency is None:
        return main
    direct = instruments.get(str(currency))
    if direct is not None:
        return direct
    code = str(currency).casefold()
    matches = [
        row
        for row in instruments.values()
        if str(row.get("shortTitle", "")).casefold() == code
    ]
    if len(matches) != 1:
        raise InvalidArgumentError(f"Unknown or ambiguous analytics currency: {currency}")
    return matches[0]


def _convert_transaction(
    amount: Decimal,
    tx: Mapping[str, Any],
    side: str,
    account: Mapping[str, Any],
    target: Mapping[str, Any],
    convert: Any,
) -> Decimal:
    instrument = tx.get(f"{side}Instrument", account.get("instrument"))
    if instrument is None:
        raise InvalidArgumentError(f"transaction.{side} requires an instrument")
    return convert(amount, instrument, target["id"], str(tx.get("date")))


def _holdings_value(
    holdings: Mapping[Any, Any],
    target: Mapping[str, Any],
    on_date: str,
    convert: Any,
) -> Decimal:
    return sum(
        (convert(_money(amount, "holding.amount"), instrument, target["id"], on_date)
         for instrument, amount in holdings.items()),
        ZERO,
    )


def _decimalized_transaction(tx: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(tx)
    result["income"] = _money(tx.get("income", 0), "transaction.income")
    result["outcome"] = _money(tx.get("outcome", 0), "transaction.outcome")
    return result


def _currency_code(instrument: Mapping[str, Any]) -> str:
    return str(instrument.get("shortTitle") or instrument["id"])


def _period_days(bound: Mapping[str, Any]) -> int:
    start = datetime.date.fromisoformat(bound["start_date"])
    end = datetime.date.fromisoformat(bound["end_date"])
    return (end - start).days + 1


def _is_deleted(item: Mapping[str, Any]) -> bool:
    return (
        item.get("deleted") is True
        or item.get("isDeleted") is True
        or item.get("state") == "deleted"
        or item.get("status") == "deleted"
    )


def _money(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidArgumentError(f"{field} must be a finite number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be a finite number") from exc
    if not result.is_finite() or result < ZERO:
        raise InvalidArgumentError(f"{field} must be a finite non-negative number")
    return result


def _signed_money(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidArgumentError(f"{field} must be a finite number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be a finite number") from exc
    if not result.is_finite():
        raise InvalidArgumentError(f"{field} must be a finite number")
    return result


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
