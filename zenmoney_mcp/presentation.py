"""Project large read results without changing the underlying calculation."""

from __future__ import annotations

import json
from typing import Any

_MAX_ITEMS = 5
_MAX_DEPTH = 5
_MAX_FIELDS = 24
_MAX_NODES = 120
_MAX_TEXT = 240
_PRIORITY = (
    "status", "code", "error", "summary", "metadata", "total", "balance",
    "income", "outcome", "amount", "currency", "currency_code", "period",
    "start_date", "end_date", "count",
)


def compact_payload(payload: Any) -> Any:
    """Return a bounded view with an explicit way to retrieve every omitted value."""
    omitted = False
    visited = 0

    def project(value: Any, depth: int) -> Any:
        nonlocal omitted, visited
        visited += 1
        if visited > _MAX_NODES:
            omitted = True
            return {"omitted": True}
        if isinstance(value, list):
            if depth >= _MAX_DEPTH:
                omitted = True
                return {"count": len(value), "items_omitted": True}
            items = [project(item, depth + 1) for item in value[:_MAX_ITEMS]]
            if len(value) > _MAX_ITEMS:
                omitted = True
                return {"count": len(value), "items": items, "remaining_items_omitted": len(value) - _MAX_ITEMS}
            return items
        if isinstance(value, dict):
            if depth >= _MAX_DEPTH:
                omitted = True
                return {"fields_omitted": True, "field_count": len(value)}
            keys = [key for key in _PRIORITY if key in value]
            keys.extend(key for key in value if key not in keys)
            chosen = keys[:_MAX_FIELDS]
            result = {key: project(value[key], depth + 1) for key in chosen}
            if len(value) > len(chosen):
                omitted = True
                result["_fields_omitted"] = len(value) - len(chosen)
            return result
        if isinstance(value, str) and len(value) > _MAX_TEXT:
            omitted = True
            return value[:_MAX_TEXT] + "…"
        return value

    projected = project(payload, 0)
    if omitted:
        if isinstance(projected, dict):
            projected = {**projected, "_response": {
                "mode": "compact", "truncated": True,
                "message": "Call this tool again with response_mode=full for the complete result.",
            }}
        else:
            projected = {"data": projected, "_response": {
                "mode": "compact", "truncated": True,
                "message": "Call this tool again with response_mode=full for the complete result.",
            }}
    return projected


def present(raw: str, *, mode: str) -> tuple[str, Any | None, bool]:
    """Return text, parsed structured data when possible, and error flag."""
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw, None, False
    is_error = isinstance(payload, dict) and payload.get("status") == "error"
    if mode == "full" or is_error:
        return raw, payload, is_error
    projected = compact_payload(payload)
    return json.dumps(projected, ensure_ascii=False, separators=(",", ":")), projected, False
