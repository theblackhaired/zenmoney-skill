"""Launch the local MCP server from any working directory."""

from __future__ import annotations

import asyncio

from zenmoney_mcp.__main__ import main

if __name__ == "__main__":
    asyncio.run(main())
