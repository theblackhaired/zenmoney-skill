import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, domain, reminder_tools, tools


ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
ARCHIVED_ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
TAG_ID = "33333333-3333-3333-3333-333333333333"
TAG_2_ID = "44444444-4444-4444-4444-444444444444"
REMINDER_ID = "55555555-5555-5555-5555-555555555555"
MARKER_ID = "66666666-6666-6666-6666-666666666666"


def _budget_config() -> dict:
    return {
        "budget_mode_configured": True,
        "budget_mode": "income_vs_expense",
        "budget_modes": {
            "income_vs_expense": {
                "label": "Income vs Expense",
                "count_all_movements": False,
                "income": {"from_savings": True},
                "expense": {"to_credit": True},
            }
        },
        "accounts_meta": {},
        "round_balance_to_integer": True,
    }


class BudgetReminderRegressionTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Ruble"},
        }
        cache.CACHE.data["user"] = {"1": {"id": 1}}
        cache.CACHE.data["account"] = {
            ACCOUNT_ID: {
                "id": ACCOUNT_ID,
                "user": 1,
                "instrument": 1,
                "title": "Main",
                "type": "ccard",
                "balance": 1000,
                "inBalance": True,
                "archive": False,
            },
            ARCHIVED_ACCOUNT_ID: {
                "id": ARCHIVED_ACCOUNT_ID,
                "user": 1,
                "instrument": 1,
                "title": "Archived",
                "type": "ccard",
                "balance": 9999,
                "inBalance": True,
                "archive": True,
            },
        }
        cache.CACHE.data["tag"] = {
            TAG_ID: {"id": TAG_ID, "title": "Food", "parent": None},
            TAG_2_ID: {"id": TAG_2_ID, "title": "Transport", "parent": None},
        }

    def test_budget_formatter_distinguishes_aggregate_and_uncategorized(self):
        aggregate = domain._fmt_budget({
            "tag": domain.ALL_CATEGORIES_ID,
            "date": "2026-07-01",
            "income": 0,
            "outcome": 100,
        })
        uncategorized = domain._fmt_budget({
            "tag": None,
            "date": "2026-07-01",
            "income": 0,
            "outcome": 50,
        })

        self.assertEqual(aggregate["category"], "ALL (aggregate)")
        self.assertEqual(aggregate["category_id"], domain.ALL_CATEGORIES_ID)
        self.assertEqual(uncategorized["category"], "Uncategorized")
        self.assertIsNone(uncategorized["category_id"])

    def test_create_budget_all_uses_zero_uuid_without_payload_id(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(budget_tools, "_write_diff", side_effect=fake_write_diff):
            result = json.loads(asyncio.run(tools.tool_create_budget({
                "month": "2026-07",
                "category": "ALL",
                "outcome": 100,
            })))

        budget = captured["diff"]["budget"][0]
        self.assertNotIn("id", budget)
        self.assertEqual(budget["tag"], domain.ALL_CATEGORIES_ID)
        self.assertEqual(result["budget"]["category_id"], domain.ALL_CATEGORIES_ID)

    def test_delete_budget_zeroes_amounts_and_unlocks(self):
        key = f"{domain.ALL_CATEGORIES_ID}:2026-07-01"
        cache.CACHE.data["budget"][key] = {
            "tag": domain.ALL_CATEGORIES_ID,
            "date": "2026-07-01",
            "income": 10,
            "outcome": 20,
            "incomeLock": True,
            "outcomeLock": True,
        }
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(budget_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(tools.tool_delete_budget({"month": "2026-07", "category": "ALL"}))

        deleted = captured["diff"]["budget"][0]
        self.assertEqual(deleted["income"], 0)
        self.assertEqual(deleted["outcome"], 0)
        self.assertFalse(deleted["incomeLock"])
        self.assertFalse(deleted["outcomeLock"])

    def test_deleted_marker_state_is_excluded_from_get_reminders_even_processed_mode(self):
        cache.CACHE.data["reminder"][REMINDER_ID] = {
            "id": REMINDER_ID,
            "user": 1,
            "incomeInstrument": 1,
            "incomeAccount": ACCOUNT_ID,
            "income": 0,
            "outcomeInstrument": 1,
            "outcomeAccount": ACCOUNT_ID,
            "outcome": 100,
            "tag": [TAG_ID],
            "interval": "month",
            "step": 1,
            "points": [0],
            "startDate": "2026-07-25",
            "notify": True,
        }
        cache.CACHE.data["reminderMarker"] = {
            "planned": {"id": "planned", "reminder": REMINDER_ID, "date": "2026-07-25", "state": "planned", "outcome": 100},
            "processed": {"id": "processed", "reminder": REMINDER_ID, "date": "2026-07-26", "state": "processed", "outcome": 200},
            "deleted": {"id": "deleted", "reminder": REMINDER_ID, "date": "2026-07-27", "state": "deleted", "outcome": 900},
        }

        result = json.loads(asyncio.run(tools.tool_get_reminders({
            "include_processed": True,
            "active_only": False,
            "marker_from": "2026-07-01",
            "marker_to": "2026-07-31",
        })))

        markers = result["reminders"][0]["markers"]
        self.assertEqual({m["id"] for m in markers}, {"planned", "processed"})
        self.assertEqual(result["reminders"][0]["markers_total_outcome"], 300)

    def test_deleted_marker_state_is_excluded_from_plan_totals(self):
        cache.CACHE.data["reminder"][REMINDER_ID] = {
            "id": REMINDER_ID,
            "user": 1,
            "incomeInstrument": 1,
            "incomeAccount": ACCOUNT_ID,
            "income": 0,
            "outcomeInstrument": 1,
            "outcomeAccount": ACCOUNT_ID,
            "outcome": 100,
            "tag": [TAG_ID],
            "interval": "month",
            "step": 1,
            "points": [0],
            "startDate": "2026-07-25",
            "notify": True,
        }
        cache.CACHE.data["reminderMarker"] = {
            "planned": {"id": "planned", "reminder": REMINDER_ID, "date": "2026-07-25", "state": "planned", "outcome": 100},
            "processed": {"id": "processed", "reminder": REMINDER_ID, "date": "2026-07-26", "state": "processed", "outcome": 200},
            "deleted": {"id": "deleted", "reminder": REMINDER_ID, "date": "2026-07-27", "state": "deleted", "outcome": 900},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(_budget_config()), encoding="utf-8")
            with patch.object(budget_tools, "_cfg_path", config_path):
                result = json.loads(asyncio.run(tools.tool_analyze_budget_detailed({
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-31",
                    "show_forecast": False,
                })))

        self.assertEqual(result["summary"]["expense"]["planned"], 100)
        self.assertEqual(result["summary"]["expense"]["for_balance"], 100)

    def test_create_reminder_defaults_recurring_points_to_zero_and_sets_forecast_flag(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff):
            result = json.loads(asyncio.run(tools.tool_create_reminder({
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "interval": "month",
                "start_date": "2099-01-31",
                "generate_markers": 1,
            })))

        reminder = captured["diff"]["reminder"][0]
        marker = captured["diff"]["reminderMarker"][0]
        self.assertEqual(reminder["points"], [0])
        self.assertEqual(result["reminder"]["points"], [0])
        self.assertFalse(marker["isForecast"])
        self.assertEqual(marker["date"], "2099-01-31")

    def test_update_reminder_propagates_metadata_to_planned_markers(self):
        cache.CACHE.data["reminder"][REMINDER_ID] = {
            "id": REMINDER_ID,
            "user": 1,
            "incomeInstrument": 1,
            "incomeAccount": ACCOUNT_ID,
            "income": 0,
            "outcomeInstrument": 1,
            "outcomeAccount": ACCOUNT_ID,
            "outcome": 100,
            "tag": [TAG_ID],
            "payee": "Old",
            "comment": "Old comment",
            "interval": "month",
            "step": 1,
            "points": [0],
            "startDate": "2099-01-31",
            "notify": True,
        }
        cache.CACHE.data["reminderMarker"][MARKER_ID] = {
            "id": MARKER_ID,
            "user": 1,
            "reminder": REMINDER_ID,
            "date": "2099-01-31",
            "state": "planned",
            "outcome": 100,
            "tag": [TAG_ID],
            "payee": "Old",
            "comment": "Old comment",
            "notify": True,
        }
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(tools.tool_update_reminder({
                "id": REMINDER_ID,
                "category_ids": [TAG_2_ID],
                "payee": "New",
                "comment": "New comment",
                "notify": False,
            }))

        marker = captured["diff"]["reminderMarker"][0]
        self.assertEqual(marker["tag"], [TAG_2_ID])
        self.assertEqual(marker["payee"], "New")
        self.assertEqual(marker["comment"], "New comment")
        self.assertFalse(marker["notify"])
        self.assertFalse(marker["isForecast"])

    def test_update_reminder_validates_points_against_existing_step(self):
        cache.CACHE.data["reminder"][REMINDER_ID] = {
            "id": REMINDER_ID,
            "user": 1,
            "incomeInstrument": 1,
            "incomeAccount": ACCOUNT_ID,
            "income": 0,
            "outcomeInstrument": 1,
            "outcomeAccount": ACCOUNT_ID,
            "outcome": 100,
            "tag": [TAG_ID],
            "payee": "Old",
            "comment": "Old comment",
            "interval": "month",
            "step": 1,
            "points": [0],
            "startDate": "2099-01-31",
            "notify": True,
        }

        with patch.object(reminder_tools, "_write_diff") as mocked_write:
            with self.assertRaisesRegex(ValueError, "less than step"):
                asyncio.run(tools.tool_update_reminder({
                    "id": REMINDER_ID,
                    "points": [1],
                }))

        mocked_write.assert_not_called()

    def test_one_time_marker_payload_writes_parent_first(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(reminder_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(tools.tool_create_reminder_marker({
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "date": "2026-08-01",
            }))

        self.assertEqual(list(captured["diff"].keys()), ["reminder", "reminderMarker"])
        reminder = captured["diff"]["reminder"][0]
        marker = captured["diff"]["reminderMarker"][0]
        self.assertIsNone(reminder["interval"])
        self.assertEqual(reminder["step"], 0)
        self.assertEqual(reminder["points"], [0])
        self.assertFalse(marker["isForecast"])
        self.assertEqual(marker["reminder"], reminder["id"])

    def test_forecast_works_without_calendar_and_skips_archived_and_past_actuals(self):
        cache.CACHE.data["transaction"] = {
            "past-income": {
                "id": "past-income",
                "date": "2026-07-10",
                "income": 500,
                "outcome": 0,
                "incomeAccount": ACCOUNT_ID,
                "outcomeAccount": ACCOUNT_ID,
                "incomeInstrument": 1,
                "outcomeInstrument": 1,
                "tag": [],
            }
        }
        cache.CACHE.data["reminder"][REMINDER_ID] = {
            "id": REMINDER_ID,
            "user": 1,
            "incomeInstrument": 1,
            "incomeAccount": ACCOUNT_ID,
            "income": 200,
            "outcomeInstrument": 1,
            "outcomeAccount": ACCOUNT_ID,
            "outcome": 0,
            "tag": [],
            "interval": "month",
            "step": 1,
            "points": [0],
            "startDate": "2026-08-01",
            "notify": True,
        }
        cache.CACHE.data["reminderMarker"] = {
            "future-income": {
                "id": "future-income",
                "reminder": REMINDER_ID,
                "date": "2026-08-01",
                "state": "planned",
                "income": 200,
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(_budget_config()), encoding="utf-8")
            with patch.object(budget_tools, "_cfg_path", config_path):
                result = json.loads(asyncio.run(tools.tool_analyze_budget_detailed({
                    "start_date": "2026-07-01",
                    "end_date": "2026-08-31",
                    "show_calendar": False,
                    "show_forecast": True,
                })))

        self.assertNotIn("calendar", result)
        self.assertEqual(result["forecast"], [
            {"date": "2026-08-01", "balance": 1200, "operations_count": 1},
        ])


if __name__ == "__main__":
    unittest.main()
