import asyncio
import json
import os
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import cache, config, tools, transport


class FreshRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.original = cache.CACHE
        cache.CACHE = cache.Cache()
        self.addCleanup(setattr, cache, "CACHE", self.original)
        for method in ("read_json_state", "write_json_state_atomic"):
            guard = patch.object(config, method, side_effect=AssertionError("state disk access"))
            guard.start()
            self.addCleanup(guard.stop)

    def run_tool(self, name, args=None):
        return json.loads(asyncio.run(tools._run_tool_fresh(name, args or {})))

    def test_each_call_fetches_full_snapshot_without_reusing_entities(self):
        snapshots = []

        async def handler(args):
            snapshots.append(cache.CACHE)
            return json.dumps(cache.CACHE.merchants())

        with patch.dict(tools.TOOLS, {"get_merchants": handler}), patch.object(
            transport, "_api_post", AsyncMock(side_effect=[
                {"serverTimestamp": 20, "merchant": [{"id": "first", "title": "First"}]},
                {"serverTimestamp": 10, "merchant": [{"id": "second", "title": "Second"}]},
            ])
        ) as api:
            self.assertEqual(self.run_tool("get_merchants")[0]["id"], "first")
            self.assertEqual(self.run_tool("get_merchants")[0]["id"], "second")
        self.assertEqual([call.args[1]["serverTimestamp"] for call in api.await_args_list], [0, 0])
        self.assertIsNot(snapshots[0], snapshots[1])
        self.assertTrue(all(snapshot.merchants() == [] for snapshot in snapshots))
        self.assertTrue(all(snapshot.server_timestamp == 0 for snapshot in snapshots))

    def test_actual_read_handler_uses_full_snapshot(self):
        with patch.object(transport, "_api_post", AsyncMock(return_value={
            "serverTimestamp": 1, "merchant": [{"id": "merchant-1", "title": "Shop"}],
        })) as api:
            result = self.run_tool("get_merchants")
        self.assertEqual(result["merchants"], [{"id": "merchant-1", "title": "Shop"}])
        self.assertEqual(api.await_args.args[1]["serverTimestamp"], 0)

    def test_write_requires_fresh_server_confirmation_without_disk_access(self):
        tx = {"id": "test-tx", "outcome": 100}

        async def handler(args):
            await transport._write_diff({"transaction": [tx]})
            return json.dumps({"status": "success"})

        with patch.dict(tools.TOOLS, {"get_merchants": handler}), patch.object(
            transport, "_api_post", AsyncMock(side_effect=[
                {"serverTimestamp": 1},
                {"serverTimestamp": 2, "transaction": [tx]},
                {"serverTimestamp": 3, "transaction": [tx]},
            ])
        ) as api:
            self.assertEqual(self.run_tool("get_merchants")["status"], "success")
        self.assertEqual(api.await_count, 3)
        self.assertEqual(api.await_args.args[1]["serverTimestamp"], 0)
        self.assertEqual(api.await_args.args[1]["forceFetch"], ["transaction"])

    def test_unconfirmed_write_is_an_error(self):
        async def handler(args):
            await transport._write_diff({"transaction": [{"id": "test-tx", "outcome": 100}]})
            return json.dumps({"status": "success"})

        with patch.dict(tools.TOOLS, {"get_merchants": handler}), patch.object(
            transport, "_api_post", AsyncMock(side_effect=[
                {"serverTimestamp": 1}, {"serverTimestamp": 2},
                {"serverTimestamp": 3, "transaction": []},
            ])
        ):
            result = self.run_tool("get_merchants")
        self.assertEqual(result["status"], "error")
        self.assertIn("not confirmed", result["error"])

    def test_snapshot_is_cleared_on_cancellation(self):
        snapshots = []

        async def handler(args):
            snapshots.append(cache.CACHE)
            raise asyncio.CancelledError()

        previous = cache.CACHE
        with patch.dict(tools.TOOLS, {"get_merchants": handler}), patch.object(
            transport, "_api_post", AsyncMock(return_value={
                "serverTimestamp": 1, "merchant": [{"id": "test"}],
            })
        ):
            with self.assertRaises(asyncio.CancelledError):
                self.run_tool("get_merchants")
        self.assertEqual(snapshots[0].merchants(), [])
        self.assertIs(cache.CACHE, previous)

    def test_invalid_syntax_keeps_original_code_without_api_calls(self):
        with patch.object(transport, "_api_post", AsyncMock()) as api:
            result = self.run_tool("get_accounts", {"include_archived": "false"})
        self.assertEqual(result["code"], "INVALID_BOOL")
        api.assert_not_awaited()

    def test_unknown_tool_does_not_fetch(self):
        with patch.object(transport, "_api_post", AsyncMock()) as api:
            self.assertEqual(self.run_tool("unknown")["code"], "UNKNOWN_TOOL")
        api.assert_not_awaited()

    def test_repeated_full_sync_replaces_entities_and_never_uses_cursor(self):
        async def sync_twice():
            await transport._sync()
            await transport._sync()

        with patch.object(transport, "_api_post", AsyncMock(side_effect=[
            {"serverTimestamp": 1, "merchant": [{"id": "removed", "title": "Old"}]},
            {"serverTimestamp": 2, "merchant": []},
        ])) as api:
            asyncio.run(sync_twice())
        self.assertEqual(cache.CACHE.merchants(), [])
        self.assertEqual([call.args[1]["serverTimestamp"] for call in api.await_args_list], [0, 0])

    def test_compatibility_entrypoint_uses_fresh_snapshot(self):
        cache.CACHE.apply_diff({"serverTimestamp": 99, "merchant": [{"id": "stale"}]})
        with patch.object(transport, "_api_post", AsyncMock(return_value={
            "serverTimestamp": 1, "merchant": [],
        })) as api:
            result = json.loads(asyncio.run(tools._run_tool("get_merchants", {})))
        self.assertEqual(result["merchants"], [])
        self.assertEqual(api.await_args.args[1]["serverTimestamp"], 0)

    def test_external_state_directory_is_used_without_loading_credentials(self):
        state_dir = ROOT / "test-external-state"
        with patch.dict(os.environ, {
            "ZENMONEY_STATE_DIR": str(state_dir), "ZENMONEY_TOKEN": "test-token",
        }):
            loaded = runpy.run_path(str(ROOT / "scripts" / "zenmoney" / "config.py"))
        self.assertEqual(loaded["_cfg_path"], state_dir / "config.json")
        self.assertNotIn("CACHE_PATH", loaded)


if __name__ == "__main__":
    unittest.main()
