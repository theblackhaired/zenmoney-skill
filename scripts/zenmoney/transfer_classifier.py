from __future__ import annotations

import json
from typing import Any

from .errors import InvalidArgumentError


BALANCE = "BALANCE"
BUDGET_LIMIT = "BUDGET_LIMIT"
EXCLUDE_OPENING_BALANCE = "EXCLUDE_OPENING_BALANCE"

INCLUDE_OPENING_BALANCE = "INCLUDE_OPENING_BALANCE"

_AXIS_SETTINGS = {
    ("from", "savings"): "EXCLUDE_TRANSFER_FROM_SAVINGS",
    ("from", "loans"): "EXCLUDE_TRANSFER_FROM_LOANS",
    ("from", "debts"): "EXCLUDE_TRANSFER_FROM_DEBTS",
    ("from", "other_accounts"): "EXCLUDE_TRANSFER_FROM_OTHER_ACCOUNTS",
    ("to", "savings"): "EXCLUDE_TRANSFER_TO_SAVINGS",
    ("to", "loans"): "EXCLUDE_TRANSFER_TO_LOANS",
    ("to", "debts"): "EXCLUDE_TRANSFER_TO_DEBTS",
    ("to", "other_accounts"): "EXCLUDE_TRANSFER_TO_OTHER_ACCOUNTS",
}

PLAN_SETTINGS = frozenset({INCLUDE_OPENING_BALANCE, *_AXIS_SETTINGS.values()})

_PLAN_SETTING_ALIASES = {
    "includeOpeningBalance": INCLUDE_OPENING_BALANCE,
    "excludeTransferFromSavings": "EXCLUDE_TRANSFER_FROM_SAVINGS",
    "excludeTransferFromLoans": "EXCLUDE_TRANSFER_FROM_LOANS",
    "excludeTransferFromDebts": "EXCLUDE_TRANSFER_FROM_DEBTS",
    "excludeTransferFromOtherAccounts": "EXCLUDE_TRANSFER_FROM_OTHER_ACCOUNTS",
    "excludeTransferToSavings": "EXCLUDE_TRANSFER_TO_SAVINGS",
    "excludeTransferToLoans": "EXCLUDE_TRANSFER_TO_LOANS",
    "excludeTransferToDebts": "EXCLUDE_TRANSFER_TO_DEBTS",
    "excludeTransferToOtherAccounts": "EXCLUDE_TRANSFER_TO_OTHER_ACCOUNTS",
}

PUBLIC_MODE_TO_ZM = {
    "balance_vs_expense": BALANCE,
    "income_vs_expense": EXCLUDE_OPENING_BALANCE,
}

_ZM_MODE_ALIASES = {
    "balance": BALANCE,
    "budgetLimit": BUDGET_LIMIT,
    "excludeOpeningBalance": EXCLUDE_OPENING_BALANCE,
    BALANCE: BALANCE,
    BUDGET_LIMIT: BUDGET_LIMIT,
    EXCLUDE_OPENING_BALANCE: EXCLUDE_OPENING_BALANCE,
}


def normalize_plan_balance_mode(value: str) -> str:
    try:
        return _ZM_MODE_ALIASES[value]
    except (KeyError, TypeError) as exc:
        raise InvalidArgumentError(f"Unsupported ZenMoney plan balance mode: {value!r}") from exc


def parse_plan_settings(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise InvalidArgumentError("ZenMoney planSettings must be a JSON array") from exc
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise InvalidArgumentError("ZenMoney planSettings must be an array")
    raw_settings = set(value)
    if not all(isinstance(setting, str) for setting in raw_settings):
        raise InvalidArgumentError("ZenMoney planSettings entries must be strings")
    settings = {_PLAN_SETTING_ALIASES.get(setting, setting) for setting in raw_settings}
    unknown = sorted(settings - PLAN_SETTINGS)
    if unknown:
        raise InvalidArgumentError(
            f"Unsupported ZenMoney planSettings: {', '.join(unknown)}",
            {"unknown_plan_settings": unknown},
        )
    return settings


def _account_axis(side: dict[str, Any]) -> str:
    if side.get("known_account") is False:
        return "unknown"
    account_type = str(side.get("account_type") or "").lower()
    account_subtype = str(side.get("account_subtype") or "").lower()
    credit_limit = side.get("credit_limit", side.get("creditLimit", 0)) or 0

    if account_type == "debt" or account_subtype == "debt":
        return "debts"
    if (
        account_type == "loan"
        or account_subtype == "credit"
        or (account_type == "ccard" and credit_limit > 0)
    ):
        return "loans"
    if account_type == "deposit" or account_subtype == "savings" or side.get("savings") is True:
        return "savings"
    return "other_accounts"


def select_plan_user(
    users: list[dict[str, Any]],
    preferred_user_id: Any = None,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    if preferred_user_id is not None:
        matches = [user for user in users if str(user.get("id")) == str(preferred_user_id)]
        if len(matches) != 1:
            raise InvalidArgumentError(
                f"Configured plan_user_id was not found: {preferred_user_id}",
                {"plan_user_id": preferred_user_id},
            )
        return matches[0]
    if len(users) == 1:
        return users[0]
    if not users:
        if allow_empty:
            return {}
        raise InvalidArgumentError(
            "ZenMoney user preferences are unavailable; sync user data or configure an explicit Plans policy"
        )
    raise InvalidArgumentError(
        "ZenMoney returned multiple users; configure plan_user_id explicitly",
        {"user_ids": [user.get("id") for user in users]},
    )


def _copy_side(side: Any, field: str) -> dict[str, Any]:
    if not isinstance(side, dict):
        raise InvalidArgumentError(f"{field} must be an object")
    required = {"account_id", "amount", "currency", "in_balance"}
    missing = sorted(required - side.keys())
    if missing:
        raise InvalidArgumentError(f"{field} is missing: {', '.join(missing)}")
    return dict(side)


def classify_transfer_event(item: dict[str, Any]) -> dict[str, Any]:
    """Create a lossless Plans transfer event without applying mode policy."""
    outcome_side = _copy_side(item.get("outcome_side"), "outcome_side")
    income_side = _copy_side(item.get("income_side"), "income_side")
    outcome_in_balance = outcome_side["in_balance"] is True
    income_in_balance = income_side["in_balance"] is True

    if outcome_in_balance and income_in_balance:
        direction = "balance_to_balance"
    elif not outcome_in_balance and income_in_balance:
        direction = "off_balance_to_balance"
    elif outcome_in_balance and not income_in_balance:
        direction = "balance_to_off_balance"
    else:
        direction = "off_balance_to_off_balance"

    return {
        "kind": "transfer",
        "id": item.get("id"),
        "date": item.get("date"),
        "status": item.get("status"),
        "comment": item.get("comment"),
        "direction": direction,
        "outcome_axis": _account_axis(outcome_side),
        "income_axis": _account_axis(income_side),
        "outcome_side": outcome_side,
        "income_side": income_side,
    }


def classify_opening_balance_event(side: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "opening_balance",
        "side": _copy_side(side, "side"),
    }


def _effect(kind: str, side: dict[str, Any], setting: str | None = None) -> dict[str, Any]:
    effect = {
        "kind": kind,
        "amount": side["amount"],
        "currency": side["currency"],
    }
    if setting is not None:
        effect["setting"] = setting
    return effect


def evaluate_plan_transfer(
    event: dict[str, Any],
    *,
    plan_balance_mode: str,
    plan_settings: Any,
) -> dict[str, Any]:
    """Apply the Android 26.6 Plans mode/settings policy to a lossless event."""
    mode = normalize_plan_balance_mode(plan_balance_mode)
    settings = parse_plan_settings(plan_settings)
    if mode == BUDGET_LIMIT:
        raise InvalidArgumentError(
            "ZenMoney BUDGET_LIMIT uses a separate SmartBudget formula and is not a Plans UI mode"
        )

    if event.get("kind") == "opening_balance":
        include = mode == BALANCE or INCLUDE_OPENING_BALANCE in settings
        effects = [_effect("income", event["side"])] if include else []
        reason = "opening_balance_included" if include else "opening_balance_excluded"
    elif event.get("kind") == "transfer":
        if "unknown" in {event.get("outcome_axis"), event.get("income_axis")}:
            raise InvalidArgumentError(
                "Cannot classify a Plans transfer with an unknown account endpoint",
                {"event_id": event.get("id"), "direction": event.get("direction")},
            )
        direction = event.get("direction")
        if direction == "balance_to_balance":
            effects = []
            reason = "balance_to_balance_neutral"
        elif direction == "off_balance_to_off_balance":
            effects = []
            reason = "outside_balance_perimeter"
        else:
            inbound = direction == "off_balance_to_balance"
            policy_direction = "from" if inbound else "to"
            axis = event["outcome_axis"] if inbound else event["income_axis"]
            setting = _AXIS_SETTINGS[(policy_direction, axis)]
            excluded = mode == EXCLUDE_OPENING_BALANCE and setting in settings
            if excluded:
                effects = []
                reason = setting
            else:
                side = event["income_side"] if inbound else event["outcome_side"]
                effects = [_effect("income" if inbound else "expense", side, setting)]
                reason = "included_balance_change"
    else:
        raise InvalidArgumentError(f"Unsupported plan event kind: {event.get('kind')!r}")

    net = sum(
        effect["amount"] if effect["kind"] == "income" else -effect["amount"]
        for effect in effects
    )
    return {
        "event": event,
        "plan_balance_mode": mode,
        "plan_settings": sorted(settings),
        "effects": effects,
        "net": net,
        "reason": reason,
    }
