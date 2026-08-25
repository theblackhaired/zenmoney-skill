from __future__ import annotations

import enum
import json
from typing import Any, Awaitable, Callable

from . import cache as _cache
from .transport import _close_client, _sync
from .validation import map_exception, validate_tool_args


class SyncPolicy(str, enum.Enum):
    CACHE_ONLY = "cache_only"
    PREFETCH_SYNC = "prefetch_sync"
    FORCED_LIVE = "forced_live"


SYNC_POLICY_CACHE_ONLY = SyncPolicy.CACHE_ONLY
SYNC_POLICY_PREFETCH_SYNC = SyncPolicy.PREFETCH_SYNC
SYNC_POLICY_FORCED_LIVE = SyncPolicy.FORCED_LIVE


TOOL_SYNC_POLICY: dict[str, SyncPolicy] = {
    "check_auth_status": SYNC_POLICY_FORCED_LIVE,
    "setup_budget_mode": SYNC_POLICY_CACHE_ONLY,
    "suggest": SYNC_POLICY_FORCED_LIVE,
}

CACHE_DEPENDENT_VALIDATION_CODES = {
    "AMBIGUOUS_CATEGORY",
    "ENTITY_NOT_FOUND",
    "INVALID_CATEGORY",
}


def get_sync_policy(name: str) -> SyncPolicy:
    return TOOL_SYNC_POLICY.get(name, SYNC_POLICY_PREFETCH_SYNC)


def is_cache_only_tool(name: str) -> bool:
    return get_sync_policy(name) == SYNC_POLICY_CACHE_ONLY


def _error_payload(exc: Exception) -> dict[str, Any]:
    to_payload = getattr(exc, "to_payload", None)
    if callable(to_payload):
        return to_payload()
    return map_exception(exc).to_payload()


def _is_cache_dependent_validation_error(exc: Exception) -> bool:
    return _error_payload(exc).get("code") in CACHE_DEPENDENT_VALIDATION_CODES


async def run_tool(
    name: str,
    args: dict,
    tools: dict[str, Callable[[dict], Awaitable[str]]],
    migrate_account_meta: Callable[[], None],
) -> str:
    try:
        handler = tools.get(name)
        if not handler:
            return json.dumps({
                "status": "error",
                "code": "UNKNOWN_TOOL",
                "error": f"Unknown tool: {name}. Use --list to see available tools.",
            }, ensure_ascii=False)
        sync_policy = get_sync_policy(name)
        if sync_policy != SYNC_POLICY_CACHE_ONLY:
            _cache.CACHE.load()
        migrate_account_meta()
        did_prefetch_sync = False
        try:
            validated_args = validate_tool_args(name, args)
        except Exception as exc:
            if sync_policy == SYNC_POLICY_PREFETCH_SYNC and _is_cache_dependent_validation_error(exc):
                await _sync()
                did_prefetch_sync = True
                validated_args = validate_tool_args(name, args)
            else:
                raise
        if sync_policy == SYNC_POLICY_PREFETCH_SYNC and not did_prefetch_sync:
            await _sync()
            # Syntax validation intentionally happens before network access, but
            # entity existence must be checked against the cache produced by
            # the prefetch. Revalidate the original arguments so the internal
            # validated marker cannot preserve stale cache-dependent results.
            validated_args = validate_tool_args(name, args)
        return await handler(validated_args)
    except Exception as exc:
        return json.dumps(_error_payload(exc), ensure_ascii=False)
    finally:
        await _close_client()
