import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cli
from zenmoney import budget_tools, cache, config, dispatch, tools, transport


class StateStoreRegressionTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()

    def test_atomic_json_write_uses_replace_and_preserves_existing_file_on_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text('{"serverTimestamp": 10}', encoding="utf-8")

            with patch.object(config.os, "replace", side_effect=RuntimeError("replace failed")) as mocked_replace:
                with self.assertRaisesRegex(RuntimeError, "replace failed"):
                    config.write_json_state_atomic(path, {"serverTimestamp": 11})

            mocked_replace.assert_called_once()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"serverTimestamp": 10})

    def test_file_lock_rejects_second_writer_while_held(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "state.json.lock"
            with config._FileLock(lock_path):
                with self.assertRaises(TimeoutError):
                    with config._FileLock(lock_path, timeout=0.01, poll_interval=0.001):
                        pass
            self.assertTrue(lock_path.exists())

    def test_corrupt_cache_json_raises_structured_error_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            original = '{"serverTimestamp":'
            cache_path.write_text(original, encoding="utf-8")

            loaded = cache.Cache()
            with patch.object(config, "CACHE_PATH", cache_path):
                with self.assertRaises(config.CorruptStateError) as raised:
                    loaded.load()

            self.assertEqual(raised.exception.to_payload()["code"], "CORRUPT_STATE")
            self.assertEqual(cache_path.read_text(encoding="utf-8"), original)

    def test_non_object_state_json_is_rejected_and_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text("[]", encoding="utf-8")

            with self.assertRaises(config.CorruptStateError) as raised:
                config.read_json_state(config_path)

            self.assertEqual(raised.exception.to_payload()["code"], "CORRUPT_STATE")
            self.assertEqual(config_path.read_text(encoding="utf-8"), "[]")

    def test_budget_analysis_does_not_hide_corrupt_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"budget_mode":', encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path):
                with self.assertRaises(config.CorruptStateError):
                    asyncio.run(budget_tools.tool_analyze_budget_detailed({
                        "start_date": "2026-07-01",
                        "end_date": "2026-07-31",
                    }))

    def test_cache_load_resets_existing_state_when_file_is_missing(self):
        loaded = cache.Cache()
        loaded.server_timestamp = 99
        loaded.data["account"] = {"stale": {"id": "stale"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(config, "CACHE_PATH", Path(temp_dir) / ".cache.json"):
                loaded.load()

        self.assertEqual(loaded.server_timestamp, 0)
        self.assertEqual(loaded.data["account"], {})

    def test_cache_load_replaces_previous_entities_with_file_contents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(
                json.dumps({
                    "serverTimestamp": 12,
                    "account": [{"id": "fresh", "title": "Fresh"}],
                }),
                encoding="utf-8",
            )

            loaded = cache.Cache()
            loaded.data["account"] = {"stale": {"id": "stale"}}
            with patch.object(config, "CACHE_PATH", cache_path):
                loaded.load()

        self.assertEqual(loaded.server_timestamp, 12)
        self.assertEqual(list(loaded.data["account"]), ["fresh"])

    def test_cache_save_refuses_to_overwrite_disk_timestamp_different_from_loaded_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            original_payload = {"serverTimestamp": 10, "account": [{"id": "newer"}]}
            cache_path.write_text(json.dumps(original_payload), encoding="utf-8")

            stale = cache.Cache()
            stale.server_timestamp = 9
            stale.data["account"] = {"stale": {"id": "stale"}}
            with patch.object(config, "CACHE_PATH", cache_path):
                with self.assertRaises(config.LostUpdateError) as raised:
                    stale.save()

            self.assertEqual(raised.exception.to_payload()["code"], "LOST_UPDATE")
            self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), original_payload)

    def test_cache_save_rejects_equal_response_timestamp_collision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(json.dumps({"serverTimestamp": 10}), encoding="utf-8")

            loaded = cache.Cache()
            with patch.object(config, "CACHE_PATH", cache_path):
                loaded.load()
                loaded.apply_diff({"serverTimestamp": 11, "account": [{"id": "local"}]})
                concurrent_payload = {"serverTimestamp": 11, "account": [{"id": "concurrent"}]}
                cache_path.write_text(json.dumps(concurrent_payload), encoding="utf-8")
                with self.assertRaises(config.LostUpdateError) as raised:
                    loaded.save()

            payload = raised.exception.to_payload()
            self.assertEqual(payload["details"]["expected_serverTimestamp"], 10)
            self.assertEqual(payload["details"]["disk_serverTimestamp"], 11)
            self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), concurrent_payload)

    def test_cache_save_rejects_newer_cursor_collision_from_loaded_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(json.dumps({"serverTimestamp": 10}), encoding="utf-8")

            loaded = cache.Cache()
            with patch.object(config, "CACHE_PATH", cache_path):
                loaded.load()
                loaded.apply_diff({"serverTimestamp": 11, "account": [{"id": "local"}]})
                concurrent_payload = {"serverTimestamp": 12, "account": [{"id": "concurrent"}]}
                cache_path.write_text(json.dumps(concurrent_payload), encoding="utf-8")
                with self.assertRaises(config.LostUpdateError) as raised:
                    loaded.save()

            payload = raised.exception.to_payload()
            self.assertEqual(payload["details"]["expected_serverTimestamp"], 10)
            self.assertEqual(payload["details"]["disk_serverTimestamp"], 12)
            self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), concurrent_payload)

    def test_cache_save_updates_loaded_base_after_successful_save(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(json.dumps({"serverTimestamp": 10}), encoding="utf-8")

            loaded = cache.Cache()
            with patch.object(config, "CACHE_PATH", cache_path):
                loaded.load()
                loaded.apply_diff({"serverTimestamp": 11, "account": [{"id": "first"}]})
                loaded.save()
                loaded.apply_diff({"serverTimestamp": 12, "account": [{"id": "second"}]})
                loaded.save()

            saved = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["serverTimestamp"], 12)
            self.assertEqual([item["id"] for item in saved["account"]], ["first", "second"])

    def test_force_fetch_replaces_only_requested_entity_stores_before_deletions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(
                json.dumps({
                    "serverTimestamp": 1,
                    "account": [
                        {"id": "account-delete", "title": "Delete me"},
                        {"id": "account-keep", "title": "Keep me"},
                    ],
                    "transaction": [
                        {"id": "tx-stale"},
                        {"id": "tx-keep", "outcome": 1},
                    ],
                    "budget": [
                        {"tag": "old-tag", "date": "2026-07-01", "outcome": 10},
                    ],
                    "tag": [
                        {"id": "old-tag", "title": "Old"},
                    ],
                }),
                encoding="utf-8",
            )

            cache.CACHE = cache.Cache()
            with patch.object(config, "CACHE_PATH", cache_path):
                cache.CACHE.load()
                self.assertIn("old-tag", cache.CACHE.tags_by_id())
                transport._apply_and_save_diff(
                    {
                        "serverTimestamp": 2,
                        "transaction": [{"id": "tx-keep", "outcome": 2}],
                        "budget": [
                            {"tag": None, "date": "2026-07-01", "outcome": 100},
                            {"tag": "new-tag", "date": "2026-07-01", "outcome": 200},
                        ],
                        "tag": [{"id": "new-tag", "title": "New"}],
                        "deletion": [{"object": "account", "id": "account-delete"}],
                    },
                    force_fetch=["transaction", "budget", "tag"],
                )

            saved = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["serverTimestamp"], 2)
        self.assertEqual(saved["transaction"], [{"id": "tx-keep", "outcome": 2}])
        self.assertEqual(
            sorted(cache.CACHE.data["budget"]),
            ["new-tag:2026-07-01", "null:2026-07-01"],
        )
        self.assertNotIn("old-tag", cache.CACHE.tags_by_id())
        self.assertIn("new-tag", cache.CACHE.tags_by_id())
        self.assertIsNone(cache.CACHE.get_account("account-delete"))
        self.assertIsNotNone(cache.CACHE.get_account("account-keep"))

    def test_cache_apply_diff_rejects_backward_server_timestamp(self):
        loaded = cache.Cache()
        loaded.server_timestamp = 10

        with self.assertRaises(config.LostUpdateError):
            loaded.apply_diff({"serverTimestamp": 9})

        self.assertEqual(loaded.server_timestamp, 10)

    def test_cache_dependent_validation_retries_after_prefetch_sync(self):
        async def fake_sync():
            cache.CACHE.data["tag"] = {
                "food": {"id": "food", "title": "Food", "parent": None},
            }
            cache.CACHE.server_timestamp = 1
            return {}

        async def fake_handler(args):
            return json.dumps({"category_id": args["category_id"]})

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            config_path = Path(temp_dir) / "config.json"
            with patch.object(config, "CACHE_PATH", cache_path), \
                 patch.object(config, "_cfg_path", config_path), \
                 patch.object(dispatch, "_sync", AsyncMock(side_effect=fake_sync)), \
                 patch.object(dispatch, "_close_client", AsyncMock(return_value=None)):
                result = asyncio.run(dispatch.run_tool(
                    "get_reminders",
                    {"category": "Food"},
                    {"get_reminders": fake_handler},
                    lambda: None,
                ))

        self.assertEqual(json.loads(result), {"category_id": "food"})

    def test_analytics_selected_entities_are_revalidated_after_prefetch_sync(self):
        entity_id = "11111111-1111-1111-1111-111111111111"
        cases = {
            "account": {
                "store": "account",
                "entity": {"id": entity_id, "title": "Account", "inBalance": True},
                "arguments": {"account_scope": "selected", "account_ids": [entity_id]},
            },
            "category": {
                "store": "tag",
                "entity": {"id": entity_id, "title": "Category", "parent": None},
                "arguments": {"category_scope": "selected", "category_ids": [entity_id]},
            },
            "merchant": {
                "store": "merchant",
                "entity": {"id": entity_id, "title": "Merchant"},
                "arguments": {"merchant_scope": "selected", "merchant_ids": [entity_id]},
            },
        }

        for entity_type, case in cases.items():
            with self.subTest(entity_type=entity_type), tempfile.TemporaryDirectory() as temp_dir:
                cache_path = Path(temp_dir) / ".cache.json"
                cache_path.write_text(
                    json.dumps({"serverTimestamp": 1, case["store"]: [case["entity"]]}),
                    encoding="utf-8",
                )
                cache.CACHE = cache.Cache()

                async def fake_sync():
                    cache.CACHE.data[case["store"]] = {}
                    return {}

                arguments = {
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-31",
                    "report": "outcome",
                    **case["arguments"],
                }
                with patch.object(config, "CACHE_PATH", cache_path), \
                     patch.object(config, "_cfg_path", Path(temp_dir) / "config.json"), \
                     patch.object(dispatch, "_sync", AsyncMock(side_effect=fake_sync)), \
                     patch.object(dispatch, "_close_client", AsyncMock(return_value=None)):
                    result = asyncio.run(dispatch.run_tool(
                        "get_analytics",
                        arguments,
                        {"get_analytics": AsyncMock(return_value='{"status":"ok"}')},
                        lambda: None,
                    ))

                parsed = json.loads(result)
                self.assertEqual(parsed["code"], "ENTITY_NOT_FOUND")
                self.assertEqual(parsed["details"]["entity_type"], entity_type)

    def test_dispatch_uses_supplied_account_meta_migration_callback(self):
        migrate = Mock()

        async def fake_handler(args):
            return "[]"

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(config, "CACHE_PATH", Path(temp_dir) / ".cache.json"), \
                 patch.object(dispatch, "_sync", AsyncMock(return_value=None)), \
                 patch.object(dispatch, "_close_client", AsyncMock(return_value=None)):
                result = asyncio.run(dispatch.run_tool(
                    "get_accounts",
                    {},
                    {"get_accounts": fake_handler},
                    migrate,
                ))

        self.assertEqual(json.loads(result), [])
        migrate.assert_called_once_with()

    def test_pure_syntax_validation_error_does_not_sync(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(config, "CACHE_PATH", Path(temp_dir) / ".cache.json"), \
                 patch.object(config, "_cfg_path", Path(temp_dir) / "config.json"), \
                 patch.object(dispatch, "_sync", AsyncMock(return_value=None)) as mocked_sync, \
                 patch.object(dispatch, "_close_client", AsyncMock(return_value=None)):
                result = asyncio.run(dispatch.run_tool(
                    "get_accounts",
                    {"include_archived": "false"},
                    {"get_accounts": AsyncMock(return_value="[]")},
                    lambda: None,
                ))

        parsed = json.loads(result)
        self.assertEqual(parsed["code"], "INVALID_BOOL")
        mocked_sync.assert_not_awaited()

    def test_setup_budget_mode_is_cache_only_and_writes_config_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            config_path = Path(temp_dir) / "config.json"
            cache_path.write_text('{"serverTimestamp":', encoding="utf-8")
            config_path.write_text(
                json.dumps({
                    "budget_modes": {
                        "income_vs_expense": {
                            "label": "Income vs Expense",
                            "description": "Mode description",
                        }
                    }
                }),
                encoding="utf-8",
            )

            with patch.object(config, "CACHE_PATH", cache_path), \
                 patch.object(config, "_cfg_path", config_path), \
                 patch.object(dispatch, "_sync", AsyncMock(return_value=None)) as mocked_sync, \
                 patch.object(dispatch, "_close_client", AsyncMock(return_value=None)):
                result = asyncio.run(dispatch.run_tool(
                    "setup_budget_mode",
                    {"mode": "income_vs_expense"},
                    {"setup_budget_mode": budget_tools.tool_setup_budget_mode},
                    lambda: None,
                ))

            parsed = json.loads(result)
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertTrue(parsed["success"])
        self.assertEqual(parsed["label"], "Income vs Expense")
        self.assertEqual(saved["budget_mode"], "income_vs_expense")
        self.assertTrue(saved["budget_mode_configured"])
        mocked_sync.assert_not_awaited()

    def test_account_meta_migration_uses_config_state_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            refs = root / "references"
            refs.mkdir()
            config_path = root / "config.json"
            refs.joinpath("account_meta.json").write_text(
                json.dumps({"acct-1": {"description": "Main"}}),
                encoding="utf-8",
            )
            config_path.write_text(json.dumps({"token": "secret"}), encoding="utf-8")

            with patch.object(config, "ROOT", root), \
                 patch.object(config, "_cfg_path", config_path), \
                 patch.object(config, "write_json_state_atomic", wraps=config.write_json_state_atomic) as write_atomic:
                tools._migrate_account_meta()

            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["token"], "secret")
        self.assertEqual(saved["accounts_meta"]["acct-1"]["description"], "Main")
        write_atomic.assert_called_once()

    def test_account_meta_migration_fails_closed_on_corrupt_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            refs = root / "references"
            refs.mkdir()
            config_path = root / "config.json"
            original = '{"token":'
            config_path.write_text(original, encoding="utf-8")
            refs.joinpath("account_meta.json").write_text(
                json.dumps({"acct-1": {"description": "Main"}}),
                encoding="utf-8",
            )

            with patch.object(config, "ROOT", root), \
                 patch.object(config, "_cfg_path", config_path):
                with self.assertRaises(config.CorruptStateError):
                    tools._migrate_account_meta()

            self.assertEqual(config_path.read_text(encoding="utf-8"), original)

    def test_cli_allows_cache_only_setup_budget_mode_without_token(self):
        with patch.object(cli.config, "TOKEN", ""), \
             patch.object(cli.config, "CONFIG_LOAD_ERROR", None), \
             patch.object(cli, "_run_tool", AsyncMock(return_value='{"success": true}')), \
             patch.object(sys, "argv", [
                 "cli.py",
                 "--call",
                 '{"tool":"setup_budget_mode","arguments":{"mode":"income_vs_expense"}}',
             ]), \
             patch("builtins.print") as mocked_print:
            cli.main()

        mocked_print.assert_called_once_with('{"success": true}')

    def test_cli_reads_call_payload_from_stdin(self):
        payload = '{"tool":"get_accounts","arguments":{}}'
        with patch.object(cli.config, "TOKEN", "token"), \
             patch.object(cli, "_run_tool", AsyncMock(return_value="[]")), \
             patch.object(sys, "argv", ["cli.py", "--call", "-"]), \
             patch.object(sys, "stdin", io.StringIO(payload)), \
             patch("builtins.print") as mocked_print:
            cli.main()

        mocked_print.assert_called_once_with("[]")

    def test_cli_reads_cyrillic_call_payload_from_stdin(self):
        payload = '{"tool":"suggest","arguments":{"payee":"Тестовый магазин"}}'
        with patch.object(cli.config, "TOKEN", "token"), \
             patch.object(cli, "_run_tool", AsyncMock(return_value='{"status":"ok"}')) as run_tool, \
             patch.object(sys, "argv", ["cli.py", "--call", "-"]), \
             patch.object(sys, "stdin", io.StringIO(payload)), \
             patch("builtins.print"):
            cli.main()

        run_tool.assert_awaited_once_with("suggest", {"payee": "Тестовый магазин"})

    def test_cli_decodes_ascii_escaped_unicode_from_stdin(self):
        payload = r'{"tool":"suggest","arguments":{"payee":"\u0422\u0435\u0441\u0442\u043e\u0432\u044b\u0439 \u043c\u0430\u0433\u0430\u0437\u0438\u043d"}}'
        with patch.object(cli.config, "TOKEN", "token"), \
             patch.object(cli, "_run_tool", AsyncMock(return_value='{"status":"ok"}')) as run_tool, \
             patch.object(sys, "argv", ["cli.py", "--call", "-"]), \
             patch.object(sys, "stdin", io.StringIO(payload)), \
             patch("builtins.print"):
            cli.main()

        run_tool.assert_awaited_once_with("suggest", {"payee": "Тестовый магазин"})

    def test_cli_reports_corrupt_config_instead_of_missing_token(self):
        corrupt = config.CorruptStateError(Path("config.json"), "bad JSON")
        with patch.object(cli.config, "TOKEN", ""), \
             patch.object(cli.config, "CONFIG_LOAD_ERROR", corrupt), \
             patch.object(sys, "argv", [
                 "cli.py",
                 "--call",
                 '{"tool":"get_accounts","arguments":{}}',
             ]), \
             patch("builtins.print") as mocked_print:
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 1)
        printed = json.loads(mocked_print.call_args.args[0])
        self.assertEqual(printed["code"], "CORRUPT_STATE")
        self.assertIs(mocked_print.call_args.kwargs["file"], sys.stderr)

    def test_cli_writes_structured_tool_error_to_stderr_and_exits_nonzero(self):
        result = '{"status":"error","error":"bad auth"}'
        with patch.object(cli.config, "TOKEN", "token"), \
             patch.object(cli, "_run_tool", AsyncMock(return_value=result)), \
             patch.object(sys, "argv", [
                 "cli.py",
                 "--call",
                 '{"tool":"get_accounts","arguments":{}}',
             ]), \
             patch("builtins.print") as mocked_print:
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 1)
        mocked_print.assert_called_once_with(result, file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
