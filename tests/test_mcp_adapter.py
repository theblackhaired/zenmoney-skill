"""MCP boundary contracts without live financial API calls."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from jsonschema import Draft202012Validator
from mcp import Client
from mcp.types import CallToolRequestParams

from zenmoney_mcp.catalog import (
    READ_TOOLS,
    TOOL_BY_NAME,
    TOOLS,
    WRITE_TOOLS,
    default_state_dir,
)
from zenmoney_mcp.presentation import present
from zenmoney_mcp.server import call_tool, server


class CatalogTests(unittest.TestCase):
    def test_required_fields_for_every_write_and_key_reads(self):
        expected = {
            "setup_budget_mode": {"mode"},
            "create_transaction": {"type", "amount", "account_id"},
            "update_transaction": {"id"},
            "delete_transaction": {"id"},
            "create_account": {"title", "type", "currency_id"},
            "create_budget": {"month", "category"},
            "update_budget": {"month", "category"},
            "delete_budget": {"month", "category"},
            "create_reminder": {"type", "amount", "account_id", "interval"},
            "update_reminder": {"id"},
            "delete_reminder": {"id"},
            "create_reminder_marker": {"type", "amount", "account_id", "date"},
            "delete_reminder_marker": {"id"},
            "get_budgets": {"month"},
            "analyze_budget_detailed": {"period"},
            "get_analytics": {"report"},
            "suggest": {"payee"},
        }
        self.assertEqual(set(expected) - WRITE_TOOLS, {
            "get_budgets", "analyze_budget_detailed", "get_analytics", "suggest",
        })
        for name, required in expected.items():
            advertised = set(TOOL_BY_NAME[name].input_schema["required"])
            if name in WRITE_TOOLS:
                required = required | {"confirm_write"}
            self.assertEqual(advertised, required, name)
        for name in set(TOOL_BY_NAME) - set(expected):
            self.assertFalse(TOOL_BY_NAME[name].input_schema.get("required"), name)

    def test_schema_types_enums_and_conditional_requirements(self):
        schemas = {name: Draft202012Validator(tool.input_schema) for name, tool in TOOL_BY_NAME.items()}
        self.assertFalse(schemas["create_transaction"].is_valid({"confirm_write": True, "type": "expense", "account_id": "id"}))
        self.assertTrue(schemas["create_transaction"].is_valid({
            "confirm_write": True, "type": "expense", "account_id": "id", "amount": "12.50",
        }))
        self.assertFalse(schemas["create_transaction"].is_valid({
            "confirm_write": True, "type": "transfer", "account_id": "id", "amount": 12.5,
        }))
        self.assertTrue(schemas["create_transaction"].is_valid({
            "confirm_write": True, "type": "transfer", "account_id": "id", "amount": 12.5,
            "to_account_id": "other",
        }))
        self.assertFalse(schemas["get_transactions"].is_valid({"period": "week"}))
        self.assertTrue(schemas["get_transactions"].is_valid({"period": "week", "first_weekday": 0}))
        self.assertTrue(schemas["get_transactions"].is_valid({
            "start_date": "2026-09-01", "end_date": "2026-09-24",
        }))
        self.assertFalse(schemas["get_transactions"].is_valid({"start_date": "2026-09-01"}))
        for name in ("get_category_report", "get_money_flow", "get_income_outcome_comparison", "get_balance_trend"):
            self.assertTrue(schemas[name].is_valid({"period": "month", "period_offset": -1}), name)
        self.assertTrue(schemas["get_balance_trend"].is_valid({"period": "month", "currency": 1}))
        self.assertTrue(schemas["get_analytics"].is_valid({"period": "month", "report": "turnover"}))
        self.assertFalse(schemas["get_reminders"].is_valid({"marker_from": "2026-09-01"}))

    def test_default_state_dir_is_outside_source_tree(self):
        home = Path("C:/Users/example")
        self.assertEqual(
            default_state_dir(system="nt", environ={}, home=home),
            home / "AppData" / "Local" / "zenmoney-mcp",
        )
        self.assertEqual(
            default_state_dir(system="posix", environ={}, home=Path("/home/example")),
            Path("/home/example/.local/share/zenmoney-mcp"),
        )

    def test_catalog_matches_core_and_write_schema_requires_confirmation(self):
        from zenmoney.tools import TOOL_DOCS

        self.assertEqual({tool.name for tool in TOOLS}, set(TOOL_DOCS))
        self.assertEqual(len(TOOLS), 28)
        self.assertEqual(READ_TOOLS | WRITE_TOOLS, set(TOOL_DOCS))
        self.assertFalse(READ_TOOLS & WRITE_TOOLS)
        for tool in TOOLS:
            self.assertEqual(tool.description, TOOL_DOCS[tool.name]["desc"])
            self.assertEqual(tool.annotations.read_only_hint, tool.name in READ_TOOLS)
            if tool.name in WRITE_TOOLS:
                self.assertIn("confirm_write", tool.input_schema["required"])
                self.assertTrue(tool.input_schema["properties"]["confirm_write"]["const"])

    def test_every_tool_advertises_both_scopes_for_initial_consent(self):
        for tool in TOOLS:
            wire = tool.model_dump(by_alias=True, exclude_none=True)
            self.assertEqual(wire["_meta"]["securitySchemes"], [
                {"type": "oauth2", "scopes": ["finance:read", "finance:write"]},
            ], tool.name)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_removed_analytics_alias_reaches_core_with_specific_details(self):
        result = await call_tool(None, CallToolRequestParams(
            name="get_analytics",
            arguments={"period": "month", "report": "outcome", "account_id": "old-id"},
        ))
        self.assertTrue(result.is_error)
        self.assertIn("Removed argument 'account_id'", result.content[0].text)
        self.assertEqual(result.structured_content["details"]["removed_argument"], "account_id")
        self.assertEqual(result.structured_content["details"]["replacement"],
                         "account_scope=selected with account_ids")

    async def test_removed_alias_error_survives_sdk_client(self):
        async with Client(server) as client:
            result = await client.call_tool("get_analytics", {
                "period": "month", "report": "outcome", "account_id": "old-id",
            })
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["details"]["removed_argument"], "account_id")

    async def test_compact_bounds_nested_summary_and_metadata_but_preserves_scalars(self):
        payload = {
            "status": "success",
            "summary": {"total": 123.45, "currency": "RUB", "rows": [
                {"label": "a" * 1000, "amount": i} for i in range(300)
            ]},
            "metadata": {"period": "2026-09", "lookup": {str(i): "x" * 1000 for i in range(300)}},
        }
        raw = json.dumps(payload)
        compact, projected, is_error = present(raw, mode="compact")
        self.assertFalse(is_error)
        self.assertLess(len(compact), 15_000)
        self.assertEqual(projected["summary"]["total"], 123.45)
        self.assertEqual(projected["summary"]["currency"], "RUB")
        self.assertEqual(projected["metadata"]["period"], "2026-09")
        self.assertTrue(projected["_response"]["truncated"])

    async def test_write_guard_precedes_core_and_strips_guard_on_confirmed_call(self):
        with patch("zenmoney.tools._run_tool_fresh", new_callable=AsyncMock) as run:
            denied = await call_tool(None, CallToolRequestParams(
                name="setup_budget_mode", arguments={"mode": "balance_vs_expense"},
            ))
            self.assertTrue(denied.is_error)
            self.assertEqual(denied.structured_content["code"], "WRITE_NOT_CONFIRMED")
            run.assert_not_awaited()

            raw = '{"status":"success","mode":"balance_vs_expense"}'
            run.return_value = raw
            accepted = await call_tool(None, CallToolRequestParams(
                name="setup_budget_mode",
                arguments={"mode": "balance_vs_expense", "confirm_write": True},
            ))
            self.assertEqual(accepted.content[0].text, raw)
            run.assert_awaited_once_with("setup_budget_mode", {"mode": "balance_vs_expense"})

    async def test_full_parity_and_compact_truncation(self):
        payload = {"status": "success", "summary": {"total": 987.5}, "transactions": [
            {"id": str(i), "amount": i} for i in range(9)
        ]}
        raw = json.dumps(payload, ensure_ascii=False)
        with patch("zenmoney.tools._run_tool_fresh", new=AsyncMock(return_value=raw)):
            full = await call_tool(None, CallToolRequestParams(
                name="get_transactions", arguments={"response_mode": "full"},
            ))
            self.assertEqual(full.content[0].text, raw)
            self.assertEqual(full.structured_content, payload)

            compact = await call_tool(None, CallToolRequestParams(
                name="get_transactions", arguments={},
            ))
            self.assertEqual(compact.structured_content["summary"], payload["summary"])
            self.assertEqual(compact.structured_content["transactions"]["count"], 9)
            self.assertTrue(compact.structured_content["_response"]["truncated"])
            self.assertIn("response_mode=full", compact.content[0].text)

    async def test_error_is_not_shortened(self):
        payload = {"status": "error", "code": "AMBIGUOUS_CATEGORY", "error": "Ambiguous",
                   "details": {"candidates": list(range(20))}}
        raw = json.dumps(payload)
        with patch("zenmoney.tools._run_tool_fresh", new=AsyncMock(return_value=raw)):
            result = await call_tool(None, CallToolRequestParams(
                name="get_transactions", arguments={},
            ))
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, raw)

    async def test_call_serialization(self):
        active = 0
        maximum = 0

        async def fake_run(_name, _args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "{}"

        with patch("zenmoney.tools._run_tool_fresh", side_effect=fake_run):
            await asyncio.gather(*[
                call_tool(None, CallToolRequestParams(name="get_accounts", arguments={}))
                for _ in range(3)
            ])
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
