from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from typing import Any

from .. import periods
from ..analytics.category_difference import apply_category_difference
from ..errors import InvalidArgumentError, ToolError, UnsupportedCalculationError
from ..instrument_rates import (
    InstrumentRateCache,
    exchange_converter,
    fetch_instrument_rates,
    instrument_rate_predicate,
)
from ..transfer_classifier import BALANCE, classify_transfer_event, evaluate_plan_transfer
from .categories import ALL_CATEGORIES_ID, UNCATEGORIZED_CATEGORY_ID, category_bucket
from .context import PlansContext, resolve_previous_billing_period
from .events import event_from_reminder_marker, event_from_transaction
from .exchange import calculate_exchange_difference
from .forecast import build_daily_forecast
from .models import CategoryBucket, PlanCategoryRow, PlanEvent, PlanRowSide
from .opening import reconstruct_native_opening, resolve_opening_balance
from .reserve import calculate_row


RATE_CACHE = InstrumentRateCache()
ZERO = Decimal(0)


def render_analysis(ctx: PlansContext) -> dict[str, Any]:
    return _jsonable(_render_period(ctx, ctx.resolved_period))


async def prepare_historical_rates(ctx: PlansContext) -> None:
    target_id = str(_target_instrument(ctx))
    dates = _conversion_dates(ctx)
    instrument_ids = sorted(
        instrument_id
        for instrument_id in _used_instrument_ids(ctx)
        if instrument_id != target_id and instrument_id in ctx.instruments
    )
    predicates = [
        instrument_rate_predicate(
            instrument_id,
            target_id,
            from_date=dates[0],
            to_date=dates[-1],
        )
        for instrument_id in instrument_ids
    ]
    await fetch_instrument_rates(predicates, cache=RATE_CACHE)


def _render_period(ctx: PlansContext, resolved_period: dict[str, Any]) -> dict[str, Any]:
    period_ctx = replace(ctx, budgets=_budgets_for_period(ctx, resolved_period))
    target_instrument = _target_instrument(ctx)
    convert = _converter(ctx, target_instrument)
    events = _period_events(ctx, resolved_period["start_date"], resolved_period["end_date"])
    rows, calendar = _category_rows(period_ctx, events, convert, target_instrument)
    roots = tuple(calculate_row(row) for row in _roots(rows))
    transfers, transfer_totals, exchange_flows = _transfers(
        ctx,
        events,
        convert,
        target_instrument,
    )
    opening = _opening(ctx, resolved_period, convert, target_instrument)
    exchange = _exchange_difference(
        ctx,
        opening_native=opening["native"],
        exclude_opening_balance=not opening["public"]["included"],
        target_instrument=target_instrument,
        convert=convert,
        end_date=resolved_period["end_date"],
        exchange_flows=exchange_flows,
    )
    income = _income_summary(period_ctx, roots)
    expense = _expense_summary(period_ctx, roots)
    balance_raw = (
        opening["total"]
        + income["for_balance"]
        + exchange["fact"]
        - expense["for_balance"]
        - transfer_totals["net"]
    )

    period_output = periods.public_period(resolved_period)
    period_output["budget_months"] = [resolved_period["budget_month_anchor"]]
    summary = {
        "budget_mode": ctx.mode_name,
        "plan_balance_mode": ctx.plan_balance_mode,
        "plan_settings": sorted(ctx.plan_settings),
        "difference_calculation_mode": ctx.difference_calculation_mode,
        "budget_mode_label": (
            "Баланс vs Расходы" if ctx.plan_balance_mode == BALANCE else "Доходы vs Расходы"
        ),
        "period": period_output,
        "income": income,
        "expense": expense,
        "transfers": transfer_totals,
        "opening_balance": opening["public"],
        "exchange_difference": exchange,
    }
    summary["balance"] = (
        round(balance_raw)
        if ctx.cfg.get("round_balance_to_integer", True)
        else balance_raw
    )
    remaining_plan = expense["for_balance"] + transfer_totals["net"] - expense["actual"]
    summary["balance_breakdown"] = {
        "total_income": income["for_balance"],
        "opening_balance": opening["total"],
        "exchange_difference_fact": exchange["fact"],
        "total_expense_plan": expense["for_balance"],
        "total_transfers_net": transfer_totals["net"],
        "total_plan": expense["for_balance"] + transfer_totals["net"],
        "remaining_plan": remaining_plan,
        "formula": (
            f"{opening['total']} + {income['for_balance']} + {exchange['fact']} "
            f"- {expense['for_balance']} - {transfer_totals['net']} = {balance_raw}"
        ),
    }

    result: dict[str, Any] = {
        "summary": summary,
        "income": sorted(
            (_row_json(row, "income") for row in roots if _active(row.income)),
            key=lambda item: item["actual"] + item["residue"],
            reverse=True,
        ),
        "expenses": sorted(
            (_row_json(row, "outcome") for row in roots if _active(row.outcome)),
            key=lambda item: max(
                item["actual"] + item["planned_from_reminders"],
                item["budget"],
            ),
            reverse=True,
        ),
        "transfers": transfers,
    }
    if ctx.args["show_calendar"]:
        result["calendar"] = sorted(
            calendar + _transfer_calendar(transfers),
            key=lambda item: item["date"],
        )
    if ctx.args["show_forecast"]:
        result["forecast"] = build_daily_forecast(
            residue=max(ZERO, remaining_plan + exchange["residue"]),
            planned_operations=_forecast_operations(calendar, transfers),
            start_date=resolved_period["start_date"],
            end_date=resolved_period["end_date"],
            cutoff_date=ctx.today,
            show_calendar=ctx.args["show_calendar"],
        )["points"]
    return result


def _period_events(ctx: PlansContext, start: str, end: str) -> list[PlanEvent]:
    events: list[PlanEvent] = []
    for transaction in ctx.transactions:
        tx_date = str(transaction.get("date", ""))
        if start <= tx_date <= end:
            _reject_unknown_balance_change(transaction, ctx)
            events.append(event_from_transaction(transaction, ctx.accounts))
        elif tx_date >= start:
            _reject_unknown_balance_change(transaction, ctx)
    reminders = {
        item["id"]: item
        for item in ctx.reminders
        if item.get("id") is not None
    }
    for marker in ctx.markers:
        if not ctx.forecast_enabled and marker.get("isForecast") is True:
            continue
        marker_state = marker.get("state")
        if marker_state == "deleted":
            continue
        if marker_state not in {"planned", "processed"}:
            raise InvalidArgumentError(
                "reminderMarker state must be planned, processed, or deleted"
            )
        if not start <= str(marker.get("date", "")) <= end:
            continue
        reminder = reminders.get(marker.get("reminder"))
        if reminder is not None:
            events.append(event_from_reminder_marker(reminder, marker, ctx.accounts))
    return events


def _reject_unknown_balance_change(transaction: dict[str, Any], ctx: PlansContext) -> None:
    if not (
        transaction.get("income")
        and transaction.get("outcome")
        and transaction.get("incomeAccount") == transaction.get("outcomeAccount")
    ):
        return
    same_account = transaction.get("incomeAccount") == transaction.get("outcomeAccount")
    one_sided = not transaction.get("income") or not transaction.get("outcome")
    if not same_account and not one_sided:
        return
    delta = ZERO
    if _account_in_balance(ctx, transaction.get("incomeAccount")):
        delta += Decimal(str(transaction.get("income", 0) or 0))
    if _account_in_balance(ctx, transaction.get("outcomeAccount")):
        delta -= Decimal(str(transaction.get("outcome", 0) or 0))
    if delta:
        raise UnsupportedCalculationError(
            "An unclassified transaction changes the Plans balance perimeter",
            {
                "reason": "unclassified_balance_change",
                "transaction_id": transaction.get("id"),
                "perimeter_delta": delta,
            },
        )


def _category_rows(
    ctx: PlansContext,
    events: list[PlanEvent],
    convert: Any,
    target_instrument: Any,
) -> tuple[dict[str, PlanCategoryRow], list[dict[str, Any]]]:
    sides: dict[str, dict[str, Any]] = defaultdict(_empty_side)
    for budget in ctx.budgets:
        category_id = budget.get("category_id")
        if category_id == ALL_CATEGORIES_ID:
            continue
        if category_id not in (None, UNCATEGORIZED_CATEGORY_ID) and str(category_id) not in ctx.categories:
            raise UnsupportedCalculationError(
                "A Plans budget references a category missing from the synced category tree",
                {"reason": "unknown_budget_category", "category_id": category_id},
            )
        bucket = category_bucket(category_id, ctx.categories)
        row = sides[bucket.category_id]
        if ctx.forecast_enabled or budget.get("isIncomeForecast") is not True:
            row["income_budget"] += Decimal(str(budget.get("income", 0) or 0))
        row["income_lock"] = row["income_lock"] or budget.get("incomeLock") is True
        if ctx.forecast_enabled or budget.get("isOutcomeForecast") is not True:
            row["outcome_budget"] += Decimal(str(budget.get("outcome", 0) or 0))
        row["outcome_lock"] = row["outcome_lock"] or budget.get("outcomeLock") is True

    calendar: list[dict[str, Any]] = []
    for event in events:
        if event.kind == "transfer":
            continue
        side = event.income_side if event.kind == "income" else event.outcome_side
        if side is None or not side.in_balance:
            continue
        bucket = category_bucket(
            event.category_ids[0] if event.category_ids else None,
            ctx.categories,
        )
        amount = convert(side.amount, side.currency, target_instrument, event.date)
        row = sides[bucket.category_id]
        status = "completed" if event.source_type == "transaction" else event.marker_state
        field_prefix = "income" if event.kind == "income" else "outcome"
        if status == "planned":
            row[f"{field_prefix}_planned"] += amount
        elif status == "processed":
            row[f"{field_prefix}_processed"] += amount
        else:
            row[f"{field_prefix}_fact"] += amount
            if (
                ctx.difference_calculation_mode == "REFUNDS"
                and _is_refund_event(event, ctx.categories)
            ):
                row[f"{field_prefix}_refund"] += amount
        item = _calendar_item(event, bucket, amount, status or "completed")
        row["items"].append(item)
        calendar.append(item)

    for category_id in list(sides):
        _ensure_parents(
            sides,
            category_bucket(category_id, ctx.categories),
            ctx.categories,
        )
    rows = _tree_rows(sides, ctx.categories)
    return _apply_category_difference(
        rows,
        ctx.categories,
        ctx.difference_calculation_mode,
        refund_income={key: value["income_refund"] for key, value in sides.items()},
        refund_outcome={key: value["outcome_refund"] for key, value in sides.items()},
    ), calendar


def _empty_side() -> dict[str, Any]:
    return {
        "income_fact": ZERO,
        "income_refund": ZERO,
        "income_planned": ZERO,
        "income_processed": ZERO,
        "income_budget": ZERO,
        "income_lock": False,
        "outcome_fact": ZERO,
        "outcome_refund": ZERO,
        "outcome_planned": ZERO,
        "outcome_processed": ZERO,
        "outcome_budget": ZERO,
        "outcome_lock": False,
        "items": [],
    }


def _is_refund_event(
    event: PlanEvent,
    categories: dict[str, dict[str, Any]],
) -> bool:
    if event.source_type != "transaction" or not event.category_ids:
        return False
    if event.income_side is None or event.outcome_side is None:
        return False
    if event.income_side.account_id != event.outcome_side.account_id:
        return False
    category = categories.get(event.category_ids[0])
    if category is None:
        return False
    show_income = category.get("showIncome")
    show_outcome = category.get("showOutcome")
    if type(show_income) is not bool or type(show_outcome) is not bool:
        raise UnsupportedCalculationError(
            "REFUNDS requires synced Tag.showIncome and Tag.showOutcome",
            {"category_id": category.get("id", event.category_ids[0])},
        )
    return (
        event.kind == "income" and show_outcome and not show_income
    ) or (
        event.kind == "outcome" and show_income and not show_outcome
    )


def _tree_rows(
    raw: dict[str, dict[str, Any]],
    categories: dict[str, dict[str, Any]],
) -> dict[str, PlanCategoryRow]:
    children: dict[str | None, list[str]] = defaultdict(list)
    for category_id in raw:
        bucket = category_bucket(category_id, categories)
        parent_id = bucket.parent_id if bucket.parent_id in raw else None
        children[parent_id].append(category_id)

    def build(category_id: str, visiting: frozenset[str]) -> PlanCategoryRow:
        if category_id in visiting:
            raise UnsupportedCalculationError(
                "A Plans category tree contains a cycle",
                {"reason": "category_cycle", "category_id": category_id},
            )
        data = raw[category_id]
        child_rows = tuple(
            build(child_id, visiting | {category_id})
            for child_id in sorted(children.get(category_id, []))
        )
        return PlanCategoryRow(
            category=category_bucket(category_id, categories),
            income=PlanRowSide(
                fact=data["income_fact"],
                planned=data["income_planned"],
                processed=data["income_processed"],
                explicit_budget=data["income_budget"],
                lock=data["income_lock"],
            ),
            outcome=PlanRowSide(
                fact=data["outcome_fact"],
                planned=data["outcome_planned"],
                processed=data["outcome_processed"],
                explicit_budget=data["outcome_budget"],
                lock=data["outcome_lock"],
            ),
            children=child_rows,
        )

    return {
        category_id: build(category_id, frozenset())
        for category_id in sorted(children.get(None, []))
    }


def _ensure_parents(
    rows: dict[str, dict[str, Any]],
    bucket: CategoryBucket,
    categories: dict[str, dict[str, Any]],
) -> None:
    parent_id = bucket.parent_id
    seen = {bucket.category_id}
    while parent_id and parent_id not in seen and parent_id in categories:
        seen.add(parent_id)
        rows[parent_id]
        parent_id = categories[parent_id].get("parent")


def _roots(rows: dict[str, PlanCategoryRow]) -> tuple[PlanCategoryRow, ...]:
    return tuple(sorted(rows.values(), key=lambda row: row.category.name))


def _apply_category_difference(
    rows: dict[str, PlanCategoryRow],
    categories: dict[str, dict[str, Any]],
    mode: str,
    *,
    refund_income: dict[str, Decimal],
    refund_outcome: dict[str, Decimal],
) -> dict[str, PlanCategoryRow]:
    flat: dict[str, PlanCategoryRow] = {}

    def collect(row: PlanCategoryRow) -> None:
        flat[row.category.category_id] = row
        for child in row.children:
            collect(child)

    for root in rows.values():
        collect(root)
    adjusted = apply_category_difference(
        income={key: row.income.fact for key, row in flat.items()},
        outcome={key: row.outcome.fact for key, row in flat.items()},
        categories=categories,
        mode=mode,
        refund_income=refund_income,
        refund_outcome=refund_outcome,
    )

    def rebuild(row: PlanCategoryRow) -> PlanCategoryRow:
        category_id = row.category.category_id
        return replace(
            row,
            income=replace(
                row.income,
                fact_with_refund=adjusted["income"][category_id],
            ),
            outcome=replace(
                row.outcome,
                fact_with_refund=adjusted["outcome"][category_id],
            ),
            children=tuple(rebuild(child) for child in row.children),
        )

    return {category_id: rebuild(row) for category_id, row in rows.items()}


def _transfers(
    ctx: PlansContext,
    events: list[PlanEvent],
    convert: Any,
    target_instrument: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, list[dict[str, Any]]]]:
    out_total = ZERO
    in_total = ZERO
    result = []
    flows = {
        "excluded_income": [],
        "excluded_expense": [],
        "income_facts": [],
        "expense_facts": [],
    }
    for event in events:
        if event.kind != "transfer":
            continue
        classified = classify_transfer_event(_transfer_event_json(event, ctx))
        evaluated = evaluate_plan_transfer(
            classified,
            plan_balance_mode=ctx.plan_balance_mode,
            plan_settings=ctx.plan_settings,
        )
        converted_effects = []
        for effect in evaluated["effects"]:
            instrument = _instrument_from_currency(ctx, effect["currency"])
            amount = convert(effect["amount"], instrument, target_instrument, event.date)
            if effect["kind"] == "expense":
                out_total += amount
                if event.source_type == "transaction":
                    flows["expense_facts"].append(
                        {
                            "instrument": event.outcome_side.currency,
                            "amount": event.outcome_side.amount,
                            "date": event.date,
                        }
                    )
            else:
                in_total += amount
                if event.source_type == "transaction":
                    flows["income_facts"].append(
                        {
                            "instrument": event.income_side.currency,
                            "amount": event.income_side.amount,
                            "date": event.date,
                        }
                    )
            converted_effects.append({**effect, "amount": amount})
        if not evaluated["effects"] and classified["direction"] in {
            "off_balance_to_balance",
            "balance_to_off_balance",
        }:
            side = (
                event.income_side
                if classified["direction"] == "off_balance_to_balance"
                else event.outcome_side
            )
            label = (
                "excluded_income"
                if classified["direction"] == "off_balance_to_balance"
                else "excluded_expense"
            )
            flows[label].append(
                {"instrument": side.currency, "amount": side.amount, "date": event.date}
            )
        result.append({**evaluated, "effects": converted_effects})
    return (
        sorted(result, key=lambda item: item["event"].get("date") or ""),
        {
            "out": out_total,
            "in": in_total,
            "net": out_total - in_total,
            "description": "Directed balance-boundary transfers under ZenMoney PlanSetting policy",
        },
        flows,
    )


def _opening(
    ctx: PlansContext,
    resolved_period: dict[str, Any],
    convert: Any,
    target_instrument: Any,
) -> dict[str, Any]:
    def previous_day_summary(
        _day: str,
        *,
        plan_balance_mode: str,
        plan_settings: frozenset[str],
    ) -> dict[str, Any]:
        previous = resolve_previous_billing_period(ctx)
        previous_ctx = replace(
            ctx,
            plan_balance_mode=plan_balance_mode,
            plan_settings=plan_settings,
            resolved_period=previous,
            args={**ctx.args, "show_forecast": False, "show_calendar": False},
        )
        previous_balance = _render_period(previous_ctx, previous)["summary"]["balance"]
        amount = Decimal(str(previous_balance))
        return {
            "balance": {
                "by_account": {
                    "__previous_summary__": {
                        "instrument": target_instrument,
                        "amount": amount,
                    },
                },
                "by_instrument": {target_instrument: amount},
            }
        }

    raw = resolve_opening_balance(
        accounts=ctx.accounts.values(),
        transactions=ctx.transactions,
        start_date=resolved_period["start_date"],
        today=ctx.today,
        plan_balance_mode=ctx.plan_balance_mode,
        plan_settings=ctx.plan_settings,
        previous_day_summary=previous_day_summary,
    )
    if not raw["included"]:
        native_balance = reconstruct_native_opening(
            accounts=ctx.accounts.values(),
            transactions=ctx.transactions,
            start_date=resolved_period["start_date"],
        )
        return {
            "native": native_balance["by_instrument"],
            "native_balance": native_balance,
            "total": ZERO,
            "public": {
                "total": ZERO,
                "by_currency": {},
                "effects": [],
                "included": False,
                "source": "excluded",
            },
        }
    if raw["source"] == "previous_day_summary":
        native = raw["balance"]["by_instrument"]
        balance = _native_total(
            native,
            resolved_period["start_date"],
            target_instrument,
            convert,
        )
        return {
            "native": native,
            "native_balance": raw["balance"],
            "total": balance,
            "public": {
                "total": balance,
                "by_currency": {
                    _currency_title(ctx, instrument): amount
                    for instrument, amount in sorted(
                        native.items(),
                        key=lambda item: str(item[0]),
                    )
                },
                "effects": [],
                "included": True,
                "source": "previous_day_summary",
                "recursion_policy": raw["recursion_policy"],
            },
        }
    native = raw["balance"]["by_instrument"]
    total = _native_total(
        native,
        resolved_period["start_date"],
        target_instrument,
        convert,
    )
    return {
        "native": native,
        "native_balance": raw["balance"],
        "total": total,
        "public": {
            "total": total,
            "by_currency": {
                _currency_title(ctx, instrument): amount
                for instrument, amount in sorted(
                    native.items(),
                    key=lambda item: str(item[0]),
                )
            },
            "effects": [],
            "included": True,
            "source": raw["source"],
        },
    }


def _native_total(
    native: dict[Any, Any],
    on_date: str,
    target_instrument: Any,
    convert: Any,
) -> Decimal:
    return sum(
        (
            convert(amount, instrument, target_instrument, on_date)
            for instrument, amount in native.items()
        ),
        ZERO,
    )


def _exchange_difference(
    ctx: PlansContext,
    *,
    opening_native: dict[Any, Any],
    exclude_opening_balance: bool,
    target_instrument: Any,
    convert: Any,
    end_date: str,
    exchange_flows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    period_end = datetime.date.fromisoformat(end_date)
    target_native = reconstruct_native_opening(
        accounts=ctx.accounts.values(),
        transactions=ctx.transactions,
        start_date=(period_end + datetime.timedelta(days=1)).isoformat(),
    )["by_instrument"]
    current = [
        {
            "instrument": instrument,
            "balance": amount,
            "date": end_date,
        }
        for instrument, amount in target_native.items()
    ]
    opening = [
        {
            "instrument": instrument,
            "balance": amount,
            "date": ctx.resolved_period["start_date"],
        }
        for instrument, amount in opening_native.items()
    ]
    used_instruments = {row["instrument"] for row in [*current, *opening]}
    for rows in exchange_flows.values():
        used_instruments.update(row["instrument"] for row in rows)
    if {str(instrument) for instrument in used_instruments} <= {str(target_instrument)}:
        return {
            "fact": ZERO,
            "budget": ZERO,
            "residue": ZERO,
            "currency": _currency_title(ctx, target_instrument),
        }
    if not opening and not current:
        row = {"fact": ZERO, "budget": ZERO, "residue": ZERO, "components": {}}
    else:
        income_facts, expense_facts = _exchange_facts(ctx)
        row = calculate_exchange_difference(
            opening_holdings=opening,
            target_holdings=current,
            excluded_transfer_income=exchange_flows["excluded_income"],
            excluded_transfer_expense=exchange_flows["excluded_expense"],
            income_facts=income_facts + exchange_flows["income_facts"],
            expense_facts=expense_facts + exchange_flows["expense_facts"],
            target_instrument=target_instrument,
            period_end_date=end_date,
            exclude_opening_balance=exclude_opening_balance,
            convert=convert,
        )
    return {
        "fact": row["fact"],
        "budget": row["budget"],
        "residue": row["residue"],
        "currency": _currency_title(ctx, target_instrument),
        "components": row.get("components", {}),
    }


def _income_summary(ctx: PlansContext, roots: tuple[PlanCategoryRow, ...]) -> dict[str, Any]:
    actual = sum((row.income.fact for row in roots), ZERO)
    actual_with_refunds = sum((row.income.fact_with_refund for row in roots), ZERO)
    residue = sum((row.income.residue for row in roots), ZERO)
    aggregate_budget = sum(
        Decimal(str(budget.get("income", 0) or 0))
        for budget in ctx.budgets
        if budget.get("category_id") == ALL_CATEGORIES_ID
    )
    return {
        "actual": actual,
        "actual_with_refunds": actual_with_refunds,
        "planned": sum((row.income.planned for row in roots), ZERO),
        "processed": sum((row.income.processed for row in roots), ZERO),
        "explicit_budget": sum(_sum_side(row, "income", "explicit_budget") for row in roots),
        "effective_budget": sum((row.income.effective_budget for row in roots), ZERO),
        "aggregate_budget": aggregate_budget,
        "residue": residue,
        "for_balance": actual + residue,
        "description": "for_balance = fact + root category residue.",
    }


def _expense_summary(ctx: PlansContext, roots: tuple[PlanCategoryRow, ...]) -> dict[str, Any]:
    aggregate_budget = sum(
        Decimal(str(budget.get("outcome", 0) or 0))
        for budget in ctx.budgets
        if budget.get("category_id") == ALL_CATEGORIES_ID
    )
    actual = sum((row.outcome.fact for row in roots), ZERO)
    actual_with_refunds = sum((row.outcome.fact_with_refund for row in roots), ZERO)
    planned = sum((row.outcome.planned for row in roots), ZERO)
    for_balance = sum((row.outcome.fact + row.outcome.residue for row in roots), ZERO)
    category_budget = sum(_leaf_budget(row) for row in roots)
    return {
        "budget": max(aggregate_budget, category_budget),
        "budget_scope": "configured_max_including_all",
        "category_budget": category_budget,
        "aggregate_budget": aggregate_budget,
        "actual": actual,
        "actual_with_refunds": actual_with_refunds,
        "planned": planned,
        "processed_planned": sum((row.outcome.processed for row in roots), ZERO),
        "category_difference_policy": ctx.difference_calculation_mode,
        "remaining": for_balance - actual,
        "expected_total": actual + planned,
        "for_balance": for_balance,
        "description": "for_balance = raw fact + category-tree reserve calculated from fact_with_refund.",
    }


def _row_json(row: PlanCategoryRow, side_name: str) -> dict[str, Any]:
    side = getattr(row, side_name)
    data = {
        "category_id": row.category.category_id,
        "category_name": _category_name(row.category),
        "category_full_name": _category_name(row.category),
        "parent_id": row.category.parent_id,
        "parent_name": None,
        "is_parent": bool(row.children),
        "actual": side.fact,
        "actual_with_refunds": side.fact_with_refund,
        "items": [],
        "children": [
            _row_json(child, side_name)
            for child in row.children
            if _active(getattr(child, side_name))
        ],
    }
    if side_name == "income":
        data.update(
            {
                "planned": side.planned,
                "processed": side.processed,
                "explicit_budget": side.explicit_budget,
                "income_lock": side.lock,
                "effective_budget": side.effective_budget,
                "residue": side.residue,
            }
        )
    else:
        data.update(
            {
                "planned_from_reminders": side.planned,
                "processed_from_reminders": side.processed,
                "budget": side.explicit_budget,
                "outcome_lock": side.lock,
                "remaining": side.residue,
            }
        )
    return data


def _active(side: PlanRowSide) -> bool:
    return any(
        value != ZERO
        for value in (
            side.fact,
            side.planned,
            side.processed,
            side.explicit_budget,
            side.effective_budget,
            side.residue,
        )
    )


def _leaf_budget(row: PlanCategoryRow) -> Decimal:
    if not row.children:
        return row.outcome.explicit_budget
    return max(
        row.outcome.explicit_budget,
        sum((_leaf_budget(child) for child in row.children), ZERO),
    )


def _sum_side(row: PlanCategoryRow, side_name: str, field: str) -> Decimal:
    side = getattr(row, side_name)
    return getattr(side, field) + sum(
        (_sum_side(child, side_name, field) for child in row.children),
        ZERO,
    )


def _target_instrument(ctx: PlansContext) -> Any:
    for account in ctx.accounts.values():
        if account.get("inBalance") is True and str(account.get("instrument")) not in ctx.instruments:
            raise ToolError(
                "UNKNOWN_CURRENCY",
                "Plans calculation requires a known currency for every included amount",
            )
    main = [
        item
        for item in ctx.instruments.values()
        if Decimal(str(item.get("rate", 0) or 0)) == Decimal(1)
    ]
    if main:
        return min(main, key=lambda item: int(item["id"]))["id"]
    for account in ctx.accounts.values():
        if account.get("inBalance") is True:
            return account.get("instrument")
    raise ToolError(
        "UNKNOWN_CURRENCY",
        "Plans calculation requires at least one inBalance account currency",
    )


def _converter(ctx: PlansContext, target_instrument: Any):
    return exchange_converter(
        instruments=ctx.instruments.values(),
        main_instrument_id=target_instrument,
        cache=RATE_CACHE,
    )


def _account_in_balance(ctx: PlansContext, account_id: Any) -> bool:
    account = ctx.accounts.get(str(account_id))
    return account is not None and account.get("inBalance") is True


def _currency_title(ctx: PlansContext, instrument: Any) -> str:
    row = ctx.instruments.get(str(instrument))
    if row is None:
        raise ToolError(
            "UNKNOWN_CURRENCY",
            "Plans calculation requires a known currency for every included amount",
        )
    return str(row.get("shortTitle") or row.get("title") or instrument)


def _instrument_from_currency(ctx: PlansContext, currency: Any) -> Any:
    for row in ctx.instruments.values():
        if currency in {row.get("id"), row.get("shortTitle"), row.get("title")}:
            return row["id"]
    raise ToolError(
        "UNKNOWN_CURRENCY",
        "Plans calculation requires a known currency for every included amount",
    )


def _transfer_event_json(event: PlanEvent, ctx: PlansContext) -> dict[str, Any]:
    return {
        "id": event.source_id,
        "date": event.date,
        "status": "completed" if event.source_type == "transaction" else event.marker_state,
        "comment": None,
        "outcome_side": _side_json(event.outcome_side, ctx),
        "income_side": _side_json(event.income_side, ctx),
    }


def _side_json(side: Any, ctx: PlansContext) -> dict[str, Any]:
    return {
        "account_id": side.account_id,
        "known_account": side.known_account,
        "amount": side.amount,
        "currency": _currency_title(ctx, side.currency),
        "in_balance": side.in_balance,
        "account_type": side.account_type,
        "account_subtype": side.account_subtype,
        "credit_limit": side.credit_limit,
        "savings": side.savings,
    }


def _calendar_item(
    event: PlanEvent,
    bucket: CategoryBucket,
    amount: Decimal,
    status: str,
) -> dict[str, Any]:
    return {
        "date": event.date,
        "type": "income" if event.kind == "income" else "expense",
        "category": _category_name(bucket),
        "payee": None,
        "amount": amount,
        "status": status,
    }


def _transfer_calendar(transfers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": item["event"]["date"],
            "type": "transfer",
            "direction": item["event"]["direction"],
            "outcome_side": item["event"]["outcome_side"],
            "income_side": item["event"]["income_side"],
            "plan_effects": item["effects"],
            "plan_reason": item["reason"],
            "comment": item["event"].get("comment"),
            "status": item["event"].get("status"),
        }
        for item in transfers
    ]


def _forecast_operations(
    calendar: list[dict[str, Any]],
    transfers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    operations = [
        {"date": item["date"], "amount": item["amount"], "status": item["status"]}
        for item in calendar
        if item.get("status") == "planned" and item.get("type") == "expense"
    ]
    for transfer in transfers:
        if transfer["event"].get("status") != "planned":
            continue
        operations.append(
            {
                "date": transfer["event"]["date"],
                "amount": sum(
                    (
                        effect["amount"]
                        for effect in transfer["effects"]
                        if effect["kind"] == "expense"
                    ),
                    ZERO,
                ),
                "status": "planned",
            }
        )
    return operations


def _exchange_facts(ctx: PlansContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    income_facts: list[dict[str, Any]] = []
    expense_facts: list[dict[str, Any]] = []
    start = ctx.resolved_period["start_date"]
    end = ctx.resolved_period["end_date"]
    for event in _period_events(ctx, start, end):
        if event.source_type != "transaction":
            continue
        if event.kind == "income" and event.income_side and event.income_side.in_balance:
            income_facts.append(
                {
                    "instrument": event.income_side.currency,
                    "amount": event.income_side.amount,
                    "date": event.date,
                }
            )
        elif event.kind == "outcome" and event.outcome_side and event.outcome_side.in_balance:
            expense_facts.append(
                {
                    "instrument": event.outcome_side.currency,
                    "amount": event.outcome_side.amount,
                    "date": event.date,
                }
            )
    return income_facts, expense_facts


def _budgets_for_period(ctx: PlansContext, resolved_period: dict[str, Any]) -> list[dict[str, Any]]:
    month_date = resolved_period["budget_month_anchor"]
    return [budget for budget in ctx.budgets if budget.get("month") == month_date]


def _conversion_dates(ctx: PlansContext) -> list[str]:
    dates = {ctx.today}
    for resolved in (ctx.resolved_period, resolve_previous_billing_period(ctx)):
        dates.add(resolved["start_date"])
        dates.add(resolved["end_date"])
        period_end = datetime.date.fromisoformat(resolved["end_date"])
        dates.add((period_end + datetime.timedelta(days=1)).isoformat())
    for transaction in ctx.transactions:
        if transaction.get("date"):
            dates.add(str(transaction["date"]))
    for marker in ctx.markers:
        if marker.get("date"):
            dates.add(str(marker["date"]))
    return sorted(dates)


def _used_instrument_ids(ctx: PlansContext) -> set[str]:
    instrument_ids = {
        str(account.get("instrument"))
        for account in ctx.accounts.values()
        if account.get("instrument") is not None and account.get("inBalance") is True
    }
    for transaction in ctx.transactions:
        for field in ("incomeInstrument", "outcomeInstrument"):
            if transaction.get(field) is not None:
                instrument_ids.add(str(transaction[field]))
    for reminder in ctx.reminders:
        for field in ("incomeInstrument", "outcomeInstrument"):
            if reminder.get(field) is not None:
                instrument_ids.add(str(reminder[field]))
    for marker in ctx.markers:
        for field in ("incomeInstrument", "outcomeInstrument"):
            if marker.get(field) is not None:
                instrument_ids.add(str(marker[field]))
    return instrument_ids


def _category_name(bucket: CategoryBucket) -> str:
    if bucket.category_id == UNCATEGORIZED_CATEGORY_ID:
        return "Без категории"
    return bucket.name


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
