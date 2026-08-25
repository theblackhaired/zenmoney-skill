from __future__ import annotations

import json
import unicodedata
from typing import Any

from . import cache as _cache
from .domain import (
    _category_full_path,
    _fmt_account,
    _fmt_transaction,
    _today,
    _tx_type,
)
from .errors import ToolError
from .transport import _api_post, _sync
from .validation import validate_tool_args


async def tool_get_accounts(args: dict) -> str:
    args = validate_tool_args("get_accounts", args)
    include_archived = args["include_archived"]
    accounts = _cache.CACHE.accounts()
    if not include_archived:
        accounts = [a for a in accounts if not a.get("archive")]
    return json.dumps([_fmt_account(a) for a in accounts], ensure_ascii=False)


async def tool_get_transactions(args: dict) -> str:
    args = validate_tool_args("get_transactions", args)
    start_date = args["start_date"]
    end_date = args.get("end_date") or _today()
    account_id = args.get("account_id")
    category_id = args.get("category_id")
    tx_type = args.get("type")
    limit = args["limit"]
    offset = args["offset"]

    txs = [t for t in _cache.CACHE.transactions() if not t.get("deleted")]
    txs = [t for t in txs if t.get("date", "") >= start_date and t.get("date", "") <= end_date]

    if account_id:
        txs = [t for t in txs if t.get("incomeAccount") == account_id or t.get("outcomeAccount") == account_id]
    if category_id:
        txs = [t for t in txs if category_id in (t.get("tag") or [])]
    if tx_type:
        txs = [t for t in txs if _tx_type(t) == tx_type]

    txs.sort(key=lambda t: (t.get("date", ""), t.get("created", 0)), reverse=True)
    total = len(txs)
    limited = txs[offset:offset + limit]
    result: dict[str, Any] = {"transactions": [_fmt_transaction(t) for t in limited]}
    if total > offset + len(limited):
        result["truncated"] = True
        result["total"] = total
        result["showing"] = len(limited)
        result["offset"] = offset
    return json.dumps(result, ensure_ascii=False)


async def tool_get_categories(args: dict) -> str:
    tags = _cache.CACHE.tags()
    roots = [t for t in tags if not t.get("parent")]
    children = [t for t in tags if t.get("parent")]
    tree = []
    for root in roots:
        child_list = [{"id": c["id"], "title": c["title"]} for c in children if c.get("parent") == root["id"]]
        node: dict[str, Any] = {"id": root["id"], "title": root["title"]}
        if child_list:
            node["children"] = child_list
        tree.append(node)
    return json.dumps(tree, ensure_ascii=False)


async def tool_get_instruments(args: dict) -> str:
    args = validate_tool_args("get_instruments", args)
    include_all = args["include_all"]
    instruments = _cache.CACHE.instruments()
    if not include_all:
        used_ids = {a.get("instrument") for a in _cache.CACHE.accounts()}
        instruments = [i for i in instruments if i.get("id") in used_ids]
    formatted = [
        {"id": i["id"], "code": i.get("shortTitle", ""), "title": i.get("title", ""),
         "symbol": i.get("symbol", ""), "rate": i.get("rate", 1)}
        for i in instruments
    ]
    return json.dumps(formatted, ensure_ascii=False)

async def tool_get_analytics(args: dict) -> str:
    args = validate_tool_args("get_analytics", args)
    start_date = args["start_date"]
    end_date = args.get("end_date") or _today()
    report = args["report"]
    group_by = args["group_by"]
    currency_mode = args["currency_mode"]
    account_scope = args["account_scope"]
    account_ids = set(args["account_ids"])
    category_scope = args["category_scope"]
    category_role = args["category_role"]
    category_ids = set(args["category_ids"])
    merchant_scope = args["merchant_scope"]
    merchant_ids = set(args["merchant_ids"])
    payees = set(args["payees"])

    txs = [t for t in _cache.CACHE.transactions() if not t.get("deleted")]
    txs = [t for t in txs if t.get("date", "") >= start_date and t.get("date", "") <= end_date]

    filtered = []
    for t in txs:
        tx_type = _tx_type(t)
        if tx_type not in ("expense", "income"):
            continue
        expected_type = "income" if report == "income" else "expense"
        if report != "net" and tx_type != expected_type:
            continue

        side = "outcome" if tx_type == "expense" else "income"
        account_id = t.get(f"{side}Account")
        account = _cache.CACHE.get_account(account_id) if account_id else None
        if account_scope == "in_balance" and not (account and account.get("inBalance") is True):
            continue
        if account_scope == "selected" and account_id not in account_ids:
            continue

        if category_scope == "selected":
            tag_ids = [str(tag_id) for tag_id in (t.get("tag") or [])]
            if category_role == "primary":
                eligible_tags = tag_ids[:1]
            elif category_role == "additional":
                eligible_tags = tag_ids[1:]
            else:
                eligible_tags = tag_ids
            if category_ids.isdisjoint(eligible_tags):
                continue

        if merchant_scope == "selected":
            merchant_id = t.get("merchant")
            if merchant_id:
                if str(merchant_id) not in merchant_ids:
                    continue
            else:
                payee = t.get("payee")
                normalized_payee = unicodedata.normalize("NFC", payee) if isinstance(payee, str) else None
                if normalized_payee not in payees:
                    continue

        filtered.append(t)

    # A transaction's own side instrument is authoritative. The account is a
    # fallback for older records that do not carry the instrument field.
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for tx in filtered:
        tx_type = _tx_type(tx)
        side = "outcome" if tx_type == "expense" else "income"
        account_id = tx.get(f"{side}Account")
        account = _cache.CACHE.get_account(account_id) if account_id else None
        instrument_id = tx.get(f"{side}Instrument")
        instrument = _cache.CACHE.get_instrument(instrument_id) if instrument_id is not None else None
        if instrument is None and account:
            instrument = _cache.CACHE.get_instrument(account.get("instrument"))
        currency = instrument.get("shortTitle") if instrument else None
        currency = currency or "UNKNOWN"

        if group_by == "category":
            tag_ids = tx.get("tag") or []
            if tag_ids:
                tag_id = str(tag_ids[0])
                group_key = f"category:{tag_id}"
                name = _category_full_path(tag_id) or "Unknown Category"
            else:
                group_key = "category:uncategorized"
                name = "Uncategorized"
        elif group_by == "account":
            group_key = f"account:{account_id}" if account else "account:unknown"
            name = account["title"] if account else "Unknown Account"
        else:
            merchant_id = tx.get("merchant")
            payee = tx.get("payee")
            if merchant_id:
                merchant = _cache.CACHE.get_merchant(merchant_id)
                group_key = f"merchant:{merchant_id}"
                name = merchant["title"] if merchant else (payee or "Unknown Merchant")
            elif payee:
                normalized_payee = unicodedata.normalize("NFC", str(payee))
                group_key = f"payee:{normalized_payee}"
                name = normalized_payee
            else:
                group_key = "merchant:unknown"
                name = "Unknown Merchant"

        composite = (group_key, currency)
        if composite not in groups:
            groups[composite] = {
                "key": group_key,
                "name": name,
                "currency": currency,
                "income": 0,
                "outcome": 0,
                "transaction_count": 0,
            }
        group = groups[composite]
        group["income"] += tx.get("income", 0)
        group["outcome"] += tx.get("outcome", 0)
        group["transaction_count"] += 1

    def report_value(income: float, outcome: float) -> float:
        if report == "income":
            return income
        if report == "outcome":
            return outcome
        return income - outcome

    by_currency: dict[str, dict[str, float | int]] = {}
    for (_, currency), group in groups.items():
        totals = by_currency.setdefault(
            currency,
            {"income": 0, "outcome": 0, "value": 0, "transaction_count": 0},
        )
        totals["income"] += group["income"]
        totals["outcome"] += group["outcome"]
        totals["transaction_count"] += group["transaction_count"]

    currencies = sorted(by_currency)
    for currency in currencies:
        totals = by_currency[currency]
        totals["value"] = report_value(totals["income"], totals["outcome"])
    by_currency = {currency: by_currency[currency] for currency in currencies}

    groups_list = []
    for group in groups.values():
        entry = {
            "key": group["key"],
            "name": group["name"],
            "income": group["income"],
            "outcome": group["outcome"],
            "value": report_value(group["income"], group["outcome"]),
            "transaction_count": group["transaction_count"],
        }
        if currency_mode == "split":
            entry["currency"] = group["currency"]
        groups_list.append(entry)

    def sort_key(group: dict[str, Any]) -> tuple:
        currency = group.get("currency", "")
        magnitude = abs(group["value"]) if report == "net" else group["value"]
        name = unicodedata.normalize("NFC", str(group["name"]))
        key = unicodedata.normalize("NFC", str(group["key"]))
        return currency, -magnitude, name, key

    groups_list.sort(key=sort_key)

    result = {
        "period": {"start_date": start_date, "end_date": end_date},
        "report": report,
        "group_by": group_by,
        "currency_mode": currency_mode,
        "transaction_count": len(filtered),
        "policies": {
            "tag_policy": "primary_tag",
            "currency_conversion": "none",
            "transfers": "excluded",
            "unknown_currency": "separate_bucket",
            "account_filter": "report_side",
            "category_filter": "exact_tag_id",
            "merchant_identity": "merchant_then_payee_exact",
        },
        "filters": {
            "account": {
                "scope": account_scope,
                "ids": args["account_ids"],
            },
            "category": {
                "scope": category_scope,
                "role": category_role,
                "ids": args["category_ids"],
            },
            "merchant": {
                "scope": merchant_scope,
                "ids": args["merchant_ids"],
                "payees": args["payees"],
            },
        },
        "groups": groups_list,
    }

    if currency_mode == "split":
        result["currencies"] = currencies
        result["totals"] = {"by_currency": by_currency}
    else:
        if len(currencies) > 1:
            raise ToolError(
                "MIXED_CURRENCY",
                "currency_mode=scalar is unavailable for a mixed-currency report",
                {"currencies": currencies, "currency_mode": currency_mode},
            )
        if currencies:
            currency = currencies[0]
            result["totals"] = {"currency": currency, **by_currency[currency]}
        else:
            result["totals"] = {
                "currency": None,
                "income": 0,
                "outcome": 0,
                "value": 0,
                "transaction_count": 0,
            }
    return json.dumps(result, ensure_ascii=False)


async def tool_suggest(args: dict) -> str:
    args = validate_tool_args("suggest", args)
    payee = args["payee"]
    result = await _api_post("/v8/suggest/", {"payee": payee})
    return json.dumps(result, ensure_ascii=False)


async def tool_get_merchants(args: dict) -> str:
    args = validate_tool_args("get_merchants", args)
    search = args.get("search")
    limit = args["limit"]
    offset = args["offset"]
    merchants = _cache.CACHE.merchants()
    if search:
        q = search.lower()
        merchants = [m for m in merchants if q in m.get("title", "").lower()]
    total = len(merchants)
    eff_limit = min(limit, 200)
    limited = merchants[offset:offset + eff_limit]
    formatted = [{"id": m["id"], "title": m["title"]} for m in limited]
    result: dict[str, Any] = {"merchants": formatted}
    if total > offset + len(limited):
        result["truncated"] = True
        result["total"] = total
        result["showing"] = len(limited)
        result["offset"] = offset
    return json.dumps(result, ensure_ascii=False)


async def tool_check_auth_status(args: dict) -> str:
    args = validate_tool_args("check_auth_status", args)
    try:
        await _sync()
        return json.dumps({"status": "authenticated", "message": "Token is valid and working"}, ensure_ascii=False)
    except Exception as e:
        msg = str(e)
        return json.dumps({
            "status": "error",
            "error": msg,
            "solution": (
                "Token expired. Get a new token from https://budgera.com/settings/export"
                if "401" in msg or "expired" in msg.lower()
                else "Check your credentials or network connection"
            ),
        }, ensure_ascii=False)
