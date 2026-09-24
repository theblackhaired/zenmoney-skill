"""Run the local MCP stdio transport."""

from __future__ import annotations

import asyncio

from mcp.server.stdio import stdio_server

from .server import server


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
