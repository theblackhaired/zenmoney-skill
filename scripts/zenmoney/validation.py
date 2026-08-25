from __future__ import annotations

import datetime
import math
import secrets
import unicodedata
from typing import Any

from . import cache as _cache
from . import config
from . import periods
from .domain import (
    ALL_CATEGORIES_ID,
    _BUDGET_MODE_DEFAULTS,
    _find_category_id,
    _g,
    _get_bool_arg,
    _today,
    _validate_date,
    _validate_month,
    _validate_positive,
    _validate_strict_positive,
    _validate_uuid,
)
from .errors import (
    EntityNotFoundError,
    InvalidArgumentError,
    InvalidBoolError,
    InvalidCategoryError,
    InvalidDateError,
    InvalidDateRangeError,
    InvalidMonthError,
    InvalidUUIDError,
    ToolError,
    UnsupportedCalculationError,
    UnsupportedCategoryFilterError,
)


_VALIDATED_TOOL_KEY = "__validated_for_tool__"
_VALIDATED_TOOL_TOKEN = secrets.token_hex(8)
_VALIDATED_TOOL_VALUE_PREFIX = f"internal:{_VALIDATED_TOOL_TOKEN}:"
_TRANSACTION_TYPES = {"expense", "income", "transfer"}
_TRANSACTION_FILTER_TYPES = _TRANSACTION_TYPES
_REMINDER_FILTER_TYPES = _TRANSACTION_TYPES | {"all"}
_ANALYTICS_REPORTS = {"income", "outcome", "net"}
_ANALYTICS_GROUP_BY = {"category", "account", "merchant"}
_ANALYTICS_CURRENCY_MODES = {"split", "scalar"}
_ANALYTICS_ACCOUNT_SCOPES = {"all", "in_balance", "selected"}
_ANALYTICS_CATEGORY_SCOPES = {"all", "selected"}
_ANALYTICS_CATEGORY_ROLES = {"primary", "additional", "any"}
_ANALYTICS_MERCHANT_SCOPES = {"all", "selected"}
_PERIOD_ARGUMENTS = {
    "period",
    "period_offset",
    "first_weekday",
    "start_date",
    "end_date",
}
_TRANSACTION_ARGUMENTS = _PERIOD_ARGUMENTS | {
    "account_id",
    "category_id",
    "type",
    "limit",
    "offset",
}
_ANALYTICS_ARGUMENTS = _PERIOD_ARGUMENTS | {
    "report",
    "group_by",
    "currency_mode",
    "account_scope",
    "account_ids",
    "category_scope",
    "category_ids",
    "category_role",
    "merchant_scope",
    "merchant_ids",
    "payees",
}
_PLANS_ARGUMENTS = {
    "period",
    "period_offset",
    "budget_mode",
    "show_forecast",
    "show_calendar",
}
_ACCOUNT_TYPES = {"cash", "ccard", "checking"}
_REMINDER_INTERVALS = {"day", "week", "month", "year"}


def _ensure_not_empty_string(value: Any, key: str, *, error_cls: type[ToolError]) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise error_cls(f"{key} must not be empty")
    return value


def get_bool_arg(args: dict, key: str, default: bool) -> bool:
    try:
        return _get_bool_arg(args, key, default)
    except ValueError as exc:
        raise InvalidBoolError(str(exc)) from exc


def get_int_arg(args: dict, key: str, default: int) -> int:
    raw = _g(key, args, default)
    if type(raw) is bool or (isinstance(raw, float) and not raw.is_integer()):
        raise InvalidArgumentError(f"{key} must be an integer, got {raw!r}")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise InvalidArgumentError(f"{key} must be an integer, got {raw!r}") from None


def get_float_arg(args: dict, key: str, default: float) -> float:
    value = _coerce_finite_float(_g(key, args, default), key)
    assert value is not None
    return value


def get_non_negative_int_arg(args: dict, key: str, default: int) -> int:
    value = get_int_arg(args, key, default)
    if value < 0:
        raise InvalidArgumentError(f"{key} must be non-negative, got {value}")
    return value


def get_strict_positive_int_arg(args: dict, key: str, default: int) -> int:
    value = get_int_arg(args, key, default)
    if value <= 0:
        raise InvalidArgumentError(f"{key} must be positive, got {value}")
    return value


def get_enum_arg(args: dict, key: str, allowed: set[str], *, default: str | None = None) -> str | None:
    value = _g(key, args, default)
    if value is None:
        return None
    value = _ensure_not_empty_string(value, key, error_cls=InvalidArgumentError)
    if not isinstance(value, str):
        raise InvalidArgumentError(f"{key} must be a string, got {type(value).__name__}")
    if value not in allowed:
        raise InvalidArgumentError(f"Invalid {key}: {value}. Allowed values: {', '.join(sorted(allowed))}")
    return value


def get_points_arg(args: dict, key: str, *, step: int | None = None) -> list[int] | None:
    if key not in args:
        return None
    values = args[key]
    if not isinstance(values, list):
        raise InvalidArgumentError(f"{key} must be a list of integers")
    if not values:
        raise InvalidArgumentError(f"{key} must contain at least one recurrence offset")
    normalized: list[int] = []
    for i, value in enumerate(values):
        if type(value) is not int:
            raise InvalidArgumentError(f"{key}[{i}] must be an integer, got {value!r}")
        if value < 0:
            raise InvalidArgumentError(f"{key}[{i}] must be non-negative, got {value}")
        if step is not None and value >= step:
            raise InvalidArgumentError(f"{key}[{i}] must be less than step ({step}), got {value}")
        normalized.append(value)
    return normalized


def get_month_arg(args: dict, key: str = "month") -> str:
    value = _ensure_not_empty_string(args[key], key, error_cls=InvalidMonthError)
    try:
        _validate_month(value, key)
        datetime.date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise InvalidMonthError(str(exc)) from exc
    return value


def get_required_uuid_arg(args: dict, key: str) -> str:
    value = _ensure_not_empty_string(args[key], key, error_cls=InvalidUUIDError)
    try:
        _validate_uuid(value, key)
    except ValueError as exc:
        raise InvalidUUIDError(str(exc)) from exc
    return value


def get_optional_uuid_arg(args: dict, key: str) -> str | None:
    value = _g(key, args)
    if value is None:
        return None
    value = _ensure_not_empty_string(value, key, error_cls=InvalidUUIDError)
    if value:
        try:
            _validate_uuid(value, key)
        except ValueError as exc:
            raise InvalidUUIDError(str(exc)) from exc
    return value


def get_optional_uuid_list_arg(args: dict, key: str, *, item_label: str | None = None) -> list[str] | None:
    values = _g(key, args)
    if not values:
        return values
    for i, value in enumerate(values):
        field = item_label or f"{key}[{i}]"
        value = _ensure_not_empty_string(value, field, error_cls=InvalidUUIDError)
        try:
            _validate_uuid(value, field)
        except ValueError as exc:
            raise InvalidUUIDError(str(exc)) from exc
    return values


def get_analytics_selector_ids(args: dict, key: str) -> list[str]:
    if key not in args:
        return []
    values = args[key]
    if not isinstance(values, list):
        raise InvalidArgumentError(f"{key} must be a list of UUID strings")
    normalized: list[str] = []
    for i, value in enumerate(values):
        field = f"{key}[{i}]"
        if not isinstance(value, str):
            raise InvalidUUIDError(f"{field} must be a UUID string")
        value = _ensure_not_empty_string(value, field, error_cls=InvalidUUIDError)
        try:
            _validate_uuid(value, field)
        except ValueError as exc:
            raise InvalidUUIDError(str(exc)) from exc
        normalized.append(value)
    return sorted(set(normalized))


def get_analytics_payees(args: dict) -> list[str]:
    if "payees" not in args:
        return []
    values = args["payees"]
    if not isinstance(values, list):
        raise InvalidArgumentError("payees must be a list of non-empty strings")
    normalized: list[str] = []
    for i, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise InvalidArgumentError(f"payees[{i}] must be a non-empty string")
        normalized.append(unicodedata.normalize("NFC", value))
    return sorted(set(normalized))


def get_optional_date_arg(args: dict, key: str) -> str | None:
    value = _g(key, args)
    if value is None:
        return None
    value = _ensure_not_empty_string(value, key, error_cls=InvalidDateError)
    if value:
        try:
            _validate_date(value, key)
            datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidDateError(str(exc)) from exc
    return value


def get_date_arg_or_today(args: dict, key: str = "date") -> str:
    if key in args:
        value = _ensure_not_empty_string(_g(key, args), key, error_cls=InvalidDateError)
        try:
            _validate_date(value, key)
            datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidDateError(str(exc)) from exc
        return value
    return _today()


def _billing_start_day() -> int:
    cfg = config._load_config()
    if "billing_period_start_day" not in cfg:
        raise InvalidArgumentError(
            "billing_period_start_day is required in config.json for period=billing_period"
        )
    raw = cfg["billing_period_start_day"]
    if type(raw) is not int or not 1 <= raw <= 31:
        raise InvalidArgumentError("billing_period_start_day must be an integer from 1 to 31")
    return raw


def normalize_strict_period(args: dict) -> dict:
    request = {
        key: args[key]
        for key in ("period", "period_offset", "start_date", "end_date")
        if key in args
    }
    first_weekday = args.get("first_weekday")
    if "first_weekday" in args and args.get("period") != "week":
        raise InvalidArgumentError("first_weekday is valid only when period=week")
    if args.get("period") == "week" and first_weekday is None:
        raise InvalidArgumentError("first_weekday is required when period=week")
    billing_start_day = _billing_start_day() if request.get("period") == "billing_period" else 1
    try:
        return periods.resolve_period(
            request,
            today=_today(),
            billing_start_day=billing_start_day,
            first_weekday=first_weekday,
        )
    except periods.InvalidPeriodSelectorError as exc:
        raise InvalidArgumentError(str(exc)) from exc
    except periods.InvalidPeriodValueError as exc:
        if "on or before" in str(exc):
            raise InvalidDateRangeError(str(exc)) from exc
        if "must be an ISO date" in str(exc):
            raise InvalidDateError(str(exc)) from exc
        raise InvalidArgumentError(str(exc)) from exc
    except (OverflowError, ValueError) as exc:
        raise InvalidArgumentError(f"period is outside the supported date range: {exc}") from exc


def _coerce_finite_float(value: Any, key: str) -> float | None:
    if value is None or type(value) is bool:
        raise InvalidArgumentError(f"{key} must be a finite number, got {value!r}")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise InvalidArgumentError(f"{key} must be a finite number, got {value!r}") from None
    if not math.isfinite(result):
        raise InvalidArgumentError(f"{key} must be a finite number, got {value!r}")
    return result


def get_float_arg_validated(
    args: dict,
    key: str,
    *,
    default: float | None = None,
    strict_positive: bool = False,
    non_negative: bool = False,
) -> float | None:
    if key not in args:
        return default
    value = _coerce_finite_float(args[key], key)
    if strict_positive:
        try:
            _validate_strict_positive(value, key)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
    if non_negative:
        try:
            _validate_positive(value, key)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
    return value


def get_marker_range(args: dict, start_key: str = "marker_from", end_key: str = "marker_to") -> tuple[str | None, str | None]:
    start = _g(start_key, args)
    end = _g(end_key, args)

    if start is not None:
        start = _ensure_not_empty_string(start, start_key, error_cls=InvalidDateRangeError)
    if end is not None:
        end = _ensure_not_empty_string(end, end_key, error_cls=InvalidDateRangeError)

    if start:
        try:
            _validate_date(start, start_key)
            datetime.date.fromisoformat(start)
        except ValueError as exc:
            raise InvalidDateRangeError(str(exc)) from exc
    if end:
        try:
            _validate_date(end, end_key)
            datetime.date.fromisoformat(end)
        except ValueError as exc:
            raise InvalidDateRangeError(str(exc)) from exc
    if bool(start) != bool(end):
        raise InvalidDateRangeError(f"{start_key} and {end_key} must both be provided, or neither")
    if start and end and start > end:
        raise InvalidDateRangeError(f"{start_key} must be on or before {end_key}")
    return start, end


def resolve_category_arg(args: dict, key: str = "category", *, allow_all: bool = False) -> str:
    return _find_category_id(args[key], allow_all=allow_all)


def require_entity(entity: str, entity_id: str, message: str) -> dict:
    existing = _cache.CACHE.get(entity, entity_id)
    if not existing:
        raise EntityNotFoundError(message)
    return existing


def require_account(account_id: str, field: str = "account_id") -> dict:
    account = _cache.CACHE.get_account(account_id)
    if not account:
        raise EntityNotFoundError(f"Account not found: {account_id}")
    return account


def require_optional_account(account_id: str | None, field: str = "account_id") -> dict | None:
    if not account_id:
        return None
    return require_account(account_id, field)


def require_category_ids_exist(category_ids: list[str] | None) -> None:
    if not category_ids:
        return
    for category_id in category_ids:
        if not _cache.CACHE.get_tag(category_id):
            raise InvalidCategoryError(f"Category not found: {category_id}")


def require_first_user() -> dict:
    user = _cache.CACHE.first_user()
    if not user:
        raise EntityNotFoundError("No user found in cache")
    return user


def require_user_or_account_owner() -> dict:
    user = _cache.CACHE.first_user()
    if user:
        return user
    accounts = _cache.CACHE.accounts()
    if not accounts:
        raise EntityNotFoundError("No accounts found. Cannot determine user ID.")
    return {"id": accounts[0].get("user")}


def validate_tool_args(name: str, args: dict) -> dict:
    """Validate and normalize args for the given tool.

    Idempotent: re-validating the returned normalized dict short-circuits via
    _VALIDATED_TOOL_KEY (the marker is written onto the returned dict, not the
    input). Dispatch (`dispatch.run_tool`) validates upstream and passes the
    normalized result to the handler; handlers then re-call this function so
    direct invocation in tests is also validated. The marker token is
    process-local (see `_VALIDATED_TOOL_TOKEN`), so a forged
    `__validated_for_tool__` value from external input cannot bypass validation.
    """
    if args.get(_VALIDATED_TOOL_KEY) == f"{_VALIDATED_TOOL_VALUE_PREFIX}{name}":
        return args

    normalized = dict(args)
    normalized.pop(_VALIDATED_TOOL_KEY, None)

    if name == "get_accounts":
        normalized["include_archived"] = get_bool_arg(normalized, "include_archived", False)
    elif name == "get_instruments":
        normalized["include_all"] = get_bool_arg(normalized, "include_all", False)
    elif name == "get_budgets":
        normalized["month"] = get_month_arg(normalized)
    elif name == "get_transactions":
        unknown_arguments = sorted(set(normalized) - _TRANSACTION_ARGUMENTS)
        if unknown_arguments:
            raise InvalidArgumentError(
                f"Unknown get_transactions arguments: {', '.join(unknown_arguments)}",
                {
                    "unknown_arguments": unknown_arguments,
                    "accepted_arguments": sorted(_TRANSACTION_ARGUMENTS),
                },
            )
        resolved_period = normalize_strict_period(normalized)
        normalized["start_date"] = resolved_period["start_date"]
        normalized["end_date"] = resolved_period["end_date"]
        normalized["resolved_period"] = resolved_period
        normalized["account_id"] = get_optional_uuid_arg(normalized, "account_id")
        normalized["category_id"] = get_optional_uuid_arg(normalized, "category_id")
        normalized["type"] = get_enum_arg(normalized, "type", _TRANSACTION_FILTER_TYPES)
        normalized["limit"] = min(get_non_negative_int_arg(normalized, "limit", 100), 500)
        normalized["offset"] = get_non_negative_int_arg(normalized, "offset", 0)
    elif name == "get_reminders":
        normalized["include_processed"] = get_bool_arg(normalized, "include_processed", False)
        normalized["active_only"] = get_bool_arg(normalized, "active_only", True)
        normalized["limit"] = get_non_negative_int_arg(normalized, "limit", 50)
        normalized["markers_limit"] = get_non_negative_int_arg(normalized, "markers_limit", 5)
        normalized["offset"] = get_non_negative_int_arg(normalized, "offset", 0)
        normalized["type"] = get_enum_arg(normalized, "type", _REMINDER_FILTER_TYPES, default="all")
        if "category_id" in normalized and "category" not in normalized:
            raise InvalidArgumentError("Use 'category' field instead of 'category_id' for get_reminders")
        marker_from, marker_to = get_marker_range(normalized)
        if marker_from is not None:
            normalized["marker_from"] = marker_from
        if marker_to is not None:
            normalized["marker_to"] = marker_to
        if "category" in normalized:
            category_id = resolve_category_arg(normalized, allow_all=True)
            if category_id == ALL_CATEGORIES_ID:
                raise UnsupportedCategoryFilterError("Category filter 'ALL' is not supported by get_reminders")
            normalized["category_id"] = category_id
    elif name == "get_analytics":
        for removed_argument in ("type", "metric", "scalar_total"):
            if removed_argument not in normalized:
                continue
            removed_value = normalized[removed_argument]
            if removed_argument == "type":
                accepted_values = {
                    "expense": "outcome",
                    "income": "income",
                    "net": "net",
                    "all": None,
                }
                mapped_report = accepted_values.get(removed_value)
                migration = f" use report={mapped_report}" if mapped_report else " no direct replacement is available"
                replacement = "report"
            elif removed_argument == "metric":
                accepted_values = sorted(_ANALYTICS_REPORTS)
                migration = f" use report={removed_value}" if removed_value in _ANALYTICS_REPORTS else " use report"
                replacement = "report"
            else:
                accepted_values = {False: "split", True: "scalar"}
                mapped_mode = "scalar" if removed_value else "split"
                migration = f" use currency_mode={mapped_mode}"
                replacement = "currency_mode"
            raise InvalidArgumentError(
                f"Removed argument '{removed_argument}' is not supported;{migration}",
                {
                    "removed_argument": removed_argument,
                    "replacement": replacement,
                    "accepted_values": accepted_values,
                },
            )
        removed_filter_aliases = {
            "account_id": "account_scope=selected with account_ids",
            "category_id": "category_scope=selected with category_ids",
            "merchant_id": "merchant_scope=selected with merchant_ids",
            "payee": "merchant_scope=selected with payees",
        }
        for removed_argument, replacement in removed_filter_aliases.items():
            if removed_argument in normalized:
                raise InvalidArgumentError(
                    f"Removed argument '{removed_argument}' is not supported; use {replacement}",
                    {"removed_argument": removed_argument, "replacement": replacement},
                )
        unknown_arguments = sorted(set(normalized) - _ANALYTICS_ARGUMENTS)
        if unknown_arguments:
            raise InvalidArgumentError(
                f"Unknown get_analytics arguments: {', '.join(unknown_arguments)}",
                {
                    "unknown_arguments": unknown_arguments,
                    "accepted_arguments": sorted(_ANALYTICS_ARGUMENTS),
                },
            )
        resolved_period = normalize_strict_period(normalized)
        normalized["start_date"] = resolved_period["start_date"]
        normalized["end_date"] = resolved_period["end_date"]
        normalized["resolved_period"] = resolved_period
        report = normalized.get("report")
        if report == "turnover":
            raise UnsupportedCalculationError(
                "report=turnover is reserved until the money-movement contract is implemented",
                {"report": report, "supported_reports": sorted(_ANALYTICS_REPORTS)},
            )
        if report is None:
            raise InvalidArgumentError(
                "report is required; accepted values: income, outcome, net",
                {"accepted_values": sorted(_ANALYTICS_REPORTS)},
            )
        normalized["report"] = get_enum_arg(normalized, "report", _ANALYTICS_REPORTS)
        normalized["group_by"] = get_enum_arg(normalized, "group_by", _ANALYTICS_GROUP_BY, default="category")
        normalized["currency_mode"] = get_enum_arg(
            normalized,
            "currency_mode",
            _ANALYTICS_CURRENCY_MODES,
            default="split",
        )

        normalized["account_scope"] = get_enum_arg(
            normalized,
            "account_scope",
            _ANALYTICS_ACCOUNT_SCOPES,
            default="in_balance",
        )
        account_ids_present = "account_ids" in normalized
        normalized["account_ids"] = get_analytics_selector_ids(normalized, "account_ids")
        if normalized["account_scope"] == "selected":
            if not normalized["account_ids"]:
                raise InvalidArgumentError("account_ids must be non-empty when account_scope=selected")
            unknown_ids = [
                account_id
                for account_id in normalized["account_ids"]
                if not _cache.CACHE.get_account(account_id)
            ]
            if unknown_ids:
                raise EntityNotFoundError(
                    "Selected analytics accounts were not found",
                    {"entity_type": "account", "ids": unknown_ids},
                )
        elif account_ids_present:
            raise InvalidArgumentError("account_ids is valid only when account_scope=selected")

        normalized["category_scope"] = get_enum_arg(
            normalized,
            "category_scope",
            _ANALYTICS_CATEGORY_SCOPES,
            default="all",
        )
        category_role_present = "category_role" in normalized
        normalized["category_role"] = get_enum_arg(
            normalized,
            "category_role",
            _ANALYTICS_CATEGORY_ROLES,
            default="any",
        )
        category_ids_present = "category_ids" in normalized
        normalized["category_ids"] = get_analytics_selector_ids(normalized, "category_ids")
        if normalized["category_scope"] == "selected":
            if not normalized["category_ids"]:
                raise InvalidArgumentError("category_ids must be non-empty when category_scope=selected")
            unknown_ids = [
                category_id
                for category_id in normalized["category_ids"]
                if not _cache.CACHE.get_tag(category_id)
            ]
            if unknown_ids:
                raise EntityNotFoundError(
                    "Selected analytics categories were not found",
                    {"entity_type": "category", "ids": unknown_ids},
                )
        elif category_ids_present:
            raise InvalidArgumentError("category_ids is valid only when category_scope=selected")
        elif category_role_present:
            raise InvalidArgumentError("category_role is valid only when category_scope=selected")

        normalized["merchant_scope"] = get_enum_arg(
            normalized,
            "merchant_scope",
            _ANALYTICS_MERCHANT_SCOPES,
            default="all",
        )
        merchant_ids_present = "merchant_ids" in normalized
        payees_present = "payees" in normalized
        normalized["merchant_ids"] = get_analytics_selector_ids(normalized, "merchant_ids")
        normalized["payees"] = get_analytics_payees(normalized)
        if normalized["merchant_scope"] == "selected":
            if not normalized["merchant_ids"] and not normalized["payees"]:
                raise InvalidArgumentError(
                    "merchant_ids or payees must be non-empty when merchant_scope=selected"
                )
            unknown_ids = [
                merchant_id
                for merchant_id in normalized["merchant_ids"]
                if not _cache.CACHE.get_merchant(merchant_id)
            ]
            if unknown_ids:
                raise EntityNotFoundError(
                    "Selected analytics merchants were not found",
                    {"entity_type": "merchant", "ids": unknown_ids},
                )
        elif merchant_ids_present or payees_present:
            raise InvalidArgumentError(
                "merchant_ids and payees are valid only when merchant_scope=selected"
            )
    elif name == "get_merchants":
        normalized["limit"] = get_non_negative_int_arg(normalized, "limit", 50)
        normalized["offset"] = get_non_negative_int_arg(normalized, "offset", 0)
    elif name == "setup_budget_mode":
        mode = _g("mode", normalized)
        if not mode:
            raise InvalidArgumentError("Parameter 'mode' is required")
        if mode not in ["balance_vs_expense", "income_vs_expense"]:
            raise InvalidArgumentError(
                f"Invalid mode: {mode}. Must be 'balance_vs_expense' or 'income_vs_expense'"
            )
        normalized["mode"] = mode
    elif name == "analyze_budget_detailed":
        unknown_arguments = sorted(set(normalized) - _PLANS_ARGUMENTS)
        if unknown_arguments:
            raise InvalidArgumentError(
                f"Unknown analyze_budget_detailed arguments: {', '.join(unknown_arguments)}",
                {
                    "unknown_arguments": unknown_arguments,
                    "accepted_arguments": sorted(_PLANS_ARGUMENTS),
                },
            )
        normalized["show_forecast"] = get_bool_arg(normalized, "show_forecast", True)
        normalized["show_calendar"] = get_bool_arg(normalized, "show_calendar", True)
        if normalized.get("period") != "billing_period":
            raise InvalidArgumentError("analyze_budget_detailed requires period=billing_period")
        if "budget_mode" in normalized:
            mode = normalized["budget_mode"]
            if mode not in _BUDGET_MODE_DEFAULTS:
                raise InvalidArgumentError(
                    f"Unknown budget_mode: {mode}. Available: {sorted(_BUDGET_MODE_DEFAULTS)}"
                )
        resolved_period = normalize_strict_period(normalized)
        normalized["start_date"] = resolved_period["start_date"]
        normalized["end_date"] = resolved_period["end_date"]
        normalized["resolved_period"] = resolved_period
    elif name == "suggest":
        payee = _g("payee", normalized)
        if not payee:
            raise InvalidArgumentError("Missing required argument: payee")
        normalized["payee"] = payee
    elif name in {"create_budget", "update_budget", "delete_budget"}:
        normalized["month"] = get_month_arg(normalized)
        if "category" in normalized:
            normalized["category_id"] = resolve_category_arg(normalized, allow_all=True)
        if name in {"create_budget", "update_budget"}:
            if "income" in normalized or name == "create_budget":
                normalized["income"] = get_float_arg_validated(normalized, "income", default=0, non_negative=True)
            if "outcome" in normalized or name == "create_budget":
                normalized["outcome"] = get_float_arg_validated(normalized, "outcome", default=0, non_negative=True)
            if "income_lock" in normalized or name == "create_budget":
                normalized["income_lock"] = get_bool_arg(normalized, "income_lock", False)
            if "outcome_lock" in normalized or name == "create_budget":
                normalized["outcome_lock"] = get_bool_arg(normalized, "outcome_lock", False)
    elif name == "create_account":
        title = normalized.get("title")
        if not title:
            raise InvalidArgumentError("Missing required argument: title")
        acct_type = normalized.get("type")
        if not acct_type:
            raise InvalidArgumentError("Missing required argument: type")
        normalized["type"] = get_enum_arg(normalized, "type", _ACCOUNT_TYPES)
        if "currency_id" not in normalized:
            raise InvalidArgumentError("Missing required argument: currency_id")
        if (
            type(normalized["currency_id"]) is bool
            or (
                isinstance(normalized["currency_id"], float)
                and not normalized["currency_id"].is_integer()
            )
        ):
            raise InvalidArgumentError(
                f"currency_id must be an integer, got {normalized['currency_id']!r}"
            )
        try:
            normalized["currency_id"] = int(normalized["currency_id"])
        except (TypeError, ValueError):
            raise InvalidArgumentError(
                f"currency_id must be an integer, got {normalized['currency_id']!r}"
            ) from None
        normalized["balance"] = get_float_arg(normalized, "balance", 0)
        normalized["credit_limit"] = get_float_arg_validated(normalized, "credit_limit", default=0, non_negative=True)
    elif name == "create_transaction":
        if "type" not in normalized:
            raise InvalidArgumentError("Missing required argument: type")
        normalized["type"] = get_enum_arg(normalized, "type", _TRANSACTION_TYPES)
        if "amount" not in normalized:
            raise KeyError("amount")
        normalized["amount"] = get_float_arg_validated(normalized, "amount", strict_positive=True)
        normalized["account_id"] = get_required_uuid_arg(normalized, "account_id")
        normalized["to_account_id"] = get_optional_uuid_arg(normalized, "to_account_id")
        normalized["category_ids"] = get_optional_uuid_list_arg(normalized, "category_ids")
        normalized["date"] = get_date_arg_or_today(normalized)
        normalized["income_amount"] = get_float_arg_validated(normalized, "income_amount", strict_positive=True)
        if "currency_id" in normalized and normalized["currency_id"] is not None:
            if (
                type(normalized["currency_id"]) is bool
                or (
                    isinstance(normalized["currency_id"], float)
                    and not normalized["currency_id"].is_integer()
                )
            ):
                raise InvalidArgumentError(
                    f"currency_id must be an integer, got {normalized['currency_id']!r}"
                )
            try:
                normalized["currency_id"] = int(normalized["currency_id"])
            except (TypeError, ValueError):
                raise InvalidArgumentError(
                    f"currency_id must be an integer, got {normalized['currency_id']!r}"
                ) from None
    elif name == "update_transaction":
        normalized["id"] = get_required_uuid_arg(normalized, "id")
        if "date" in normalized:
            normalized["date"] = get_optional_date_arg(normalized, "date")
        if "category_ids" in normalized:
            normalized["category_ids"] = get_optional_uuid_list_arg(normalized, "category_ids")
        if "amount" in normalized:
            normalized["amount"] = get_float_arg_validated(normalized, "amount", strict_positive=True)
    elif name in {"delete_transaction", "delete_reminder", "delete_reminder_marker"}:
        normalized["id"] = get_required_uuid_arg(normalized, "id")
    elif name == "create_reminder":
        if "type" not in normalized:
            raise InvalidArgumentError("Missing required argument: type")
        normalized["type"] = get_enum_arg(normalized, "type", _TRANSACTION_TYPES)
        if "interval" not in normalized:
            raise InvalidArgumentError("Missing required argument: interval")
        normalized["interval"] = get_enum_arg(normalized, "interval", _REMINDER_INTERVALS)
        if "amount" not in normalized:
            raise KeyError("amount")
        normalized["amount"] = get_float_arg_validated(normalized, "amount", strict_positive=True)
        normalized["account_id"] = get_required_uuid_arg(normalized, "account_id")
        normalized["to_account_id"] = get_optional_uuid_arg(normalized, "to_account_id")
        normalized["category_ids"] = get_optional_uuid_list_arg(normalized, "category_ids", item_label="category_id")
        if "start_date" in normalized:
            try:
                _validate_date(normalized["start_date"], "start_date")
                datetime.date.fromisoformat(normalized["start_date"])
            except ValueError as exc:
                raise InvalidDateError(str(exc)) from exc
        if "end_date" in normalized:
            normalized["end_date"] = get_optional_date_arg(normalized, "end_date")
        if "start_date" in normalized and normalized.get("end_date") and normalized["start_date"] > normalized["end_date"]:
            raise InvalidDateRangeError("start_date must be on or before end_date")
        normalized["notify"] = get_bool_arg(normalized, "notify", True)
        normalized["generate_markers"] = get_non_negative_int_arg(normalized, "generate_markers", 12)
        normalized["step"] = get_strict_positive_int_arg(normalized, "step", 1)
        if "points" in normalized:
            normalized["points"] = get_points_arg(normalized, "points", step=normalized["step"])
    elif name == "update_reminder":
        normalized["id"] = get_required_uuid_arg(normalized, "id")
        if "category_ids" in normalized:
            normalized["category_ids"] = get_optional_uuid_list_arg(normalized, "category_ids", item_label="category_id")
        if "interval" in normalized:
            normalized["interval"] = get_enum_arg(normalized, "interval", _REMINDER_INTERVALS)
        if "end_date" in normalized:
            normalized["end_date"] = get_optional_date_arg(normalized, "end_date")
        if "notify" in normalized:
            normalized["notify"] = get_bool_arg(normalized, "notify", True)
        if "amount" in normalized:
            normalized["amount"] = get_float_arg_validated(normalized, "amount", strict_positive=True)
        if "step" in normalized:
            normalized["step"] = get_strict_positive_int_arg(normalized, "step", 1)
        if "points" in normalized:
            normalized["points"] = get_points_arg(normalized, "points", step=normalized.get("step"))
        if "regenerate_markers" in normalized:
            normalized["regenerate_markers"] = get_non_negative_int_arg(normalized, "regenerate_markers", 12)
    elif name == "create_reminder_marker":
        if "type" not in normalized:
            raise InvalidArgumentError("Missing required argument: type")
        normalized["type"] = get_enum_arg(normalized, "type", _TRANSACTION_TYPES)
        if "amount" not in normalized:
            raise KeyError("amount")
        normalized["amount"] = get_float_arg_validated(normalized, "amount", strict_positive=True)
        normalized["account_id"] = get_required_uuid_arg(normalized, "account_id")
        normalized["to_account_id"] = get_optional_uuid_arg(normalized, "to_account_id")
        normalized["category_ids"] = get_optional_uuid_list_arg(normalized, "category_ids", item_label="category_id")
        normalized["reminder_id"] = get_optional_uuid_arg(normalized, "reminder_id")
        try:
            _validate_date(normalized["date"], "date")
            datetime.date.fromisoformat(normalized["date"])
        except ValueError as exc:
            raise InvalidDateError(str(exc)) from exc
        normalized["notify"] = get_bool_arg(normalized, "notify", True)
    elif name == "check_auth_status":
        pass

    normalized[_VALIDATED_TOOL_KEY] = f"{_VALIDATED_TOOL_VALUE_PREFIX}{name}"
    return normalized


def map_exception(exc: Exception) -> ToolError:
    if isinstance(exc, ToolError):
        return exc

    if isinstance(exc, KeyError):
        missing_key = exc.args[0] if exc.args else "unknown"
        return InvalidArgumentError(f"Missing required argument: {missing_key}")

    if isinstance(exc, ValueError):
        return InvalidArgumentError(str(exc))
    return ToolError("INTERNAL_ERROR", str(exc))
