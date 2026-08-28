from __future__ import annotations

import json
from typing import Any

from . import cache as _cache
from . import config
from .config import _cfg_path
from .domain import (
    ALL_CATEGORIES_ID,
    _fmt_budget,
    _now_ts,
    _today,
)
from .errors import EntityNotFoundError, InvalidArgumentError
from .plans.context import build_context
from .plans.render import render_analysis
from .transfer_classifier import (
    BALANCE,
    PUBLIC_MODE_TO_ZM,
    parse_plan_settings,
    select_plan_user,
)
from .transport import _write_diff
from .validation import validate_tool_args


def _is_deleted_marker(marker: dict) -> bool:
    return marker.get("deleted") or marker.get("state") == "deleted"


def _is_planned_marker(marker: dict) -> bool:
    return not _is_deleted_marker(marker) and marker.get("state", "planned") == "planned"


def _is_processed_marker(marker: dict) -> bool:
    return not _is_deleted_marker(marker) and marker.get("state") == "processed"


def _budget_cache_key(user_id: Any, category_id: str | None, month_date: str) -> str:
    return _cache.Cache._budget_key({
        "user": user_id,
        "tag": category_id,
        "date": month_date,
    })


def _select_budget_user(*, allow_empty: bool = False) -> dict[str, Any]:
    with config.state_file_lock(_cfg_path):
        cfg = config.read_json_state(_cfg_path)
    user = select_plan_user(
        _cache.CACHE.users(),
        cfg.get("plan_user_id"),
        allow_empty=allow_empty,
    )
    if user.get("id") is None:
        if allow_empty:
            return {}
        raise InvalidArgumentError("Selected ZenMoney user has no id")
    return user


def _category_display_name(category_id: str | None, fallback: str | None = None) -> str:
    if category_id == ALL_CATEGORIES_ID:
        return "ALL (aggregate)"
    if category_id is None:
        return "Uncategorized"
    return (_cache.CACHE.get_tag(category_id) or {}).get("title", fallback or category_id)


async def tool_get_budgets(args: dict) -> str:
    args = validate_tool_args("get_budgets", args)
    month = args["month"]
    month_date = f"{month}-01"
    user_id = _select_budget_user()["id"]
    budgets = [
        budget
        for budget in _cache.CACHE.budgets()
        if budget.get("date") == month_date
        and str(budget.get("user")) == str(user_id)
    ]
    return json.dumps([_fmt_budget(b) for b in budgets], ensure_ascii=False)

async def tool_analyze_budget_detailed(args: dict) -> str:
    """Detailed ZenMoney Plans analysis."""
    args = validate_tool_args("analyze_budget_detailed", args)
    with config.state_file_lock(_cfg_path):
        cfg = config.read_json_state(_cfg_path)
    user = _select_budget_user(allow_empty=True)
    user_id = user.get("id")
    budgets_raw = [
        _fmt_budget(budget)
        for budget in _cache.CACHE.budgets()
        if user_id is not None and str(budget.get("user")) == str(user_id)
    ]
    ctx = build_context(
        args=args,
        cfg=cfg,
        cache=_cache.CACHE,
        budgets=budgets_raw,
        today=_today(),
    )
    return json.dumps(
        render_analysis(ctx),
        ensure_ascii=False,
        indent=2,
    )


async def tool_setup_budget_mode(args: dict) -> str:
    """Setup budget mode configuration (balance_vs_expense or income_vs_expense)."""
    args = validate_tool_args("setup_budget_mode", args)
    mode = args["mode"]
    difference_calculation_mode = args.get("difference_calculation_mode")

    cfg = config.setup_budget_mode_config(mode, difference_calculation_mode)
    plan_balance_mode = PUBLIC_MODE_TO_ZM[mode]
    configured_difference_mode = cfg.get("difference_calculation_mode", "REFUNDS")
    effective_difference_mode = (
        "NONE" if plan_balance_mode == BALANCE else configured_difference_mode
    )
    if plan_balance_mode == BALANCE:
        plan_settings = set()
        settings_source = "inactive_in_balance_mode"
    elif "plan_settings_override" in cfg:
        plan_settings = parse_plan_settings(cfg["plan_settings_override"])
        settings_source = "local_override"
    else:
        user = select_plan_user(
            _cache.CACHE.users(),
            cfg.get("plan_user_id"),
            allow_empty=True,
        )
        if user:
            plan_settings = parse_plan_settings(user.get("planSettings"))
            settings_source = "zenmoney_user"
        else:
            plan_settings = None
            settings_source = "unavailable_no_synced_user"

    return json.dumps({
        "success": True,
        "mode": mode,
        "plan_balance_mode": plan_balance_mode,
        "plan_settings": sorted(plan_settings) if plan_settings is not None else None,
        "settings_source": settings_source,
        "configured_difference_calculation_mode": configured_difference_mode,
        "difference_calculation_mode": effective_difference_mode,
        "message": "Режим Plans сохранён; transfer-настройки читаются из ZenMoney",
    }, ensure_ascii=False, indent=2)

async def tool_create_budget(args: dict) -> str:
    args = validate_tool_args("create_budget", args)
    month = args["month"]
    category = args["category"]
    income = args["income"]
    outcome = args["outcome"]
    income_lock = args["income_lock"]
    outcome_lock = args["outcome_lock"]

    category_id = args["category_id"]
    month_date = f"{month}-01"

    user = _select_budget_user()

    budget: dict[str, Any] = {
        "user": user["id"],
        "changed": _now_ts(),
        "tag": category_id,
        "date": month_date,
        "income": income,
        "incomeLock": income_lock,
        "outcome": outcome,
        "outcomeLock": outcome_lock,
    }

    await _write_diff({"budget": [budget]})
    cat_name = _category_display_name(category_id, category)
    return json.dumps({
        "success": True,
        "budget": {
            "month": month, "category": cat_name, "category_id": category_id,
            "income": income, "outcome": outcome,
            "income_lock": income_lock, "outcome_lock": outcome_lock,
        },
    }, ensure_ascii=False)


async def tool_update_budget(args: dict) -> str:
    args = validate_tool_args("update_budget", args)
    month = args["month"]
    category = args["category"]

    category_id = args["category_id"]
    month_date = f"{month}-01"
    user_id = _select_budget_user()["id"]
    budget_key = _budget_cache_key(user_id, category_id, month_date)

    existing = _cache.CACHE.data["budget"].get(budget_key)
    if not existing:
        raise EntityNotFoundError(f'Budget not found for category "{category}" in {month}. Use create_budget to create.')

    updated = {**existing, "changed": _now_ts()}
    if "income" in args:
        updated["income"] = args["income"]
    if "outcome" in args:
        updated["outcome"] = args["outcome"]
    if "income_lock" in args:
        updated["incomeLock"] = args["income_lock"]
    if "outcome_lock" in args:
        updated["outcomeLock"] = args["outcome_lock"]

    await _write_diff({"budget": [updated]})
    cat_name = _category_display_name(category_id, category)
    return json.dumps({
        "success": True, "message": "Budget updated",
        "budget": {
            "month": month, "category": cat_name,
            "income": updated["income"], "outcome": updated["outcome"],
            "income_lock": updated.get("incomeLock", False),
            "outcome_lock": updated.get("outcomeLock", False),
        },
    }, ensure_ascii=False)


async def tool_delete_budget(args: dict) -> str:
    args = validate_tool_args("delete_budget", args)
    month = args["month"]
    category = args["category"]

    category_id = args["category_id"]
    month_date = f"{month}-01"
    user_id = _select_budget_user()["id"]
    budget_key = _budget_cache_key(user_id, category_id, month_date)

    existing = _cache.CACHE.data["budget"].get(budget_key)
    if not existing:
        raise EntityNotFoundError(f'Budget not found for category "{category}" in {month}.')

    deleted = {
        **existing,
        "changed": _now_ts(),
        "income": 0,
        "outcome": 0,
        "incomeLock": False,
        "outcomeLock": False,
    }
    await _write_diff({"budget": [deleted]})
    cat_name = _category_display_name(category_id, category)
    return json.dumps({"success": True, "message": "Budget deleted", "category": cat_name, "month": month}, ensure_ascii=False)
