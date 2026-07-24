from __future__ import annotations

import json
from typing import Any

from . import cache as _cache
from .domain import (
    _fmt_reminder,
    _generate_marker_dates,
    _new_uuid,
    _now_ts,
    _reminder_type,
    _today,
)
from .errors import InvalidArgumentError, InvalidDateRangeError
from .transport import _write_diff
from .validation import (
    require_account,
    require_category_ids_exist,
    require_entity,
    require_optional_account,
    validate_tool_args,
)


def _is_deleted_marker(marker: dict) -> bool:
    return marker.get("deleted") or marker.get("state") == "deleted"


def _is_planned_marker(marker: dict) -> bool:
    return not _is_deleted_marker(marker) and marker.get("state", "planned") == "planned"


def _marker_payload(base: dict[str, Any], *, marker_id: str, date: str, reminder_id: str, now: int) -> dict[str, Any]:
    return {
        "id": marker_id,
        "user": base["user"],
        "changed": now,
        "incomeInstrument": base.get("incomeInstrument"),
        "incomeAccount": base.get("incomeAccount"),
        "income": base.get("income", 0),
        "outcomeInstrument": base.get("outcomeInstrument"),
        "outcomeAccount": base.get("outcomeAccount"),
        "outcome": base.get("outcome", 0),
        "tag": base.get("tag"),
        "merchant": base.get("merchant"),
        "payee": base.get("payee"),
        "comment": base.get("comment"),
        "date": date,
        "reminder": reminder_id,
        "state": "planned",
        "isForecast": False,
        "notify": base.get("notify", True),
    }


def _ensure_write_confirmed(diff: dict[str, Any], expectations: dict[str, list[str]]) -> None:
    if not isinstance(diff, dict):
        raise RuntimeError("_write_diff returned invalid response")
    if not diff:
        return
    for entity, expected in expectations.items():
        expected_ids = {str(item_id) for item_id in expected}
        if not expected_ids:
            continue
        returned_ids = {
            str(item.get("id"))
            for item in diff.get(entity, [])
            if isinstance(item, dict) and item.get("id") is not None
        }
        cached_ids = set(_cache.CACHE.data.get(entity, {}))
        if expected_ids.isdisjoint(returned_ids) and expected_ids.isdisjoint(cached_ids):
            raise RuntimeError(f"_write_diff did not confirm {entity} write")


async def tool_get_reminders(args: dict) -> str:
    args = validate_tool_args("get_reminders", args)
    include_processed = args["include_processed"]
    active_only = args["active_only"]
    limit = args["limit"]
    markers_limit = args["markers_limit"]
    offset = args["offset"]
    marker_from = args.get("marker_from")
    marker_to = args.get("marker_to")
    category_id = args.get("category_id")
    r_type = args.get("type", "all")
    today_str = _today()

    reminders = _cache.CACHE.reminders()
    if active_only:
        reminders = [r for r in reminders if not r.get("endDate") or r["endDate"] >= today_str]

    # Filter by category
    if category_id:
        reminders = [r for r in reminders if category_id in (r.get("tag") or [])]

    # Filter by type
    if r_type and r_type != "all":
        reminders = [r for r in reminders if _reminder_type(r) == r_type]

    # Marker-based filtering mode
    if marker_from and marker_to:
        all_markers = _cache.CACHE.reminder_markers()
        result_list = []
        for r in reminders:
            markers = [m for m in all_markers if m.get("reminder") == r["id"] and not _is_deleted_marker(m)]
            if not include_processed:
                markers = [m for m in markers if _is_planned_marker(m)]
            # Filter markers to the requested date range
            markers = [m for m in markers if marker_from <= m.get("date", "") <= marker_to]
            if not markers:
                continue
            markers.sort(key=lambda m: m.get("date", ""))
            fmt = _fmt_reminder(r)
            fmt["type"] = _reminder_type(r)
            fmt["markers"] = [
                {"id": m["id"], "date": m.get("date"), "state": m.get("state"),
                 "income": m.get("income", 0), "outcome": m.get("outcome", 0)}
                for m in markers
            ]
            fmt["markers_total_outcome"] = sum(m.get("outcome", 0) for m in markers)
            fmt["markers_total_income"] = sum(m.get("income", 0) for m in markers)
            fmt["_sort_key"] = markers[0].get("date", "")
            result_list.append(fmt)

        result_list.sort(key=lambda x: x.pop("_sort_key"))
        total = len(result_list)
        eff_limit = min(limit, 200)
        result_list = result_list[offset:offset + eff_limit]

        output: dict[str, Any] = {"reminders": result_list, "mode": "marker_range", "marker_from": marker_from, "marker_to": marker_to}
        if total > offset + len(result_list):
            output["truncated"] = True
        output["total"] = total
        output["showing"] = len(result_list)
        output["offset"] = offset
        return json.dumps(output, ensure_ascii=False)

    # Legacy mode — sort by startDate
    reminders.sort(key=lambda r: r.get("startDate", ""), reverse=True)
    total = len(reminders)
    eff_limit = min(limit, 200)
    reminders = reminders[offset:offset + eff_limit]

    result_list = []
    for r in reminders:
        fmt = _fmt_reminder(r)
        fmt["type"] = _reminder_type(r)
        markers = [m for m in _cache.CACHE.reminder_markers() if m.get("reminder") == r["id"] and not _is_deleted_marker(m)]
        if not include_processed:
            markers = [m for m in markers if _is_planned_marker(m)]
        markers.sort(key=lambda m: m.get("date", ""))
        markers = markers[:markers_limit]
        if markers:
            fmt["markers"] = [
                {"id": m["id"], "date": m.get("date"), "state": m.get("state"),
                 "income": m.get("income", 0), "outcome": m.get("outcome", 0)}
                for m in markers
            ]
        result_list.append(fmt)

    output: dict[str, Any] = {"reminders": result_list}
    if total > offset + len(result_list):
        output["truncated"] = True
        output["total"] = total
        output["showing"] = len(result_list)
        output["offset"] = offset
    return json.dumps(output, ensure_ascii=False)

async def tool_create_reminder(args: dict) -> str:
    args = validate_tool_args("create_reminder", args)
    tx_type = args["type"]
    amount = args["amount"]
    account_id = args["account_id"]
    to_account_id = args.get("to_account_id")
    category_ids = args.get("category_ids")
    payee = args.get("payee")
    comment = args.get("comment")
    interval = args["interval"]
    step = args["step"]
    points = args.get("points")
    start_date = args.get("start_date") or _today()
    end_date = args.get("end_date")
    notify = args["notify"]
    generate_markers = args["generate_markers"]

    if tx_type == "transfer" and not to_account_id:
        raise InvalidArgumentError("to_account_id is required for transfer type")

    account = require_account(account_id)
    to_acct = require_optional_account(to_account_id, "to_account_id")
    require_category_ids_exist(category_ids)

    user_id = account.get("user")
    now = _now_ts()

    reminder: dict[str, Any] = {
        "id": _new_uuid(),
        "user": user_id,
        "changed": now,
        "incomeInstrument": account["instrument"] if tx_type == "income" else (to_acct["instrument"] if to_acct else account["instrument"]),
        "incomeAccount": account_id if tx_type == "income" else (to_account_id or account_id),
        "income": 0 if tx_type == "expense" else amount,
        "outcomeInstrument": account["instrument"] if tx_type != "income" else account["instrument"],
        "outcomeAccount": account_id if tx_type != "income" else account_id,
        "outcome": 0 if tx_type == "income" else amount,
        "tag": category_ids if category_ids else None,
        "merchant": None,
        "payee": payee,
        "comment": comment,
        "interval": interval,
        "step": step,
        "points": points if points is not None else [0],
        "startDate": start_date,
        "endDate": end_date,
        "notify": notify,
    }

    # Generate markers if requested
    markers = []
    if generate_markers > 0:
        dates = _generate_marker_dates(
            start_date, interval, step, points, end_date, generate_markers
        )
        for date_str in dates:
            marker = _marker_payload(
                reminder,
                marker_id=_new_uuid(),
                date=date_str,
                reminder_id=reminder["id"],
                now=now,
            )
            markers.append(marker)

    # Send reminder + markers in one request
    diff_data = {"reminder": [reminder]}
    if markers:
        diff_data["reminderMarker"] = markers

    diff = await _write_diff(diff_data)
    expectations = {"reminder": [reminder["id"]]}
    if markers:
        expectations["reminderMarker"] = [m["id"] for m in markers]
    _ensure_write_confirmed(diff, expectations)
    return json.dumps({
        "success": True,
        "reminder": {
            "id": reminder["id"], "type": tx_type, "amount": amount,
            "account": account.get("title"),
            "to_account": to_acct.get("title") if to_acct else None,
            "recurrence": f"Every {str(step) + ' ' if step > 1 else ''}{interval}{'s' if step > 1 else ''}",
            "start_date": start_date,
            "end_date": end_date or "indefinite",
            "points": reminder["points"],
        },
        "markers_generated": len(markers),
    }, ensure_ascii=False)


async def tool_update_reminder(args: dict) -> str:
    args = validate_tool_args("update_reminder", args)
    rid = args["id"]
    existing = require_entity("reminder", rid, f"Reminder not found: {rid}")

    require_category_ids_exist(args.get("category_ids"))

    updated = {**existing, "changed": _now_ts()}

    if "amount" in args:
        amount = float(args["amount"])
        is_income = existing.get("income", 0) > 0 and existing.get("outcome", 0) == 0
        is_expense = existing.get("outcome", 0) > 0 and existing.get("income", 0) == 0
        if is_income:
            updated["income"] = amount
        elif is_expense:
            updated["outcome"] = amount
        else:
            updated["income"] = amount
            updated["outcome"] = amount

    if "category_ids" in args:
        updated["tag"] = args["category_ids"]
    if "payee" in args:
        updated["payee"] = args["payee"]
    if "comment" in args:
        updated["comment"] = args["comment"]
    if "interval" in args:
        updated["interval"] = args["interval"]
    if "step" in args:
        updated["step"] = int(args["step"])
    if "points" in args:
        updated["points"] = args["points"]
    if "end_date" in args:
        updated["endDate"] = args["end_date"]
    if "notify" in args:
        updated["notify"] = args["notify"]

    if "points" in args:
        effective_step = int(updated.get("step", 1))
        for i, point in enumerate(updated.get("points") or []):
            if point >= effective_step:
                raise InvalidArgumentError(
                    f"points[{i}] must be less than step ({effective_step}), got {point}"
                )

    recurrence_changed = any(k in args for k in ("interval", "step", "points", "end_date"))
    if "end_date" in args and updated.get("endDate") and updated.get("startDate") and updated["endDate"] < updated["startDate"]:
        raise InvalidDateRangeError(
            f"end_date {updated['endDate']} is before start_date {updated['startDate']}"
        )

    diff: dict[str, list] = {"reminder": [updated]}
    marker_updates: list[dict] = []
    deletions: list[dict] = []
    new_markers: list[dict] = []

    if recurrence_changed:
        today_str = _today()
        now = _now_ts()
        for m in _cache.CACHE.reminder_markers():
            if m.get("reminder") != rid:
                continue
            if not _is_planned_marker(m):
                continue
            if m.get("date", "") < today_str:
                continue
            deletions.append({
                "id": m["id"],
                "object": "reminderMarker",
                "stamp": now,
                "user": m["user"],
            })

        regenerate_count = int(args.get("regenerate_markers", max(12, len(deletions))))
        start = updated.get("startDate") or today_str
        dates = _generate_marker_dates(
            start,
            updated.get("interval"),
            int(updated.get("step", 1)),
            updated.get("points") or None,
            updated.get("endDate"),
            regenerate_count,
        )
        for date_str in dates:
            new_markers.append(_marker_payload(
                updated,
                marker_id=_new_uuid(),
                date=date_str,
                reminder_id=rid,
                now=now,
            ))
    elif any(k in args for k in ("amount", "category_ids", "payee", "comment", "notify")):
        marker_now = _now_ts()
        for m in _cache.CACHE.reminder_markers():
            if m.get("reminder") == rid and _is_planned_marker(m):
                mu = {**m, "changed": marker_now}
                if updated.get("income", 0) > 0:
                    mu["income"] = updated["income"]
                if updated.get("outcome", 0) > 0:
                    mu["outcome"] = updated["outcome"]
                if "category_ids" in args:
                    mu["tag"] = updated.get("tag")
                if "payee" in args:
                    mu["payee"] = updated.get("payee")
                if "comment" in args:
                    mu["comment"] = updated.get("comment")
                if "notify" in args:
                    mu["notify"] = updated.get("notify", True)
                mu["isForecast"] = m.get("isForecast", False)
                marker_updates.append(mu)

    reminder_markers = marker_updates + new_markers
    if reminder_markers:
        diff["reminderMarker"] = reminder_markers
    if deletions:
        diff["deletion"] = deletions

    write_result = await _write_diff(diff)
    expectations = {"reminder": [rid]}
    if reminder_markers:
        expectations["reminderMarker"] = [m["id"] for m in reminder_markers]
    _ensure_write_confirmed(write_result, expectations)

    synced_count = len(marker_updates) + len(new_markers)
    return json.dumps({
        "success": True,
        "message": f"Reminder updated, {synced_count} planned marker(s) synced",
        "id": rid,
        "markers_updated": synced_count,
        "markers_deleted": len(deletions),
    }, ensure_ascii=False)


async def tool_delete_reminder(args: dict) -> str:
    args = validate_tool_args("delete_reminder", args)
    rid = args["id"]
    existing = require_entity("reminder", rid, f"Reminder not found: {rid}")

    now = _now_ts()
    deletions: list[dict] = [{"id": rid, "object": "reminder", "stamp": now, "user": existing["user"]}]

    for m in _cache.CACHE.reminder_markers():
        if m.get("reminder") == rid:
            deletions.append({"id": m["id"], "object": "reminderMarker", "stamp": now, "user": m["user"]})

    await _write_diff({"deletion": deletions})
    return json.dumps({
        "success": True,
        "message": f"Reminder deleted with {len(deletions) - 1} associated markers",
        "id": rid,
    }, ensure_ascii=False)


async def tool_create_reminder_marker(args: dict) -> str:
    args = validate_tool_args("create_reminder_marker", args)
    tx_type = args["type"]
    amount = args["amount"]
    account_id = args["account_id"]
    to_account_id = args.get("to_account_id")
    category_ids = args.get("category_ids")
    payee = args.get("payee")
    comment = args.get("comment")
    date = args["date"]
    reminder_id = args.get("reminder_id")
    notify = args["notify"]
    if tx_type == "transfer" and not to_account_id:
        raise InvalidArgumentError("to_account_id is required for transfer type")

    account = require_account(account_id)
    to_acct = require_optional_account(to_account_id, "to_account_id")
    require_category_ids_exist(category_ids)

    user_id = account.get("user")
    now = _now_ts()

    # If no reminder_id, create a one-time Reminder
    effective_reminder_id = reminder_id
    auto_created = False
    one_time: dict[str, Any] | None = None
    if not effective_reminder_id:
        one_time: dict[str, Any] = {
            "id": _new_uuid(),
            "user": user_id,
            "changed": now,
            "incomeInstrument": account["instrument"] if tx_type == "income" else (to_acct["instrument"] if to_acct else account["instrument"]),
            "incomeAccount": account_id if tx_type == "income" else (to_account_id or account_id),
            "income": 0 if tx_type == "expense" else amount,
            "outcomeInstrument": account["instrument"],
            "outcomeAccount": account_id if tx_type != "income" else account_id,
            "outcome": 0 if tx_type == "income" else amount,
            "tag": category_ids if category_ids else None,
            "merchant": None,
            "payee": payee,
            "comment": comment,
            "interval": None,
            "step": 0,
            "points": [0],
            "startDate": date,
            "endDate": None,
            "notify": notify,
        }
        effective_reminder_id = one_time["id"]
        auto_created = True
    else:
        require_entity("reminder", effective_reminder_id, f"Reminder not found: {effective_reminder_id}")

    marker_base = one_time or {
        "user": user_id,
        "incomeInstrument": account["instrument"] if tx_type == "income" else (to_acct["instrument"] if to_acct else account["instrument"]),
        "incomeAccount": account_id if tx_type == "income" else (to_account_id or account_id),
        "income": 0 if tx_type == "expense" else amount,
        "outcomeInstrument": account["instrument"],
        "outcomeAccount": account_id if tx_type != "income" else account_id,
        "outcome": 0 if tx_type == "income" else amount,
        "tag": category_ids if category_ids else None,
        "merchant": None,
        "payee": payee,
        "comment": comment,
        "notify": notify,
    }
    marker = _marker_payload(
        marker_base,
        marker_id=_new_uuid(),
        date=date,
        reminder_id=effective_reminder_id,
        now=now,
    )

    diff_data: dict[str, list[dict[str, Any]]] = {}
    if one_time is not None:
        diff_data["reminder"] = [one_time]
    diff_data["reminderMarker"] = [marker]

    diff = await _write_diff(diff_data)
    expectations = {"reminderMarker": [marker["id"]]}
    if one_time is not None:
        expectations["reminder"] = [one_time["id"]]
    _ensure_write_confirmed(diff, expectations)
    return json.dumps({
        "success": True,
        "reminder_marker": {
            "id": marker["id"], "type": tx_type, "amount": amount,
            "account": account.get("title"),
            "to_account": to_acct.get("title") if to_acct else None,
            "date": date, "state": "planned",
            "reminder_id": effective_reminder_id,
            "auto_created_reminder": auto_created,
        },
    }, ensure_ascii=False)


async def tool_delete_reminder_marker(args: dict) -> str:
    args = validate_tool_args("delete_reminder_marker", args)
    mid = args["id"]
    marker = require_entity("reminderMarker", mid, f"ReminderMarker not found: {mid}")

    await _write_diff({
        "deletion": [{"id": mid, "object": "reminderMarker", "stamp": _now_ts(), "user": marker["user"]}],
    })
    return json.dumps({"success": True, "message": "ReminderMarker deleted", "id": mid}, ensure_ascii=False)
