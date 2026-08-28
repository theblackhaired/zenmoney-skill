from __future__ import annotations

import time
from typing import Any

import httpx

from . import config
from . import cache as _cache
from .errors import ApiRequestError, AuthenticationError


_client: httpx.AsyncClient | None = None
_CONFIRMABLE_ENTITY_KEYS = {
    "account",
    "budget",
    "reminder",
    "reminderMarker",
    "transaction",
}
_CONFIRMATION_FIELDS = {
    "account": {
        "user", "instrument", "type", "role", "company", "title", "syncID",
        "balance", "startBalance", "creditLimit", "inBalance", "savings",
        "enableCorrection", "enableSMS", "archive", "capitalization", "percent",
        "startDate", "endDateOffset", "endDateOffsetInterval", "payoffStep",
        "payoffInterval",
    },
    "budget": {"user", "tag", "date", "income", "incomeLock", "outcome", "outcomeLock"},
    "reminder": {
        "user", "incomeInstrument", "incomeAccount", "income",
        "outcomeInstrument", "outcomeAccount", "outcome", "tag", "merchant",
        "payee", "comment", "interval", "step", "points", "startDate", "endDate",
        "notify",
    },
    "reminderMarker": {
        "user", "incomeInstrument", "incomeAccount", "income",
        "outcomeInstrument", "outcomeAccount", "outcome", "tag", "merchant",
        "payee", "comment", "date", "reminder", "state", "isForecast", "notify",
    },
    "transaction": {
        "user", "deleted", "incomeInstrument", "incomeAccount", "income",
        "outcomeInstrument", "outcomeAccount", "outcome", "tag", "payee",
        "comment", "date",
    },
}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=60.0)
    return _client


async def _close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def _api_post(endpoint: str, body: dict) -> Any:
    """POST to ZenMoney API, returns parsed JSON."""
    if not config.TOKEN:
        raise RuntimeError("ZENMONEY_TOKEN is not set. Set env var or add to config.json")
    client = _get_client()
    try:
        resp = await client.post(
            f"{config.BASE_URL}{endpoint}",
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.TOKEN}",
            },
        )
    except httpx.RequestError as exc:
        raise ApiRequestError(
            endpoint=endpoint,
            status_code=None,
            message=f"ZenMoney API request failed before receiving a response: {endpoint}",
        ) from exc
    if resp.status_code == 401:
        raise AuthenticationError(endpoint=endpoint)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ApiRequestError(endpoint=endpoint, status_code=resp.status_code) from exc
    return resp.json()


async def _sync(extra: dict | None = None) -> dict:
    """Incremental or full sync via /v8/diff/."""
    force_fetch = extra.get("forceFetch") if extra else None
    body: dict[str, Any] = {
        "currentClientTimestamp": int(time.time()),
        # A non-zero cursor makes ZenMoney return an incremental diff even
        # when forceFetch is present. Store replacement requires a full sync.
        "serverTimestamp": 0 if force_fetch else _cache.CACHE.server_timestamp,
    }
    if extra:
        body.update(extra)
    diff = await _api_post("/v8/diff/", body)
    _apply_and_save_diff(diff, force_fetch=force_fetch)
    return diff


async def _write_diff(changes: dict) -> dict:
    """Write entities through diff and update cache."""
    body: dict[str, Any] = {
        "currentClientTimestamp": int(time.time()),
        "serverTimestamp": _cache.CACHE.server_timestamp,
    }
    body.update(changes)
    diff = await _api_post("/v8/diff/", body)
    _apply_and_save_diff(diff)
    verification = await _verify_written_changes(changes)
    _confirm_written_changes(changes, verification)
    return diff


def _apply_and_save_diff(diff: dict[str, Any], *, force_fetch: list[str] | None = None) -> None:
    try:
        _apply_diff_to_cache(diff, force_fetch=force_fetch)
        _cache.CACHE.save()
    except config.LostUpdateError:
        _cache.CACHE.load()
        diff_timestamp = diff.get("serverTimestamp")
        if diff_timestamp is not None and int(diff_timestamp or 0) < _cache.CACHE.server_timestamp:
            return
        _apply_diff_to_cache(diff, force_fetch=force_fetch)
        _cache.CACHE.save()


def _apply_diff_to_cache(diff: dict[str, Any], *, force_fetch: list[str] | None = None) -> None:
    if force_fetch:
        _cache.CACHE.apply_force_fetch_diff(diff, force_fetch)
        return
    _cache.CACHE.apply_diff(diff)


async def _verify_written_changes(changes: dict[str, Any]) -> dict[str, Any]:
    entity_types = _changed_entity_types(changes)
    if not entity_types:
        return {}
    return await _sync({"forceFetch": sorted(entity_types)})


def _changed_entity_types(changes: dict[str, Any]) -> set[str]:
    entity_types = {
        key
        for key, value in changes.items()
        if key in _CONFIRMABLE_ENTITY_KEYS and isinstance(value, list) and value
    }
    for item in changes.get("deletion") or []:
        if not isinstance(item, dict):
            continue
        obj_type = item.get("object")
        if obj_type in _CONFIRMABLE_ENTITY_KEYS:
            entity_types.add(str(obj_type))
    return entity_types


def _confirm_written_changes(changes: dict[str, Any], verification: dict[str, Any]) -> None:
    missing, mismatched = _unconfirmed_entities(changes, verification)
    still_present = _still_present_deletions(changes, verification)
    if missing or mismatched or still_present:
        details: dict[str, Any] = {}
        if missing:
            details["missing"] = missing
        if mismatched:
            details["mismatched"] = mismatched
        if still_present:
            details["still_present"] = still_present
        raise RuntimeError(f"ZenMoney write was not confirmed by server sync: {details}")


def _unconfirmed_entities(
    changes: dict[str, Any],
    verification: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    missing: list[dict[str, str]] = []
    mismatched: list[dict[str, Any]] = []
    for entity_type, items in changes.items():
        if entity_type not in _CONFIRMABLE_ENTITY_KEYS or not isinstance(items, list):
            continue
        confirmed_items = verification.get(entity_type)
        if not isinstance(confirmed_items, list):
            for item in items:
                expected_key = _entity_key(entity_type, item)
                if expected_key is not None:
                    missing.append({"object": entity_type, "id": expected_key})
            continue
        confirmed_by_key = {
            key: item
            for item in confirmed_items
            if isinstance(item, dict)
            for key in [_entity_key(entity_type, item)]
            if key is not None
        }
        for item in items:
            expected_key = _entity_key(entity_type, item)
            if expected_key is None:
                continue
            confirmed = confirmed_by_key.get(expected_key)
            if confirmed is None:
                missing.append({"object": entity_type, "id": expected_key})
                continue
            differing_fields = _differing_confirmation_fields(entity_type, item, confirmed)
            if differing_fields:
                mismatched.append({
                    "object": entity_type,
                    "id": expected_key,
                    "fields": differing_fields,
                })
    return missing, mismatched


def _differing_confirmation_fields(
    entity_type: str,
    submitted: dict[str, Any],
    confirmed: dict[str, Any],
) -> list[str]:
    fields = _CONFIRMATION_FIELDS.get(entity_type, set())
    return sorted(
        field
        for field in fields
        if field in submitted and not _confirmation_values_equal(field, submitted[field], confirmed.get(field))
    )


def _confirmation_values_equal(field: str, submitted: Any, confirmed: Any) -> bool:
    if field in {"tag", "syncID", "points"} and submitted in (None, []) and confirmed in (None, []):
        return True
    if (
        isinstance(submitted, (int, float))
        and not isinstance(submitted, bool)
        and isinstance(confirmed, (int, float))
        and not isinstance(confirmed, bool)
    ):
        return float(submitted) == float(confirmed)
    return submitted == confirmed


def _still_present_deletions(changes: dict[str, Any], verification: dict[str, Any]) -> list[dict[str, str]]:
    still_present: list[dict[str, str]] = []
    for item in changes.get("deletion") or []:
        if not isinstance(item, dict):
            continue
        obj_type = item.get("object")
        obj_id = str(item.get("id", ""))
        if obj_type not in _CONFIRMABLE_ENTITY_KEYS or not obj_id:
            continue
        verified_items = verification.get(str(obj_type))
        verified_deleted = any(
            isinstance(deletion, dict)
            and deletion.get("object") == obj_type
            and str(deletion.get("id", "")) == obj_id
            for deletion in verification.get("deletion") or []
        )
        verified_absent = (
            isinstance(verified_items, list)
            and obj_id not in {
                str(verified.get("id", ""))
                for verified in verified_items
                if isinstance(verified, dict)
            }
        )
        if verified_absent and _cache.CACHE.get(str(obj_type), obj_id) is not None:
            _apply_and_save_diff({"deletion": [item]})
        if not verified_deleted and not verified_absent and _cache.CACHE.get(str(obj_type), obj_id) is not None:
            still_present.append({"object": str(obj_type), "id": obj_id})
    return still_present


def _entity_key(entity_type: str, item: dict[str, Any]) -> str | None:
    if entity_type == "budget":
        return _cache.CACHE._budget_key(item)
    entity_id = item.get("id")
    if entity_id is None:
        return None
    return str(entity_id)
