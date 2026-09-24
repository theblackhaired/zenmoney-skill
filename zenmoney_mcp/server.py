"""Standalone ZenMoney MCP adapter."""

from __future__ import annotations

import asyncio
import json

from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
)

from .catalog import TOOL_BY_NAME, TOOLS, VOLUMINOUS_READ_TOOLS, WRITE_TOOLS
from .presentation import present

_CALL_LOCK = asyncio.Lock()


def _error(code: str, message: str) -> CallToolResult:
    payload = {"status": "error", "code": code, "error": message}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload, is_error=True,
    )


async def list_tools(
    _ctx: ServerRequestContext, _params: PaginatedRequestParams | None,
) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def call_tool(
    _ctx: ServerRequestContext, params: CallToolRequestParams,
) -> CallToolResult:
    name = params.name
    if name not in TOOL_BY_NAME:
        return _error("UNKNOWN_TOOL", f"Unknown tool: {name}")
    args = params.arguments or {}
    if not isinstance(args, dict):
        return _error("INVALID_ARGUMENT", "Tool arguments must be an object")
    if name in WRITE_TOOLS:
        if args.get("confirm_write") is not True:
            return _error("WRITE_NOT_CONFIRMED", "Set confirm_write=true to perform this write")
        args = {key: value for key, value in args.items() if key != "confirm_write"}
        mode = "full"
    else:
        mode = args.get("response_mode", "compact" if name in VOLUMINOUS_READ_TOOLS else "full")
        if mode not in {"compact", "full"}:
            return _error("INVALID_ARGUMENT", "response_mode must be compact or full")
        args = {key: value for key, value in args.items() if key != "response_mode"}
    # The core uses process-wide state, including a current snapshot.
    async with _CALL_LOCK:
        from zenmoney.tools import _run_tool_fresh

        raw = await _run_tool_fresh(name, args)
    rendered, structured, is_error = present(raw, mode=mode)
    return CallToolResult(
        content=[TextContent(type="text", text=rendered)],
        structured_content=structured if isinstance(structured, dict) else None,
        is_error=is_error,
    )


server = Server(
    "zenmoney", version="1.0.0", on_list_tools=list_tools, on_call_tool=call_tool,
)


def streamable_http_app(**kwargs):
    """Expose an ASGI app to the authenticated remote transport wrapper."""
    kwargs.setdefault("stateless_http", True)
    return server.streamable_http_app(**kwargs)
