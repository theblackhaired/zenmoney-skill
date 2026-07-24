from __future__ import annotations

import json
from typing import Any

from . import cache as _cache
from .domain import _build_tx_spec, _fmt_account, _fmt_transaction, _new_uuid, _now_ts, _tx_type
from .errors import InvalidArgumentError
from .transport import _write_diff
from .validation import (
    require_account,
    require_category_ids_exist,
    require_entity,
    require_first_user,
    validate_tool_args,
)


def _reject_unsupported_cross_currency_single_account_transaction(
    tx_type: str,
    account: dict[str, Any],
    currency_id: int | None,
) -> None:
    if tx_type not in {"expense", "income"} or currency_id is None:
        return
    account_currency = account.get("instrument")
    if account_currency != currency_id:
        raise InvalidArgumentError(
            "Cross-currency expense/income is not supported yet. Use the account currency or create a transfer."
        )


async def tool_create_transaction(args: dict) -> str:
    args = validate_tool_args("create_transaction", args)
    tx_type = args["type"]
    amount = args["amount"]
    account_id = args["account_id"]
    to_account_id = args.get("to_account_id")
    category_ids = args.get("category_ids")
    date = args["date"]
    payee = args.get("payee")
    comment = args.get("comment")
    currency_id = args.get("currency_id")
    income_amount = args.get("income_amount")

    account = require_account(account_id)
    require_category_ids_exist(category_ids)
    _reject_unsupported_cross_currency_single_account_transaction(tx_type, account, currency_id)

    spec = _build_tx_spec(tx_type, amount, account_id, to_account_id, currency_id, income_amount)

    now = _now_ts()

    tx: dict[str, Any] = {
        "id": _new_uuid(),
        "user": account["user"],
        "changed": now,
        "created": now,
        "deleted": False,
        "hold": None,
        **spec,
        "tag": category_ids if category_ids else None,
        "merchant": None,
        "payee": payee,
        "originalPayee": None,
        "comment": comment,
        "date": date,
        "mcc": None,
        "reminderMarker": None,
        "opIncome": None,
        "opIncomeInstrument": None,
        "opOutcome": None,
        "opOutcomeInstrument": None,
        "latitude": None,
        "longitude": None,
        "qrCode": None,
        "incomeBankID": None,
        "outcomeBankID": None,
    }

    await _write_diff({"transaction": [tx]})
    created = _cache.CACHE.get("transaction", tx["id"]) or tx
    return json.dumps({"created": _fmt_transaction(created)}, ensure_ascii=False)


async def tool_update_transaction(args: dict) -> str:
    args = validate_tool_args("update_transaction", args)
    tid = args["id"]
    existing = require_entity("transaction", tid, f"Transaction not found: {tid}")
    require_category_ids_exist(args.get("category_ids"))

    updated = {**existing, "changed": _now_ts()}
    amount = args.get("amount")
    if amount is not None:
        tt = _tx_type(existing)
        if tt == "transfer":
            if existing.get("outcomeInstrument") != existing.get("incomeInstrument"):
                raise InvalidArgumentError("Cannot update amount on cross-currency transfers. Delete and recreate.")
            updated["outcome"] = amount
            updated["income"] = amount
        elif existing.get("outcome", 0) > 0:
            updated["outcome"] = amount
        else:
            updated["income"] = amount

    if "category_ids" in args:
        updated["tag"] = args["category_ids"]
    if "date" in args:
        updated["date"] = args["date"]
    if "payee" in args:
        updated["payee"] = args["payee"]
    if "comment" in args:
        updated["comment"] = args["comment"]

    await _write_diff({"transaction": [updated]})
    result = _cache.CACHE.get("transaction", tid) or updated
    return json.dumps({"updated": _fmt_transaction(result)}, ensure_ascii=False)


async def tool_delete_transaction(args: dict) -> str:
    args = validate_tool_args("delete_transaction", args)
    tid = args["id"]
    existing = require_entity("transaction", tid, f"Transaction not found: {tid}")

    deleted = {**existing, "deleted": True, "changed": _now_ts()}
    await _write_diff({"transaction": [deleted]})
    return json.dumps({
        "deleted": True, "id": tid,
        "date": existing.get("date"),
        "amount": existing.get("outcome") or existing.get("income"),
    }, ensure_ascii=False)


async def tool_create_account(args: dict) -> str:
    args = validate_tool_args("create_account", args)
    title = args["title"]
    acct_type = args["type"]
    currency_id = args["currency_id"]
    balance = args["balance"]
    credit_limit = args["credit_limit"]

    if not _cache.CACHE.get_instrument(currency_id):
        raise InvalidArgumentError(f"Unknown currency_id: {currency_id}. Use get_instruments to see available currencies.")

    user = require_first_user()
    now = _now_ts()

    new_account: dict[str, Any] = {
        "id": _new_uuid(),
        "user": user["id"],
        "instrument": currency_id,
        "type": acct_type,
        "role": None,
        "company": None,
        "title": title,
        "syncID": None,
        "balance": balance,
        "startBalance": balance,
        "creditLimit": credit_limit,
        "inBalance": True,
        "savings": False,
        "enableCorrection": False,
        "enableSMS": False,
        "archive": False,
        "private": False,
        "capitalization": None,
        "percent": None,
        "startDate": None,
        "endDateOffset": None,
        "endDateOffsetInterval": None,
        "payoffStep": None,
        "payoffInterval": None,
        "changed": now,
    }

    await _write_diff({"account": [new_account]})
    created = _cache.CACHE.get_account(new_account["id"]) or new_account
    return json.dumps({"created": _fmt_account(created)}, ensure_ascii=False)
