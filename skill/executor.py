#!/usr/bin/env python3
"""
MCP Skill Executor for ZenMoney
================================
Handles dynamic communication with the ZenMoney MCP server.
Allows using ZenMoney as a Claude Code skill without direct MCP connection.

Usage:
    python executor.py --list                    # List available tools
    python executor.py --describe get_accounts   # Get tool schema
    python executor.py --call '{"tool": "get_accounts", "arguments": {}}'  # Call a tool

Requirements:
    pip install mcp
"""

import json
import sys
import asyncio
import argparse
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    print("Warning: mcp package not installed. Install with: pip install mcp", file=sys.stderr)


async def main():
    parser = argparse.ArgumentParser(description="ZenMoney MCP Skill Executor")
    parser.add_argument("--call", help="JSON tool call: {\"tool\": \"name\", \"arguments\": {...}}")
    parser.add_argument("--describe", help="Get detailed schema for a tool")
    parser.add_argument("--list", action="store_true", help="List all available tools")

    args = parser.parse_args()

    config_path = Path(__file__).parent / "mcp-config.json"
    if not config_path.exists():
        print(f"Error: {config_path} not found. Copy mcp-config.example.json and fill in your token.", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    if not HAS_MCP:
        print("Error: mcp package not installed. Install with: pip install mcp", file=sys.stderr)
        sys.exit(1)

    server_params = StdioServerParameters(
        command=config["command"],
        args=config.get("args", []),
        env=config.get("env")
    )

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            session = ClientSession(read_stream, write_stream)

            # Monkey-patch validation to avoid MCP SDK schema compatibility issues
            async def _no_validate(name, result):
                pass
            session._validate_tool_result = _no_validate

            try:
                await session.__aenter__()
                await session.initialize()

                if args.list:
                    response = await session.list_tools()
                    tools = [
                        {"name": tool.name, "description": tool.description}
                        for tool in response.tools
                    ]
                    print(json.dumps(tools, indent=2, ensure_ascii=False))

                elif args.describe:
                    response = await session.list_tools()
                    schema = None
                    for tool in response.tools:
                        if tool.name == args.describe:
                            schema = {
                                "name": tool.name,
                                "description": tool.description,
                                "inputSchema": tool.inputSchema
                            }
                            break
                    if schema:
                        print(json.dumps(schema, indent=2, ensure_ascii=False))
                    else:
                        print(f"Tool not found: {args.describe}", file=sys.stderr)
                        sys.exit(1)

                elif args.call:
                    call_data = json.loads(args.call)
                    response = await session.call_tool(
                        call_data["tool"],
                        call_data.get("arguments", {})
                    )

                    if isinstance(response.content, list):
                        for item in response.content:
                            if hasattr(item, 'text'):
                                print(item.text)
                            else:
                                print(json.dumps(
                                    item.__dict__ if hasattr(item, '__dict__') else item,
                                    indent=2, ensure_ascii=False
                                ))
                    else:
                        print(json.dumps(
                            response.content.__dict__ if hasattr(response.content, '__dict__') else response.content,
                            indent=2, ensure_ascii=False
                        ))
                else:
                    parser.print_help()
            finally:
                await session.__aexit__(None, None, None)

    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
