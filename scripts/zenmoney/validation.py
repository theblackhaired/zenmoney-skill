from __future__ import annotations

import datetime
import math
import re
import secrets
from typing import Any

from . import cache as _cache
from . import config
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
    UnsupportedCategoryFilterError,
)


_VALIDATED_TOOL_KEY = "__validated_for_tool__"
_VALIDATED_TOOL_TOKEN = secrets.token_hex(8)
_VALIDATED_TOOL_VALUE_PREFIX = f"internal:{_VALIDATED_TOOL_TOKEN}:"
_RELATIVE_DAY_RE = re.compile(r"^[+-]?\d+d$")
_TRANSACTION_TYPES = {"expense", "income", "transfer"}
_TRANSACTION_FILTER_TYPES = _TRANSACTION_TYPES
_REMINDER_FILTER_TYPES = _TRANSACTION_TYPES | {"all"}
_ANALYTICS_TYPES = {"expense", "income", "all"}
_ANALYTICS_GROUP_BY = {"category", "account", "merchant"}
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


def _month_end(value: datetime.date) -> datetime.date:
    next_month = (value.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return next_month - datetime.timedelta(days=1)


def _current_billing_period() -> tuple[str, str]:
    cfg = config._load_config()
    start_day_raw = cfg.get("billing_period_start_day", 1)
    try:
        start_day = int(start_day_raw)
    except (TypeError, ValueError):
        start_day = 1
    start_day = max(1, min(start_day, 28))

    today = datetime.date.fromisoformat(_today())
    if today.day >= start_day:
        period_start = datetime.date(today.year, today.month, start_day)
        next_month = (today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        period_end = datetime.date(next_month.year, next_month.month, start_day) - datetime.timedelta(days=1)
    else:
        prev_month = today.replace(day=1) - datetime.timedelta(days=1)
        period_start = datetime.date(prev_month.year, prev_month.month, start_day)
        period_end = datetime.date(today.year, today.month, start_day) - datetime.timedelta(days=1)
    return period_start.isoformat(), period_end.isoformat()


def resolve_period_date_arg(value: Any, key: str, *, role: str) -> str:
    value = _ensure_not_empty_string(value, key, error_cls=InvalidDateError)
    if not isinstance(value, str):
        raise InvalidDateError(f"{key} must be a string date")

    today = datetime.date.fromisoformat(_today())
    if value == "today":
        return today.isoformat()
    if _RELATIVE_DAY_RE.fullmatch(value):
        return (today + datetime.timedelta(days=int(value[:-1]))).isoformat()
    if value == "this_month":
        if role == "start":
            return today.replace(day=1).isoformat()
        return _month_end(today).isoformat()
    if value == "billing_period":
        period_start, period_end = _current_billing_period()
        return period_start if role == "start" else period_end
    try:
        _validate_date(value, key)
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidDateError(str(exc)) from exc
    return value


def normalize_period_range(args: dict, start_key: str = "start_date", end_key: str = "end_date") -> tuple[str, str | None]:
    start_raw = args[start_key]
    start = resolve_period_date_arg(start_raw, start_key, role="start")
    end: str | None = None
    if end_key in args:
        end = resolve_period_date_arg(args[end_key], end_key, role="end")
    elif start_raw in {"this_month", "billing_period"}:
        end = resolve_period_date_arg(start_raw, end_key, role="end")
    if end is not None and start > end:
        raise InvalidDateRangeError(f"{start_key} must be on or before {end_key}")
    return start, end


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
        normalized["start_date"], normalized_end_date = normalize_period_range(normalized)
        if normalized_end_date is not None:
            normalized["end_date"] = normalized_end_date
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
        normalized["start_date"], normalized_end_date = normalize_period_range(normalized)
        if normalized_end_date is not None:
            normalized["end_date"] = normalized_end_date
        normalized["type"] = get_enum_arg(normalized, "type", _ANALYTICS_TYPES, default="expense")
        normalized["group_by"] = get_enum_arg(normalized, "group_by", _ANALYTICS_GROUP_BY, default="category")
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
        normalized["show_forecast"] = get_bool_arg(normalized, "show_forecast", True)
        normalized["show_calendar"] = get_bool_arg(normalized, "show_calendar", True)
        if "budget_mode" in normalized:
            mode = normalized["budget_mode"]
            if mode not in _BUDGET_MODE_DEFAULTS:
                raise InvalidArgumentError(
                    f"Unknown budget_mode: {mode}. Available: {sorted(_BUDGET_MODE_DEFAULTS)}"
                )
        if "start_date" in normalized:
            normalized["start_date"], normalized_end_date = normalize_period_range(normalized)
            if normalized_end_date is not None:
                normalized["end_date"] = normalized_end_date
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
