#!/usr/bin/env python3
"""ZenMoney CLI — standalone Python executor for OpenClaw AgentSkill.

Usage:
  python cli.py --list
  python cli.py --describe get_accounts
  python cli.py --call '{"tool":"get_accounts","arguments":{}}'
  '{"tool":"get_accounts","arguments":{}}' | python cli.py --call -
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from zenmoney import config
from zenmoney.dispatch import is_cache_only_tool
from zenmoney.tools import TOOL_DOCS, _run_tool


def _print_tool_result(result: str) -> None:
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        print(result)
        return

    if isinstance(payload, dict) and payload.get("status") == "error":
        print(result, file=sys.stderr)
        sys.exit(1)

    print(result)


def _parse_call_payload(raw: str) -> dict:
    if raw == "-":
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("Call payload must be a JSON object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="ZenMoney CLI executor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List all tools")
    group.add_argument("--describe", type=str, metavar="TOOL", help="Describe a tool")
    group.add_argument("--call", type=str, metavar="JSON", help='Call: {"tool":"name","arguments":{...}}')
    parsed = parser.parse_args()

    if parsed.list:
        tools = [{"name": n, "description": d["desc"]} for n, d in TOOL_DOCS.items()]
        print(json.dumps(tools, ensure_ascii=False, indent=2))
        return

    if parsed.describe:
        doc = TOOL_DOCS.get(parsed.describe)
        if not doc:
            print(json.dumps({"error": f"Unknown tool: {parsed.describe}"}), file=sys.stderr)
            sys.exit(1)
        print(json.dumps(
            {"name": parsed.describe, "description": doc["desc"], "parameters": doc["params"]},
            ensure_ascii=False, indent=2,
        ))
        return

    if parsed.call:
        try:
            payload = _parse_call_payload(parsed.call)
        except ValueError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)

        tool_name = payload.get("tool", "")
        arguments = payload.get("arguments", {})

        if tool_name not in TOOL_DOCS:
            print(json.dumps({"error": f"Unknown tool: {tool_name}. Use --list to see available tools."}), file=sys.stderr)
            sys.exit(1)

        if not config.TOKEN and not is_cache_only_tool(tool_name):
            if config.CONFIG_LOAD_ERROR is not None:
                print(json.dumps(config.CONFIG_LOAD_ERROR.to_payload(), ensure_ascii=False), file=sys.stderr)
                sys.exit(1)
            print(json.dumps({"error": "ZENMONEY_TOKEN not set. Set env var or add to config.json"}), file=sys.stderr)
            sys.exit(1)

        try:
            result = asyncio.run(_run_tool(tool_name, arguments))
            _print_tool_result(result)
        except Exception as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
