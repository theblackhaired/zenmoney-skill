from __future__ import annotations

import calendar
import datetime
import re
import time
import uuid
from typing import Any

from dateutil.relativedelta import relativedelta

from . import cache as _cache
from .errors import (
    AmbiguousCategoryError,
    EntityNotFoundError,
    InvalidArgumentError,
    InvalidBoolError,
    InvalidCategoryError,
    InvalidDateError,
    InvalidMonthError,
    InvalidUUIDError,
)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _validate_uuid(val: str, field: str) -> None:
    if not _UUID_RE.match(val):
        raise InvalidUUIDError(f"Invalid UUID for {field}: {val}")


def _validate_date(val: str, field: str) -> None:
    if not _DATE_RE.match(val):
        raise InvalidDateError(f"Invalid date for {field}: {val}. Expected yyyy-MM-dd")


def _validate_month(val: str, field: str) -> None:
    if not _MONTH_RE.match(val):
        raise InvalidMonthError(f"Invalid month for {field}: {val}. Expected yyyy-MM")


def _parse_bool(val: Any, field: str) -> bool:
    """Parse a strict boolean value from JSON input."""
    if type(val) is bool:
        return val
    raise InvalidBoolError(f"{field} must be a boolean, got {val!r}")


def _validate_positive(val: float, field: str) -> None:
    if val < 0:
        raise InvalidArgumentError(f"{field} must be non-negative, got {val}")


def _validate_strict_positive(val: float, field: str) -> None:
    if val <= 0:
        raise InvalidArgumentError(f"{field} must be positive, got {val}")


def _get_bool_arg(args: dict, key: str, default: bool) -> bool:
    if key not in args:
        return default
    return _parse_bool(args[key], key)


def _today() -> str:
    return datetime.date.today().isoformat()


def _now_ts() -> int:
    return int(time.time())


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _generate_marker_dates(
    start_date: str,
    interval: str,
    step: int,
    points: list[int] | None,
    end_date: str | None,
    count: int
) -> list[str]:
    """Generate list of marker dates based on reminder recurrence rules.

    Args:
        start_date: Starting date in yyyy-MM-dd format
        interval: "day", "week", "month", or "year"
        step: Step size (e.g., 2 for every 2 months)
        points: For month/year intervals - specific days to generate markers
                (e.g., [1, 15] = 1st and 15th of each month)
        end_date: Optional end date - don't generate beyond this
        count: Maximum number of markers to generate

    Returns:
        List of date strings in yyyy-MM-dd format
    """
    today = datetime.date.today()
    current = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date) if end_date else None

    if step <= 0:
        raise InvalidArgumentError(f"step must be positive, got {step}")

    point_offsets = points if points is not None else [0]
    for point in point_offsets:
        if point < 0 or point >= step:
            raise InvalidArgumentError(f"points must be >= 0 and < step, got {point} for step {step}")

    start_day = current.day

    def apply_month_day(value: datetime.date) -> datetime.date:
        max_day = calendar.monthrange(value.year, value.month)[1]
        return value.replace(day=min(start_day, max_day))

    dates = []
    seen: set[str] = set()

    while len(dates) < count:
        for point in point_offsets:
            if interval == "day":
                marker_date = current + datetime.timedelta(days=point)
            elif interval == "week":
                marker_date = current + datetime.timedelta(weeks=point)
            elif interval == "month":
                marker_date = apply_month_day(current + relativedelta(months=point))
            elif interval == "year":
                marker_date = apply_month_day(current + relativedelta(years=point))
            else:
                return dates

            if marker_date < today:
                continue

            if end and marker_date > end:
                return dates

            marker_key = marker_date.isoformat()
            if marker_key not in seen:
                seen.add(marker_key)
                dates.append(marker_date.isoformat())

            if len(dates) >= count:
                break

        # Move to next occurrence
        if interval == "day":
            current += datetime.timedelta(days=step)
        elif interval == "week":
            current += datetime.timedelta(weeks=step)
        elif interval == "month":
            current += relativedelta(months=step)
        elif interval == "year":
            current += relativedelta(years=step)
        else:
            # Unknown interval - stop to avoid infinite loop
            break

    return dates[:count]


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def _fmt_account(a: dict) -> dict:
    instr = _cache.CACHE.get_instrument(a.get("instrument", 0))
    result: dict[str, Any] = {
        "id": a["id"],
        "title": a.get("title", ""),
        "type": a.get("type", ""),
        "balance": a.get("balance", 0),
        "currency": instr["shortTitle"] if instr else "Unknown",
        "inBalance": a.get("inBalance", True),
    }
    if a.get("creditLimit"):
        result["creditLimit"] = a["creditLimit"]
    if a.get("archive"):
        result["archived"] = True
    return result


def _tx_type(t: dict) -> str:
    is_transfer = (
        t.get("outcomeAccount") != t.get("incomeAccount")
        and t.get("outcome", 0) > 0
        and t.get("income", 0) > 0
    )
    if is_transfer:
        return "transfer"
    if t.get("outcome", 0) > 0 and t.get("income", 0) == 0:
        return "expense"
    if t.get("income", 0) > 0 and t.get("outcome", 0) == 0:
        return "income"
    return "unknown"


def _reminder_type(r: dict) -> str:
    """Determine reminder type: expense, income, transfer, or unknown."""
    is_transfer = (
        r.get("outcomeAccount") != r.get("incomeAccount")
        and r.get("outcome", 0) > 0
        and r.get("income", 0) > 0
    )
    if is_transfer:
        return "transfer"
    if r.get("outcome", 0) > 0 and r.get("income", 0) == 0:
        return "expense"
    if r.get("income", 0) > 0 and r.get("outcome", 0) == 0:
        return "income"
    return "unknown"


def _fmt_transaction(t: dict) -> dict:
    tt = _tx_type(t)
    out_acct = _cache.CACHE.get_account(t.get("outcomeAccount", ""))
    in_acct = _cache.CACHE.get_account(t.get("incomeAccount", ""))
    out_instr = _cache.CACHE.get_instrument(t.get("outcomeInstrument", 0))
    in_instr = _cache.CACHE.get_instrument(t.get("incomeInstrument", 0))
    categories = []
    for tid in (t.get("tag") or []):
        tag = _cache.CACHE.get_tag(tid)
        if tag:
            categories.append(tag["title"])
    merchant_name = None
    if t.get("merchant"):
        m = _cache.CACHE.get_merchant(t["merchant"])
        if m:
            merchant_name = m["title"]

    result: dict[str, Any] = {"id": t["id"], "date": t.get("date", ""), "type": tt}

    if tt == "expense":
        result["amount"] = t.get("outcome", 0)
        result["currency"] = out_instr["shortTitle"] if out_instr else "RUB"
        result["account"] = out_acct["title"] if out_acct else None
    elif tt == "income":
        result["amount"] = t.get("income", 0)
        result["currency"] = in_instr["shortTitle"] if in_instr else "RUB"
        result["account"] = in_acct["title"] if in_acct else None
    else:  # transfer
        result["outcomeAmount"] = t.get("outcome", 0)
        result["outcomeCurrency"] = out_instr["shortTitle"] if out_instr else "RUB"
        result["fromAccount"] = out_acct["title"] if out_acct else None
        result["incomeAmount"] = t.get("income", 0)
        result["incomeCurrency"] = in_instr["shortTitle"] if in_instr else "RUB"
        result["toAccount"] = in_acct["title"] if in_acct else None

    if categories:
        result["categories"] = categories
    if t.get("payee"):
        result["payee"] = t["payee"]
    if t.get("comment"):
        result["comment"] = t["comment"]
    if t.get("hold"):
        result["hold"] = True
    if merchant_name:
        result["merchant"] = merchant_name
    return result


def _fmt_budget(b: dict) -> dict:
    tag_id = b.get("tag")
    tag = _cache.CACHE.get_tag(tag_id) if tag_id and tag_id != ALL_CATEGORIES_ID else None
    parent_tag = None
    if tag and tag.get("parent"):
        parent_tag = _cache.CACHE.get_tag(tag["parent"])

    if tag_id == ALL_CATEGORIES_ID:
        category = "ALL (aggregate)"
    elif tag_id is None:
        category = "Uncategorized"
    else:
        category = tag["title"] if tag else tag_id

    result = {
        "category": category,
        "category_id": tag_id,
        "month": b.get("date", ""),
        "income": b.get("income", 0),
        "incomeLock": b.get("incomeLock", False),
        "outcome": b.get("outcome", 0),
        "outcomeLock": b.get("outcomeLock", False),
    }

    if parent_tag:
        result["parent_category"] = parent_tag["title"]

    return result


def _fmt_reminder(r: dict) -> dict:
    in_acct = _cache.CACHE.get_account(r.get("incomeAccount", ""))
    out_acct = _cache.CACHE.get_account(r.get("outcomeAccount", ""))
    categories = []
    for tid in (r.get("tag") or []):
        tag = _cache.CACHE.get_tag(tid)
        if tag:
            categories.append(tag["title"])
    result: dict[str, Any] = {
        "id": r["id"],
        "payee": r.get("payee"),
        "comment": r.get("comment"),
    }
    if r.get("income", 0) != 0:
        result["income"] = r["income"]
    if r.get("outcome", 0) != 0:
        result["outcome"] = r["outcome"]
    result["fromAccount"] = out_acct["title"] if out_acct else None
    result["toAccount"] = in_acct["title"] if in_acct else None
    if categories:
        result["categories"] = categories
    result["interval"] = r.get("interval")
    result["step"] = r.get("step")
    result["startDate"] = r.get("startDate")
    result["endDate"] = r.get("endDate")
    result["notify"] = r.get("notify", True)
    return result


def _g(key: str, args: dict, default: Any = None) -> Any:
    return args.get(key, default)


ALL_CATEGORIES_ID = "00000000-0000-0000-0000-000000000000"


def _collect_entities(items: Any) -> list[dict]:
    """Collect entity dicts from either raw JSON arrays or cache maps."""
    if isinstance(items, dict):
        return [item for item in items.values() if isinstance(item, dict)]
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _category_full_path(tag_id: str) -> str | None:
    """Return 'Parent / Child' path for a category id, or None if not found."""
    tag = _cache.CACHE.get_tag(tag_id)
    if not tag:
        return None
    tags_by_id = _cache.CACHE.tags_by_id()
    parts = [tag.get("title", "")]
    parent_id = tag.get("parent")
    visited = {str(tag_id)}
    while parent_id and str(parent_id) not in visited:
        visited.add(str(parent_id))
        parent = tags_by_id.get(str(parent_id))
        if not parent:
            break
        parts.append(parent.get("title", ""))
        parent_id = parent.get("parent")
    return " / ".join(reversed([part for part in parts if part]))


def _category_ref(tag_id: str) -> dict[str, str] | None:
    """Return normalized category metadata for a known tag id."""
    tag = _cache.CACHE.get_tag(tag_id)
    if not tag:
        return None
    title = tag.get("title", "")
    return {
        "id": str(tag["id"]),
        "title": title,
        "full_path": _category_full_path(str(tag["id"])) or title,
    }


def _find_category_id(name: str, *, allow_all: bool = False) -> str:
    """Resolve category name or UUID to a category id."""
    if not isinstance(name, str):
        raise InvalidArgumentError(f"Category reference must be a string, got {type(name).__name__}")
    normalized_name = name.strip()
    if not normalized_name:
        raise InvalidArgumentError("Category reference must not be empty")
    normalized_upper = normalized_name.upper()
    if allow_all and (
        normalized_name == ALL_CATEGORIES_ID
        or normalized_upper == "ALL (AGGREGATE)"
    ):
        return ALL_CATEGORIES_ID
    if _cache.CACHE.get_tag(normalized_name):
        return normalized_name
    lowered_name = normalized_name.lower()

    for tag in _cache.CACHE.tags():
        path = _category_full_path(tag["id"])
        if path and path.lower() == lowered_name:
            return tag["id"]

    matches = [tag for tag in _cache.CACHE.tags() if tag["title"].lower() == lowered_name]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        full_paths = ", ".join(_category_full_path(tag["id"]) or tag["title"] for tag in matches)
        raise AmbiguousCategoryError(
            f"Category name is ambiguous: {normalized_name}. Use UUID or full path. Matches: {full_paths}"
        )
    if allow_all and normalized_upper == "ALL":
        return ALL_CATEGORIES_ID
    raise InvalidCategoryError(f"Category not found: {normalized_name}")


def _build_tx_spec(
    tx_type: str,
    amount: float,
    account_id: str,
    to_account_id: str | None,
    currency_id: int | None,
    income_amount: float | None,
) -> dict:
    """Build incomeAccount/outcomeAccount/income/outcome fields from type."""
    account = _cache.CACHE.get_account(account_id)
    if not account:
        raise EntityNotFoundError(f"Account not found: {account_id}")
    instrument_id = currency_id if currency_id is not None else account.get("instrument", 0)

    spec: dict[str, Any] = {
        "incomeInstrument": instrument_id,
        "incomeAccount": account_id,
        "income": 0,
        "outcomeInstrument": instrument_id,
        "outcomeAccount": account_id,
        "outcome": 0,
    }

    if tx_type == "expense":
        spec["outcome"] = amount
        spec["outcomeAccount"] = account_id
        spec["outcomeInstrument"] = instrument_id
        spec["incomeAccount"] = account_id
        spec["incomeInstrument"] = instrument_id
        spec["income"] = 0
    elif tx_type == "income":
        spec["income"] = amount
        spec["incomeAccount"] = account_id
        spec["incomeInstrument"] = instrument_id
        spec["outcomeAccount"] = account_id
        spec["outcomeInstrument"] = instrument_id
        spec["outcome"] = 0
    elif tx_type == "transfer":
        if not to_account_id:
            raise InvalidArgumentError("to_account_id is required for transfer type")
        to_acct = _cache.CACHE.get_account(to_account_id)
        if not to_acct:
            raise EntityNotFoundError(f"Destination account not found: {to_account_id}")
        spec["outcome"] = amount
        spec["outcomeAccount"] = account_id
        spec["outcomeInstrument"] = account.get("instrument", 0)
        spec["incomeAccount"] = to_account_id
        spec["incomeInstrument"] = to_acct.get("instrument", 0)
        if account.get("instrument") != to_acct.get("instrument"):
            if not income_amount:
                raise InvalidArgumentError("income_amount is required for cross-currency transfers")
            spec["income"] = income_amount
        else:
            spec["income"] = amount
    return spec
