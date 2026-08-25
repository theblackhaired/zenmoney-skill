import asyncio
import datetime
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, config, dispatch, domain, read_tools, reminder_tools, tools, validation, write_tools


class GenerateMarkerDatesTests(unittest.TestCase):
    def test_monthly_step_one_uses_start_date_day_with_zero_offset(self):
        dates = domain._generate_marker_dates(
            start_date="2099-01-31",
            interval="month",
            step=1,
            points=[0],
            end_date=None,
            count=3,
        )

        self.assertEqual(
            dates,
            ["2099-01-31", "2099-02-28", "2099-03-31"],
        )


class FindCategoryIdTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["tag"] = {
            "parent-food": {"id": "parent-food", "title": "Еда", "parent": None},
            "food-home": {"id": "food-home", "title": "Питание", "parent": "parent-food"},
            "parent-other": {"id": "parent-other", "title": "Прочее", "parent": None},
            "food-travel": {"id": "food-travel", "title": "Питание", "parent": "parent-other"},
        }

    def test_duplicate_title_requires_full_path(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            domain._find_category_id("Питание")

    def test_full_path_resolves_duplicate_title(self):
        self.assertEqual(
            domain._find_category_id("Еда / Питание"),
            "food-home",
        )

    def test_real_all_category_title_does_not_resolve_to_sentinel(self):
        cache.CACHE.data["tag"]["category-all"] = {
            "id": "category-all",
            "title": "all",
            "parent": None,
        }
        self.assertEqual(domain._find_category_id("all"), "category-all")

    def test_budget_all_alias_still_resolves_to_aggregate_sentinel(self):
        self.assertEqual(
            domain._find_category_id("ALL", allow_all=True),
            domain.ALL_CATEGORIES_ID,
        )
        self.assertEqual(
            domain._find_category_id("ALL (aggregate)", allow_all=True),
            domain.ALL_CATEGORIES_ID,
        )

    def test_category_full_path_handles_parent_cycle(self):
        cache.CACHE.data["tag"] = {
            "loop": {"id": "loop", "title": "Loop", "parent": "loop"},
        }
        self.assertEqual(domain._category_full_path("loop"), "Loop")


class CacheTagIndexTests(unittest.TestCase):
    def test_tags_by_id_reuses_cache_until_tag_diff(self):
        tags_cache = cache.Cache()
        tags_cache.data["tag"] = {
            "parent-food": {"id": "parent-food", "title": "Food", "parent": None},
            "food-home": {"id": "food-home", "title": "Home", "parent": "parent-food"},
        }

        first = tags_cache.tags_by_id()
        second = tags_cache.tags_by_id()
        self.assertIs(first, second)

        tags_cache.apply_diff({"transaction": [{"id": "tx-1"}]})
        third = tags_cache.tags_by_id()
        self.assertIs(first, third)

        tags_cache.apply_diff({
            "tag": [
                {"id": "food-home", "title": "Dining", "parent": "parent-food"},
                {"id": "food-out", "title": "Restaurants", "parent": "parent-food"},
            ]
        })
        fourth = tags_cache.tags_by_id()
        self.assertIsNot(first, fourth)
        self.assertEqual(fourth["food-home"]["title"], "Dining")
        self.assertIn("food-out", fourth)


class InitialBalanceCalculationTests(unittest.TestCase):
    def test_initial_balance_accepts_transactions_stored_as_dict_by_id(self):
        data = {
            "account": [
                {
                    "id": "acct-1",
                    "balance": 1000,
                    "instrument": "RUB",
                    "inBalance": True,
                    "archive": False,
                }
            ],
            "instrument": [],
            "transaction": {
                "tx-income-after-start": {
                    "id": "tx-income-after-start",
                    "date": "2026-04-10",
                    "incomeAccount": "acct-1",
                    "income": 200,
                    "outcomeAccount": "other",
                    "outcome": 0,
                },
                "tx-outcome-after-start": {
                    "id": "tx-outcome-after-start",
                    "date": "2026-04-12",
                    "incomeAccount": "other",
                    "income": 0,
                    "outcomeAccount": "acct-1",
                    "outcome": 50,
                },
                "tx-before-start": {
                    "id": "tx-before-start",
                    "date": "2026-03-30",
                    "incomeAccount": "acct-1",
                    "income": 999,
                    "outcomeAccount": "other",
                    "outcome": 0,
                },
            },
        }

        self.assertEqual(
            domain._calculate_initial_balance_impl(data, "2026-04-01"),
            850,
        )


class ToolErrorTests(unittest.TestCase):
    def test_tool_error_str_returns_message(self):
        exc = validation.ToolError("CODE", "boom")
        self.assertEqual(str(exc), "boom")


class TransactionValidationTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        self.account_id = "22222222-2222-2222-2222-222222222222"
        cache.CACHE.data["transaction"] = {
            "11111111-1111-1111-1111-111111111111": {
                "id": "11111111-1111-1111-1111-111111111111",
                "outcome": 1500,
                "income": 0,
                "outcomeAccount": self.account_id,
                "incomeAccount": self.account_id,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "date": "2026-04-20",
            }
        }
        cache.CACHE.data["account"] = {
            self.account_id: {
                "id": self.account_id,
                "user": 1,
                "instrument": 1,
                "title": "Main",
                "type": "checking",
                "balance": 1000,
                "archive": False,
            }
        }
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "symbol": "₽", "title": "Ruble"}
        }
        cache.CACHE.data["user"] = {"1": {"id": 1}}

    def test_create_transaction_rejects_zero_or_negative_amount(self):
        for amount in (0, -1):
            with self.assertRaisesRegex(ValueError, "positive"):
                asyncio.run(
                    tools.tool_create_transaction(
                        {
                            "type": "expense",
                            "amount": amount,
                            "account_id": self.account_id,
                        }
                    )
                )

    def test_update_transaction_rejects_zero_or_negative_amount(self):
        for amount in (0, -1):
            with self.assertRaisesRegex(ValueError, "positive"):
                asyncio.run(
                    tools.tool_update_transaction(
                        {
                            "id": "11111111-1111-1111-1111-111111111111",
                            "amount": amount,
                        }
                    )
                )

    def test_create_transaction_returns_created_transaction(self):
        async def fake_write_diff(diff):
            for tx in diff.get("transaction", []):
                cache.CACHE.data["transaction"][tx["id"]] = tx

        with patch.object(write_tools, "_write_diff", side_effect=fake_write_diff):
            result = json.loads(asyncio.run(tools.tool_create_transaction({
                "type": "expense",
                "amount": 100,
                "account_id": self.account_id,
                "date": "2026-04-26",
            })))

        self.assertIn("created", result)
        self.assertEqual(result["created"]["amount"], 100)
        self.assertEqual(result["created"]["account"], "Main")

    def test_update_transaction_returns_updated_transaction(self):
        async def fake_write_diff(diff):
            for tx in diff.get("transaction", []):
                cache.CACHE.data["transaction"][tx["id"]] = tx

        with patch.object(write_tools, "_write_diff", side_effect=fake_write_diff):
            result = json.loads(asyncio.run(tools.tool_update_transaction({
                "id": "11111111-1111-1111-1111-111111111111",
                "amount": 200,
                "comment": "updated",
            })))

        self.assertIn("updated", result)
        self.assertEqual(result["updated"]["amount"], 200)
        self.assertEqual(result["updated"]["comment"], "updated")


class BooleanValidationTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()

    def test_bool_helper_accepts_real_bool_only(self):
        self.assertTrue(domain._get_bool_arg({"flag": True}, "flag", False))
        self.assertFalse(domain._get_bool_arg({"flag": False}, "flag", True))
        with self.assertRaisesRegex(ValueError, "boolean"):
            domain._get_bool_arg({"flag": "false"}, "flag", True)
        with self.assertRaisesRegex(ValueError, "boolean"):
            domain._get_bool_arg({"flag": 0}, "flag", True)

    def test_get_accounts_rejects_string_boolean(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            asyncio.run(tools.tool_get_accounts({"include_archived": "false"}))

    def test_get_reminders_rejects_string_boolean(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            asyncio.run(tools.tool_get_reminders({"include_processed": "false"}))

    def test_create_reminder_rejects_string_boolean(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            asyncio.run(tools.tool_create_reminder({
                "type": "expense",
                "amount": 100,
                "account_id": "22222222-2222-2222-2222-222222222222",
                "interval": "month",
                "notify": "false",
            }))

    def test_create_budget_rejects_string_boolean(self):
        cache.CACHE.data["tag"] = {
            "tag-1": {"id": "tag-1", "title": "Food", "parent": None},
        }
        cache.CACHE.data["user"] = {"1": {"id": 1}}

        with patch.object(budget_tools, "_write_diff", AsyncMock(return_value={})):
            with self.assertRaisesRegex(ValueError, "boolean"):
                asyncio.run(tools.tool_create_budget({
                    "month": "2026-04",
                    "category": "Food",
                    "outcome": 100,
                    "income_lock": "false",
                }))

    def test_marker_range_helper_rejects_empty_and_partial_values(self):
        with self.assertRaisesRegex(ValueError, "marker_from must not be empty"):
            validation.get_marker_range({"marker_from": "", "marker_to": "2026-04-30"})
        with self.assertRaisesRegex(ValueError, "marker_from and marker_to"):
            validation.get_marker_range({"marker_from": "2026-04-01"})

    def test_optional_date_helper_rejects_empty_string(self):
        with self.assertRaisesRegex(ValueError, "end_date must not be empty"):
            validation.get_optional_date_arg({"end_date": ""}, "end_date")

    def test_date_or_today_helper_rejects_empty_string(self):
        with self.assertRaisesRegex(ValueError, "date must not be empty"):
            validation.get_date_arg_or_today({"date": ""}, "date")

    def test_create_budget_all_alias_uses_aggregate_tag(self):
        cache.CACHE.data["account"] = {
            "acc-1": {"id": "acc-1", "user": 123, "instrument": 1, "title": "Card"},
        }
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(budget_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(tools.tool_create_budget({
                "month": "2026-04",
                "category": "ALL",
                "outcome": 100,
            }))

        self.assertEqual(captured["diff"]["budget"][0]["tag"], domain.ALL_CATEGORIES_ID)

    def test_run_tool_returns_invalid_bool_error_code(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("get_accounts", {"include_archived": "false"}))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_BOOL")

    def test_user_supplied_validated_tool_flag_does_not_bypass_validation(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("get_accounts", {
                "__validated_for_tool__": "internal:get_accounts",
                "include_archived": "false",
            }))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_BOOL")

    def test_validated_args_are_json_serializable(self):
        args = validation.validate_tool_args("get_accounts", {})
        json.dumps(args)

    def test_create_reminder_rejects_empty_end_date(self):
        with self.assertRaisesRegex(ValueError, "end_date must not be empty"):
            asyncio.run(tools.tool_create_reminder({
                "type": "expense",
                "amount": 100,
                "account_id": "22222222-2222-2222-2222-222222222222",
                "interval": "month",
                "end_date": "",
            }))


class DispatchValidationErrorTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()

    def test_run_tool_returns_invalid_date_range_error_code(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("get_reminders", {"marker_from": "2026-04-01"}))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_DATE_RANGE")

    def test_run_tool_returns_unsupported_category_filter_code(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("get_reminders", {"category": "ALL"}))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "UNSUPPORTED_CATEGORY_FILTER")

    def test_run_tool_returns_invalid_argument_for_missing_required_field(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("get_transactions", {}))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_ARGUMENT")

    def test_run_tool_returns_invalid_date_for_empty_optional_date(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("get_transactions", {
                "start_date": "2026-04-01",
                "end_date": "",
            }))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_DATE")

    def test_run_tool_returns_invalid_argument_for_missing_required_amount(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("create_transaction", {
                "type": "expense",
                "account_id": "22222222-2222-2222-2222-222222222222",
            }))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_ARGUMENT")

    def test_run_tool_rejects_direct_category_id_for_get_reminders(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("get_reminders", {
                "category_id": "garbage",
            }))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_ARGUMENT")

    def test_run_tool_rejects_non_integer_currency_id_for_create_account(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("create_account", {
                "title": "A",
                "type": "checking",
                "currency_id": "abc",
            }))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_ARGUMENT")
        self.assertIn("currency_id must be an integer", parsed["error"])

    def test_run_tool_rejects_unknown_budget_mode(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("analyze_budget_detailed", {
                "budget_mode": "garbage",
            }))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INVALID_ARGUMENT")


class PeriodShorthandValidationTests(unittest.TestCase):
    def test_get_transactions_resolves_relative_start_date(self):
        with patch.object(validation, "_today", return_value="2026-04-26"):
            normalized = validation.validate_tool_args("get_transactions", {"start_date": "-30d"})

        self.assertEqual(normalized["start_date"], "2026-03-27")

    def test_get_analytics_this_month_expands_to_full_month(self):
        with patch.object(validation, "_today", return_value="2026-04-26"):
            normalized = validation.validate_tool_args("get_analytics", {
                "start_date": "this_month",
                "report": "outcome",
            })

        self.assertEqual(normalized["start_date"], "2026-04-01")
        self.assertEqual(normalized["end_date"], "2026-04-30")

    def test_analyze_budget_billing_period_expands_from_config(self):
        with patch.object(validation, "_today", return_value="2026-04-26"), \
             patch.object(config, "_load_config", return_value={"billing_period_start_day": 20}):
            normalized = validation.validate_tool_args(
                "analyze_budget_detailed",
                {"start_date": "billing_period"},
            )

        self.assertEqual(normalized["start_date"], "2026-04-20")
        self.assertEqual(normalized["end_date"], "2026-05-19")


class UpdateReminderRecurrenceTests(unittest.TestCase):
    REMINDER_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"
    ACCOUNT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01"
    USER_ID = 12345
    PAST_MARKER_ID = "cccccccc-cccc-cccc-cccc-cccccccccc01"

    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble", "symbol": "₽"},
        }
        cache.CACHE.data["user"] = {str(self.USER_ID): {"id": self.USER_ID, "login": "u"}}
        cache.CACHE.data["account"] = {
            self.ACCOUNT_ID: {
                "id": self.ACCOUNT_ID,
                "user": self.USER_ID,
                "instrument": 1,
                "title": "Card",
                "type": "ccard",
                "balance": 1000,
            },
        }

        today = datetime.date.today()
        # Use future startDate so generator doesn't clamp
        start = (today + datetime.timedelta(days=1)).isoformat()

        cache.CACHE.data["reminder"] = {
            self.REMINDER_ID: {
                "id": self.REMINDER_ID,
                "user": self.USER_ID,
                "changed": 1,
                "incomeInstrument": 1,
                "incomeAccount": self.ACCOUNT_ID,
                "income": 0,
                "outcomeInstrument": 1,
                "outcomeAccount": self.ACCOUNT_ID,
                "outcome": 500,
                "tag": None,
                "merchant": None,
                "payee": "Test Payee",
                "comment": None,
                "interval": "month",
                "step": 1,
                "points": [0],
                "startDate": start,
                "endDate": None,
                "notify": True,
            }
        }

        # 3 future planned markers + 1 past processed marker
        future_dates = [
            (today + datetime.timedelta(days=10)).isoformat(),
            (today + datetime.timedelta(days=40)).isoformat(),
            (today + datetime.timedelta(days=70)).isoformat(),
        ]
        markers = {}
        for i, d in enumerate(future_dates):
            mid = f"dddddddd-dddd-dddd-dddd-dddddddddd{i:02d}"
            markers[mid] = {
                "id": mid,
                "user": self.USER_ID,
                "changed": 1,
                "incomeInstrument": 1,
                "incomeAccount": self.ACCOUNT_ID,
                "income": 0,
                "outcomeInstrument": 1,
                "outcomeAccount": self.ACCOUNT_ID,
                "outcome": 500,
                "tag": None,
                "merchant": None,
                "payee": "Test Payee",
                "comment": None,
                "date": d,
                "reminder": self.REMINDER_ID,
                "state": "planned",
                "notify": True,
            }
        markers[self.PAST_MARKER_ID] = {
            "id": self.PAST_MARKER_ID,
            "user": self.USER_ID,
            "changed": 1,
            "incomeInstrument": 1,
            "incomeAccount": self.ACCOUNT_ID,
            "income": 0,
            "outcomeInstrument": 1,
            "outcomeAccount": self.ACCOUNT_ID,
            "outcome": 500,
            "tag": None,
            "merchant": None,
            "payee": "Test Payee",
            "comment": None,
            "date": (today - datetime.timedelta(days=30)).isoformat(),
            "reminder": self.REMINDER_ID,
            "state": "processed",
            "notify": True,
        }
        cache.CACHE.data["reminderMarker"] = markers

        self.future_marker_ids = [
            f"dddddddd-dddd-dddd-dddd-dddddddddd{i:02d}" for i in range(3)
        ]

    def test_recurrence_change_regenerates_planned_markers(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(tools.tool_update_reminder({
                "id": self.REMINDER_ID,
                "points": [0],
                "regenerate_markers": 3,
            }))

        diff = captured["diff"]
        self.assertIn("reminder", diff)
        self.assertEqual(len(diff["reminder"]), 1)
        self.assertEqual(diff["reminder"][0]["points"], [0])

        self.assertIn("deletion", diff)
        deletion_ids = {d["id"] for d in diff["deletion"]}
        self.assertEqual(deletion_ids, set(self.future_marker_ids))
        for d in diff["deletion"]:
            self.assertEqual(d["object"], "reminderMarker")
            self.assertIn("stamp", d)
            self.assertIn("user", d)

        # Past processed marker must NOT be deleted
        self.assertNotIn(self.PAST_MARKER_ID, deletion_ids)

        self.assertIn("reminderMarker", diff)
        new_markers = diff["reminderMarker"]
        self.assertEqual(len(new_markers), 3)
        for m in new_markers:
            self.assertEqual(m["state"], "planned")
            self.assertEqual(m["reminder"], self.REMINDER_ID)
            self.assertEqual(m["date"][-2:], cache.CACHE.data["reminder"][self.REMINDER_ID]["startDate"][-2:])
            self.assertEqual(m["outcome"], 500)
            self.assertNotIn(m["id"], self.future_marker_ids)

    def test_recurrence_change_preserves_existing_horizon_by_default(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        # Extend future planned markers beyond the old fixed default of 12.
        today = datetime.date.today()
        for i in range(3, 14):
            mid = f"dddddddd-dddd-dddd-dddd-dddddddddd{i:02d}"
            cache.CACHE.data["reminderMarker"][mid] = {
                "id": mid,
                "user": self.USER_ID,
                "changed": 1,
                "incomeInstrument": 1,
                "incomeAccount": self.ACCOUNT_ID,
                "income": 0,
                "outcomeInstrument": 1,
                "outcomeAccount": self.ACCOUNT_ID,
                "outcome": 500,
                "tag": None,
                "merchant": None,
                "payee": "Test Payee",
                "comment": None,
                "date": (today + datetime.timedelta(days=10 + i * 10)).isoformat(),
                "reminder": self.REMINDER_ID,
                "state": "planned",
                "notify": True,
            }

        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(tools.tool_update_reminder({
                "id": self.REMINDER_ID,
                "points": [0],
            }))

        diff = captured["diff"]
        self.assertEqual(len(diff["deletion"]), 14)
        self.assertEqual(len(diff["reminderMarker"]), 14)

    def test_end_date_before_start_date_raises(self):
        mock_write = AsyncMock(return_value={})
        with patch.object(reminder_tools, "_write_diff", mock_write):
            with self.assertRaisesRegex(ValueError, "end_date"):
                asyncio.run(tools.tool_update_reminder({
                    "id": self.REMINDER_ID,
                    "end_date": "2000-01-01",
                }))
        mock_write.assert_not_awaited()

    def test_amount_only_change_ignores_invalid_cached_date_order(self):
        cache.CACHE.data["reminder"][self.REMINDER_ID]["startDate"] = "2099-12-31"
        cache.CACHE.data["reminder"][self.REMINDER_ID]["endDate"] = "2099-01-01"
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        result = None
        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff):
            result = json.loads(asyncio.run(tools.tool_update_reminder({
                "id": self.REMINDER_ID,
                "amount": 999,
            })))

        self.assertTrue(result["success"])
        self.assertEqual(result["markers_updated"], 3)
        diff = captured["diff"]
        self.assertIn("reminder", diff)
        self.assertNotIn("deletion", diff)
        self.assertIn("reminderMarker", diff)
        updated_ids = {m["id"] for m in diff["reminderMarker"]}
        self.assertEqual(updated_ids, set(self.future_marker_ids))
        for m in diff["reminderMarker"]:
            self.assertEqual(m["outcome"], 999)

    def test_amount_only_change_uses_in_place_sync(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(tools.tool_update_reminder({
                "id": self.REMINDER_ID,
                "amount": 999,
            }))

        diff = captured["diff"]
        self.assertIn("reminder", diff)
        self.assertNotIn("deletion", diff)
        self.assertIn("reminderMarker", diff)
        # Should be 3 in-place updates with same IDs
        updated_ids = {m["id"] for m in diff["reminderMarker"]}
        self.assertEqual(updated_ids, set(self.future_marker_ids))
        for m in diff["reminderMarker"]:
            self.assertEqual(m["outcome"], 999)

    def test_amount_and_recurrence_change_regenerates_without_in_place_overwrite(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(tools.tool_update_reminder({
                "id": self.REMINDER_ID,
                "amount": 777,
                "points": [0],
                "regenerate_markers": 2,
            }))

        diff = captured["diff"]
        self.assertIn("deletion", diff)
        self.assertEqual({d["id"] for d in diff["deletion"]}, set(self.future_marker_ids))
        self.assertIn("reminderMarker", diff)
        self.assertEqual(len(diff["reminderMarker"]), 2)
        regenerated_ids = {m["id"] for m in diff["reminderMarker"]}
        self.assertTrue(regenerated_ids.isdisjoint(set(self.future_marker_ids)))
        for marker in diff["reminderMarker"]:
            self.assertEqual(marker["state"], "planned")
            self.assertEqual(marker["outcome"], 777)
            self.assertEqual(marker["date"][-2:], cache.CACHE.data["reminder"][self.REMINDER_ID]["startDate"][-2:])


class CreateReminderMarkerTests(unittest.TestCase):
    ACCOUNT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01"
    TAG_ID = "cccccccc-cccc-cccc-cccc-cccccccccc01"
    USER_ID = 12345

    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble", "symbol": "₽"},
        }
        cache.CACHE.data["user"] = {str(self.USER_ID): {"id": self.USER_ID, "login": "u"}}
        cache.CACHE.data["account"] = {
            self.ACCOUNT_ID: {
                "id": self.ACCOUNT_ID,
                "user": self.USER_ID,
                "instrument": 1,
                "title": "Card",
                "type": "ccard",
                "balance": 1000,
            },
        }
        cache.CACHE.data["tag"] = {
            self.TAG_ID: {"id": self.TAG_ID, "title": "Зарплата", "parent": None},
        }

    def test_auto_created_one_time_marker_is_written_with_reminder_atomically(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff), \
             patch.object(reminder_tools, "_new_uuid", side_effect=[
                 "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01",
                 "dddddddd-dddd-dddd-dddd-dddddddddd01",
             ]), \
             patch.object(reminder_tools, "_now_ts", return_value=123):
            result = json.loads(asyncio.run(tools.tool_create_reminder_marker({
                "type": "income",
                "amount": 1000,
                "account_id": self.ACCOUNT_ID,
                "category_ids": [self.TAG_ID],
                "date": "2026-07-01",
                "payee": "ISS",
                "comment": "Отпускные",
                "notify": False,
            })))

        self.assertTrue(result["success"])
        self.assertTrue(result["reminder_marker"]["auto_created_reminder"])

        diff = captured["diff"]
        self.assertIn("reminder", diff)
        self.assertIn("reminderMarker", diff)
        self.assertEqual(len(diff["reminder"]), 1)
        self.assertEqual(len(diff["reminderMarker"]), 1)

        reminder = diff["reminder"][0]
        marker = diff["reminderMarker"][0]
        self.assertEqual(reminder["id"], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01")
        self.assertEqual(marker["id"], "dddddddd-dddd-dddd-dddd-dddddddddd01")
        self.assertEqual(marker["reminder"], reminder["id"])
        self.assertEqual(reminder["step"], 0)
        self.assertEqual(reminder["points"], [0])
        self.assertIsNone(reminder["interval"])
        self.assertIsNone(reminder["endDate"])
        self.assertFalse(marker["isForecast"])
        self.assertEqual(marker["date"], "2026-07-01")
        self.assertEqual(marker["income"], 1000)
        self.assertEqual(marker["tag"], [self.TAG_ID])


class GetAnalyticsCurrencySplitTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble", "symbol": "₽"},
            "2": {"id": 2, "shortTitle": "KZT", "title": "Tenge", "symbol": "₸"},
        }
        cache.CACHE.data["account"] = {
            "acct-rub": {
                "id": "acct-rub", "user": 1, "instrument": 1,
                "title": "Card RUB", "type": "ccard", "balance": 0,
            },
            "acct-kzt": {
                "id": "acct-kzt", "user": 1, "instrument": 2,
                "title": "Card KZT", "type": "ccard", "balance": 0,
            },
        }
        cache.CACHE.data["tag"] = {
            "tag-foreign": {
                "id": "tag-foreign", "title": "Иностранные сервисы", "parent": None,
            },
        }
        cache.CACHE.data["transaction"] = {
            "tx1": {
                "id": "tx1", "date": "2026-04-10",
                "income": 0, "outcome": 100,
                "incomeInstrument": 1, "outcomeInstrument": 1,
                "incomeAccount": "acct-rub", "outcomeAccount": "acct-rub",
                "tag": ["tag-foreign"],
            },
            "tx2": {
                "id": "tx2", "date": "2026-04-15",
                "income": 0, "outcome": 200,
                "incomeInstrument": 1, "outcomeInstrument": 1,
                "incomeAccount": "acct-rub", "outcomeAccount": "acct-rub",
                "tag": ["tag-foreign"],
            },
            "tx3": {
                "id": "tx3", "date": "2026-04-20",
                "income": 0, "outcome": 50,
                "incomeInstrument": 2, "outcomeInstrument": 2,
                "incomeAccount": "acct-kzt", "outcomeAccount": "acct-kzt",
                "tag": ["tag-foreign"],
            },
        }

    def test_groups_split_by_currency(self):
        result = json.loads(asyncio.run(tools.tool_get_analytics({
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "report": "outcome",
            "group_by": "category",
        })))

        self.assertEqual(result["transaction_count"], 3)
        self.assertEqual(
            {currency: total["value"] for currency, total in result["totals"]["by_currency"].items()},
            {"KZT": 50, "RUB": 300},
        )

        groups = result["groups"]
        named_foreign = [g for g in groups if g["name"] == "Иностранные сервисы"]
        self.assertEqual(len(named_foreign), 2)

        by_currency = {g["currency"]: g for g in named_foreign}
        self.assertIn("RUB", by_currency)
        self.assertIn("KZT", by_currency)
        self.assertEqual(by_currency["RUB"]["value"], 300)
        self.assertEqual(by_currency["KZT"]["value"], 50)


class GetRemindersRangeValidationTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()

    def test_only_marker_from_raises(self):
        with self.assertRaisesRegex(ValueError, "marker_from and marker_to"):
            asyncio.run(tools.tool_get_reminders({"marker_from": "2026-04-01"}))

    def test_only_marker_to_raises(self):
        with self.assertRaisesRegex(ValueError, "marker_from and marker_to"):
            asyncio.run(tools.tool_get_reminders({"marker_to": "2026-04-30"}))

    def test_empty_marker_from_raises(self):
        with self.assertRaisesRegex(ValueError, "marker_from must not be empty"):
            asyncio.run(tools.tool_get_reminders({"marker_from": ""}))

    def test_empty_marker_pair_raises(self):
        with self.assertRaisesRegex(ValueError, "marker_from must not be empty"):
            asyncio.run(tools.tool_get_reminders({
                "marker_from": "",
                "marker_to": "",
            }))

    def test_both_marker_dates_ok(self):
        result = asyncio.run(tools.tool_get_reminders({
            "marker_from": "2026-04-01",
            "marker_to": "2026-04-30",
        }))
        self.assertIsInstance(json.loads(result), dict)

    def test_neither_marker_date_ok(self):
        result = asyncio.run(tools.tool_get_reminders({}))
        self.assertIsInstance(json.loads(result), dict)


class CheckAuthStatusDispatchTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()

    def test_check_auth_status_uses_forced_live_policy(self):
        self.assertEqual(
            tools._get_sync_policy("check_auth_status"),
            tools.SYNC_POLICY_FORCED_LIVE,
        )

    def test_dispatch_skips_prefetch_and_returns_authenticated(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(read_tools, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("check_auth_status", {}))

        # _sync should be called exactly once (inside tool, not in _run_tool)
        self.assertEqual(mock_sync.call_count, 1)
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "authenticated")

    def test_dispatch_returns_structured_error_on_token_expired(self):
        mock_sync = AsyncMock(side_effect=RuntimeError("Token expired (401): unauthorized"))
        mock_close = AsyncMock(return_value=None)
        with patch.object(read_tools, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("check_auth_status", {}))

        # Even on failure, dispatch must have called _sync only ONCE (via tool only)
        self.assertEqual(mock_sync.call_count, 1)
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "error")
        self.assertIn("solution", parsed)


class ToolSyncPolicyTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()

    def test_default_policy_is_prefetch_sync(self):
        self.assertEqual(
            tools._get_sync_policy("get_accounts"),
            tools.SYNC_POLICY_PREFETCH_SYNC,
        )

    def test_setup_budget_mode_uses_cache_only_policy(self):
        self.assertEqual(
            tools._get_sync_policy("setup_budget_mode"),
            tools.SYNC_POLICY_CACHE_ONLY,
        )

    def test_suggest_uses_forced_live_policy(self):
        self.assertEqual(
            tools._get_sync_policy("suggest"),
            tools.SYNC_POLICY_FORCED_LIVE,
        )

    def test_dispatch_prefetches_for_default_policy_tool(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("get_accounts", {}))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 1)
        self.assertIsInstance(parsed, list)

    def test_dispatch_skips_prefetch_for_setup_budget_mode(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        fake_handler = AsyncMock(return_value='{"success": true}')
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None), \
             patch.dict(tools.TOOLS, {"setup_budget_mode": fake_handler}):
            result = asyncio.run(tools._run_tool("setup_budget_mode", {"mode": "income_vs_expense"}))

        self.assertEqual(mock_sync.call_count, 0)
        fake_handler.assert_awaited_once()
        self.assertEqual(fake_handler.await_args.args[0]["mode"], "income_vs_expense")
        self.assertEqual(json.loads(result), {"success": True})

    def test_dispatch_skips_prefetch_for_suggest_but_calls_api(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        mock_api_post = AsyncMock(return_value={"status": "ok", "choices": []})
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None), \
             patch.object(read_tools, "_api_post", mock_api_post):
            result = asyncio.run(tools._run_tool("suggest", {"payee": "Coffee"}))

        self.assertEqual(mock_sync.call_count, 0)
        mock_api_post.assert_awaited_once_with("/v8/suggest/", {"payee": "Coffee"})
        self.assertEqual(json.loads(result), {"status": "ok", "choices": []})

    def test_unknown_tool_returns_error_without_sync(self):
        mock_sync = AsyncMock(return_value=None)
        mock_close = AsyncMock(return_value=None)
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None):
            result = asyncio.run(tools._run_tool("does_not_exist", {}))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 0)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "UNKNOWN_TOOL")

    def test_prefetch_sync_error_short_circuits_before_handler(self):
        mock_sync = AsyncMock(side_effect=RuntimeError("network down"))
        mock_close = AsyncMock(return_value=None)
        fake_handler = AsyncMock(return_value='{"unexpected": true}')
        with patch.object(dispatch, "_sync", mock_sync), \
             patch.object(dispatch, "_close_client", mock_close), \
             patch.object(tools, "_migrate_account_meta", lambda: None), \
             patch.dict(tools.TOOLS, {"get_accounts": fake_handler}):
            result = asyncio.run(tools._run_tool("get_accounts", {}))

        parsed = json.loads(result)
        self.assertEqual(mock_sync.call_count, 1)
        fake_handler.assert_not_awaited()
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "INTERNAL_ERROR")


class GetAnalyticsCategoryPathTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble", "symbol": "₽"},
        }
        cache.CACHE.data["account"] = {
            "acct-rub": {
                "id": "acct-rub", "user": 1, "instrument": 1,
                "title": "Card RUB", "type": "ccard", "balance": 0,
            },
        }
        cache.CACHE.data["tag"] = {
            "parent-food": {"id": "parent-food", "title": "Еда", "parent": None},
            "food-pitanie": {"id": "food-pitanie", "title": "Питание", "parent": "parent-food"},
            "parent-other": {"id": "parent-other", "title": "Прочее", "parent": None},
            "other-pitanie": {"id": "other-pitanie", "title": "Питание", "parent": "parent-other"},
        }
        cache.CACHE.data["transaction"] = {
            "tx1": {
                "id": "tx1", "date": "2026-04-10",
                "income": 0, "outcome": 100,
                "incomeInstrument": 1, "outcomeInstrument": 1,
                "incomeAccount": "acct-rub", "outcomeAccount": "acct-rub",
                "tag": ["food-pitanie"],
            },
            "tx2": {
                "id": "tx2", "date": "2026-04-15",
                "income": 0, "outcome": 200,
                "incomeInstrument": 1, "outcomeInstrument": 1,
                "incomeAccount": "acct-rub", "outcomeAccount": "acct-rub",
                "tag": ["other-pitanie"],
            },
        }

    def test_duplicate_category_titles_produce_separate_buckets(self):
        result = json.loads(asyncio.run(tools.tool_get_analytics({
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "report": "outcome",
            "group_by": "category",
        })))

        self.assertEqual(result["transaction_count"], 2)
        groups = result["groups"]
        self.assertEqual(len(groups), 2, f"Expected 2 groups, got {len(groups)}: {groups}")

        names = {g["name"] for g in groups}
        self.assertIn("Еда / Питание", names)
        self.assertIn("Прочее / Питание", names)

        by_name = {g["name"]: g for g in groups}
        self.assertEqual(by_name["Еда / Питание"]["key"], "category:food-pitanie")
        self.assertEqual(by_name["Прочее / Питание"]["key"], "category:other-pitanie")
        self.assertEqual(by_name["Еда / Питание"]["value"], 100)
        self.assertEqual(by_name["Прочее / Питание"]["value"], 200)


class GetRemindersCategoryFilterTests(unittest.TestCase):
    USER_ID = 12345
    ACCOUNT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01"
    R1_ID = "r1r1r1r1-r1r1-r1r1-r1r1-r1r1r1r1r101"
    R2_ID = "r2r2r2r2-r2r2-r2r2-r2r2-r2r2r2r2r102"

    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble", "symbol": "₽"},
        }
        cache.CACHE.data["user"] = {str(self.USER_ID): {"id": self.USER_ID, "login": "u"}}
        cache.CACHE.data["account"] = {
            self.ACCOUNT_ID: {
                "id": self.ACCOUNT_ID, "user": self.USER_ID, "instrument": 1,
                "title": "Card", "type": "ccard", "balance": 1000,
            },
        }
        cache.CACHE.data["tag"] = {
            "parent-food": {"id": "parent-food", "title": "Еда", "parent": None},
            "food-pitanie": {"id": "food-pitanie", "title": "Питание", "parent": "parent-food"},
            "parent-other": {"id": "parent-other", "title": "Прочее", "parent": None},
            "other-pitanie": {"id": "other-pitanie", "title": "Питание", "parent": "parent-other"},
        }
        today = datetime.date.today()
        future = (today + datetime.timedelta(days=30)).isoformat()
        base_reminder = {
            "user": self.USER_ID,
            "changed": 1,
            "incomeInstrument": 1,
            "incomeAccount": self.ACCOUNT_ID,
            "income": 0,
            "outcomeInstrument": 1,
            "outcomeAccount": self.ACCOUNT_ID,
            "outcome": 500,
            "merchant": None,
            "payee": None,
            "comment": None,
            "interval": "month",
            "step": 1,
            "points": [1],
            "startDate": future,
            "endDate": None,
            "notify": False,
        }
        r1 = dict(base_reminder)
        r1["id"] = self.R1_ID
        r1["tag"] = ["food-pitanie"]
        r2 = dict(base_reminder)
        r2["id"] = self.R2_ID
        r2["tag"] = ["other-pitanie"]
        cache.CACHE.data["reminder"] = {
            self.R1_ID: r1,
            self.R2_ID: r2,
        }
        cache.CACHE.data["reminderMarker"] = {}

    def test_ambiguous_short_name_raises(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            asyncio.run(tools.tool_get_reminders({"category": "Питание", "active_only": False}))

    def test_full_path_returns_correct_reminder(self):
        result = json.loads(asyncio.run(tools.tool_get_reminders({
            "category": "Еда / Питание",
            "active_only": False,
        })))
        reminders = result["reminders"]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["id"], self.R1_ID)

    def test_uuid_id_returns_correct_reminder(self):
        result = json.loads(asyncio.run(tools.tool_get_reminders({
            "category": "food-pitanie",
            "active_only": False,
        })))
        reminders = result["reminders"]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["id"], self.R1_ID)

    def test_all_sentinel_raises(self):
        with self.assertRaisesRegex(ValueError, "ALL"):
            asyncio.run(tools.tool_get_reminders({"category": "ALL", "active_only": False}))

    def test_nonexistent_category_raises(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            asyncio.run(tools.tool_get_reminders({"category": "Несуществующая", "active_only": False}))


if __name__ == "__main__":
    unittest.main()
