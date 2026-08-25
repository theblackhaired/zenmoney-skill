from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .. import periods
from ..errors import InvalidArgumentError
from ..transfer_classifier import (
    BALANCE,
    BUDGET_LIMIT,
    EXCLUDE_OPENING_BALANCE,
    PUBLIC_MODE_TO_ZM,
    normalize_plan_balance_mode,
    parse_plan_settings,
    select_plan_user,
)


@dataclass(frozen=True, slots=True)
class PlansContext:
    args: dict[str, Any]
    cfg: dict[str, Any]
    accounts: dict[str, dict[str, Any]]
    categories: dict[str, dict[str, Any]]
    instruments: dict[str, dict[str, Any]]
    transactions: list[dict[str, Any]]
    reminders: list[dict[str, Any]]
    markers: list[dict[str, Any]]
    budgets: list[dict[str, Any]]
    today: str
    mode_name: str
    plan_balance_mode: str
    plan_settings: frozenset[str]
    resolved_period: dict[str, Any]
    difference_calculation_mode: str = "NONE"


def build_context(
    *,
    args: dict[str, Any],
    cfg: dict[str, Any],
    cache: Any,
    budgets: list[dict[str, Any]],
    today: str,
) -> PlansContext:
    mode_name, plan_balance_mode, plan_settings = _resolve_policy(
        args,
        cfg,
        cache.users(),
    )
    return PlansContext(
        args=args,
        cfg=cfg,
        accounts={
            str(item["id"]): item
            for item in cache.accounts()
            if item.get("id") is not None
        },
        categories={
            str(item["id"]): item
            for item in cache.tags()
            if item.get("id") is not None
        },
        instruments={
            str(item["id"]): item
            for item in cache.instruments()
            if item.get("id") is not None
        },
        transactions=[item for item in cache.transactions() if not _is_deleted(item)],
        reminders=[item for item in cache.reminders() if not _is_deleted(item)],
        markers=[item for item in cache.reminder_markers() if not _is_deleted(item)],
        budgets=budgets,
        today=today,
        mode_name=mode_name,
        plan_balance_mode=plan_balance_mode,
        plan_settings=frozenset(plan_settings),
        difference_calculation_mode=_resolve_difference_calculation_mode(
            args,
            cfg,
            plan_balance_mode,
        ),
        resolved_period=args["resolved_period"],
    )


def resolve_previous_billing_period(ctx: PlansContext) -> dict[str, Any]:
    current = ctx.resolved_period
    if current.get("period") != "billing_period":
        raise InvalidArgumentError("Future Plans opening requires billing_period")
    return periods.resolve_period(
        {
            "period": "billing_period",
            "period_offset": int(current.get("period_offset", 0)) - 1,
        },
        today=ctx.today,
        billing_start_day=current["billing_start_day"],
    )


def _resolve_policy(
    args: Mapping[str, Any],
    cfg: Mapping[str, Any],
    users: list[dict[str, Any]],
) -> tuple[str, str, set[str]]:
    mode_name = args.get("budget_mode") or cfg.get("budget_mode")
    selected_user: dict[str, Any] | None = None
    if mode_name:
        try:
            plan_balance_mode = PUBLIC_MODE_TO_ZM[mode_name]
        except KeyError as exc:
            raise InvalidArgumentError(
                f"Unknown configured budget_mode: {mode_name!r}"
            ) from exc
    else:
        selected_user = select_plan_user(users, cfg.get("plan_user_id"))
        plan_balance_mode = normalize_plan_balance_mode(
            selected_user.get("planBalanceMode") or "balance"
        )
        mode_name = (
            "income_vs_expense"
            if plan_balance_mode == EXCLUDE_OPENING_BALANCE
            else "balance_vs_expense"
        )
    if plan_balance_mode == BUDGET_LIMIT:
        raise InvalidArgumentError(
            "ZenMoney BUDGET_LIMIT has conflicting Plans consumers and is unsupported"
        )
    if "plan_settings_override" in cfg:
        plan_settings = parse_plan_settings(cfg["plan_settings_override"])
    elif plan_balance_mode == BALANCE:
        plan_settings = set()
    else:
        selected_user = selected_user or select_plan_user(users, cfg.get("plan_user_id"))
        plan_settings = parse_plan_settings(selected_user.get("planSettings"))
    if plan_balance_mode == BALANCE:
        plan_settings = set()
    return mode_name, plan_balance_mode, plan_settings


def _is_deleted(item: Mapping[str, Any]) -> bool:
    return (
        item.get("deleted") is True
        or item.get("isDeleted") is True
        or item.get("state") == "deleted"
        or item.get("status") == "deleted"
    )


def _resolve_difference_calculation_mode(
    args: Mapping[str, Any],
    cfg: Mapping[str, Any],
    plan_balance_mode: str,
) -> str:
    if plan_balance_mode == BALANCE:
        return "NONE"
    mode = args.get("difference_calculation_mode") or cfg.get(
        "difference_calculation_mode", "REFUNDS"
    )
    if mode not in {"REFUNDS", "INCOME_OUTCOME_AND_REFUNDS", "NONE"}:
        raise InvalidArgumentError(f"Unsupported category difference mode: {mode!r}")
    return str(mode)
