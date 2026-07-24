from __future__ import annotations

import json
from typing import Any

from . import cache as _cache
from .domain import (
    _category_full_path,
    _fmt_account,
    _fmt_transaction,
    _today,
    _tx_type,
)
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
    group_by = args.get("group_by", "category")
    an_type = args.get("type", "expense")

    txs = [t for t in _cache.CACHE.transactions() if not t.get("deleted")]
    txs = [t for t in txs if t.get("date", "") >= start_date and t.get("date", "") <= end_date]

    # Filter by type
    filtered = []
    for t in txs:
        tt = _tx_type(t)
        if an_type == "expense" and tt == "expense":
            filtered.append(t)
        elif an_type == "income" and tt == "income":
            filtered.append(t)
        elif an_type == "all" and tt in ("expense", "income"):
            filtered.append(t)

    # Group by (name, currency) so different currencies get separate buckets.
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for tx in filtered:
        key = "Uncategorized"
        currency = "RUB"

        if group_by == "category":
            tag_ids = tx.get("tag") or []
            if tag_ids:
                key = _category_full_path(tag_ids[0]) or "Uncategorized"
            acct_id = tx.get("outcomeAccount") if tx.get("outcome", 0) > 0 else tx.get("incomeAccount")
            acct = _cache.CACHE.get_account(acct_id) if acct_id else None
            instr = _cache.CACHE.get_instrument(acct["instrument"]) if acct else None
            currency = instr["shortTitle"] if instr else "RUB"
        elif group_by == "account":
            acct_id = tx.get("incomeAccount") if an_type == "income" else tx.get("outcomeAccount")
            acct = _cache.CACHE.get_account(acct_id) if acct_id else None
            key = acct["title"] if acct else "Unknown Account"
            instr = _cache.CACHE.get_instrument(acct["instrument"]) if acct else None
            currency = instr["shortTitle"] if instr else "RUB"
        elif group_by == "merchant":
            if tx.get("merchant"):
                m = _cache.CACHE.get_merchant(tx["merchant"])
                key = m["title"] if m else (tx.get("payee") or "Unknown Merchant")
            elif tx.get("payee"):
                key = tx["payee"]
            acct_id = tx.get("outcomeAccount") if tx.get("outcome", 0) > 0 else tx.get("incomeAccount")
            acct = _cache.CACHE.get_account(acct_id) if acct_id else None
            instr = _cache.CACHE.get_instrument(acct["instrument"]) if acct else None
            currency = instr["shortTitle"] if instr else "RUB"

        composite = (key, currency)
        if composite not in groups:
            groups[composite] = {"income": 0, "outcome": 0, "count": 0}
        g = groups[composite]
        g["income"] += tx.get("income", 0)
        g["outcome"] += tx.get("outcome", 0)
        g["count"] += 1

    grand_total_by_currency: dict[str, float] = {}
    for (_, currency), g in groups.items():
        if an_type == "expense":
            inc = g["outcome"]
        elif an_type == "income":
            inc = g["income"]
        else:
            inc = g["income"] + g["outcome"]
        grand_total_by_currency[currency] = grand_total_by_currency.get(currency, 0) + inc

    groups_list = []
    for (name, currency), data in groups.items():
        total_val = data["outcome"] if an_type == "expense" else data["income"] if an_type == "income" else data["income"] + data["outcome"]
        entry: dict[str, Any] = {"name": name, "total": total_val, "count": data["count"], "currency": currency}
        if an_type == "all":
            entry["income"] = data["income"]
            entry["outcome"] = data["outcome"]
        groups_list.append(entry)
    # Sort by currency, then by total desc within currency.
    groups_list.sort(key=lambda x: (x["currency"], -x["total"]))

    return json.dumps({
        "period": {"from": start_date, "to": end_date},
        "type": an_type,
        "groupBy": group_by,
        "grandTotalByCurrency": grand_total_by_currency,
        "transactionCount": len(filtered),
        "groups": groups_list,
    }, ensure_ascii=False)


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
