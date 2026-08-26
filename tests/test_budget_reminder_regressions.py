import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, config, domain, reminder_tools, tools, validation
from zenmoney.plans.render import _forecast_operations


ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
ARCHIVED_ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
TAG_ID = "33333333-3333-3333-3333-333333333333"
TAG_2_ID = "44444444-4444-4444-4444-444444444444"
REMINDER_ID = "55555555-5555-5555-5555-555555555555"
MARKER_ID = "66666666-6666-6666-6666-666666666666"


def _budget_config() -> dict:
    return {
        "budget_mode": "income_vs_expense",
        "plan_settings_override": [],
        "difference_calculation_mode": "NONE",
        "accounts_meta": {},
        "round_balance_to_integer": True,
    }


def _expense_transaction(
    transaction_id: str,
    outcome: int,
    *,
    date: str = "2026-07-10",
    tag_id: str = TAG_ID,
    reminder_marker: str | None = None,
) -> dict:
    transaction = {
        "id": transaction_id,
        "date": date,
        "income": 0,
        "outcome": outcome,
        "incomeAccount": ACCOUNT_ID,
        "outcomeAccount": ACCOUNT_ID,
        "incomeInstrument": 1,
        "outcomeInstrument": 1,
        "tag": [tag_id],
    }
    if reminder_marker is not None:
        transaction["reminderMarker"] = reminder_marker
    return transaction


def _budget_entry(tag_id: str, outcome: int, *, outcome_lock: bool = False) -> dict:
    return {
        "user": 1,
        "tag": tag_id,
        "date": "2026-07-01",
        "income": 0,
        "incomeLock": False,
        "outcome": outcome,
        "outcomeLock": outcome_lock,
    }


def _expense_reminder(*, start_date: str = "2026-07-25", tag_id: str = TAG_ID) -> dict:
    return {
        "id": REMINDER_ID,
        "user": 1,
        "incomeInstrument": 1,
        "incomeAccount": ACCOUNT_ID,
        "income": 0,
        "outcomeInstrument": 1,
        "outcomeAccount": ACCOUNT_ID,
        "outcome": 100,
        "tag": [tag_id],
        "interval": "month",
        "step": 1,
        "points": [0],
        "startDate": start_date,
        "notify": True,
    }


def _marker(marker_id: str, date: str, state: str = "planned", outcome: int = 100) -> dict:
    return {
        "id": marker_id,
        "reminder": REMINDER_ID,
        "date": date,
        "state": state,
        "outcome": outcome,
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

    def _analyze_budget(self) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(_budget_config()), encoding="utf-8")
            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value="2026-07-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                return json.loads(asyncio.run(tools.tool_analyze_budget_detailed({
                    "period": "billing_period",
                    "show_forecast": False,
                })))

    @staticmethod
    def _budget_key(tag_id: str) -> str:
        return f"1:{tag_id}:2026-07-01"

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
        key = self._budget_key(domain.ALL_CATEGORIES_ID)
        cache.CACHE.data["budget"][key] = {
            "user": 1,
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
        cache.CACHE.data["reminder"][REMINDER_ID] = _expense_reminder()
        cache.CACHE.data["reminderMarker"] = {
            "planned": _marker("planned", "2026-07-25"),
            "processed": _marker("processed", "2026-07-26", "processed", 200),
            "deleted": _marker("deleted", "2026-07-27", "deleted", 900),
        }

        result = self._analyze_budget()

        self.assertEqual(result["summary"]["expense"]["planned"], 100)
        self.assertEqual(result["summary"]["expense"]["processed_planned"], 200)
        self.assertEqual(result["summary"]["expense"]["for_balance"], 300)

    def test_locked_budget_does_not_add_planned_marker_until_obligations_exceed_lock(self):
        cache.CACHE.data["transaction"] = {
            "actual": _expense_transaction("actual", 50),
        }
        cache.CACHE.data["budget"][self._budget_key(TAG_ID)] = _budget_entry(TAG_ID, 200, outcome_lock=True)
        cache.CACHE.data["reminder"][REMINDER_ID] = _expense_reminder()
        cache.CACHE.data["reminderMarker"] = {
            "planned": _marker("planned", "2026-07-25"),
        }

        result = self._analyze_budget()

        self.assertEqual(result["summary"]["expense"]["for_balance"], 200)

    def test_unlocked_budget_adds_planned_marker_to_budget_floor(self):
        cache.CACHE.data["transaction"] = {
            "actual": _expense_transaction("actual", 250),
        }
        cache.CACHE.data["budget"][self._budget_key(TAG_ID)] = _budget_entry(TAG_ID, 200)
        cache.CACHE.data["reminder"][REMINDER_ID] = _expense_reminder()
        cache.CACHE.data["reminderMarker"] = {
            "planned": _marker("planned", "2026-07-25"),
        }

        result = self._analyze_budget()

        self.assertEqual(result["summary"]["expense"]["for_balance"], 350)

    def test_processed_marker_remains_in_unlocked_budget_after_becoming_actual(self):
        cache.CACHE.data["transaction"] = {
            "actual": _expense_transaction("actual", 100, reminder_marker="processed"),
        }
        cache.CACHE.data["budget"][self._budget_key(TAG_ID)] = _budget_entry(TAG_ID, 200)
        cache.CACHE.data["reminder"][REMINDER_ID] = _expense_reminder(start_date="2026-07-10")
        cache.CACHE.data["reminderMarker"] = {
            "processed": _marker("processed", "2026-07-10", "processed"),
        }

        result = self._analyze_budget()

        self.assertEqual(result["summary"]["expense"]["category_difference_policy"], "NONE")
        self.assertEqual(result["summary"]["expense"]["processed_planned"], 100)
        self.assertEqual(result["summary"]["expense"]["for_balance"], 300)

    def test_unlinked_processed_transfer_supplies_fact_until_transaction_materializes(self):
        destination_account_id = "77777777-7777-7777-7777-777777777777"
        cache.CACHE.data["account"][destination_account_id] = {
            "id": destination_account_id,
            "user": 1,
            "instrument": 1,
            "title": "Destination",
            "type": "checking",
            "balance": 0,
            "inBalance": True,
            "archive": False,
        }
        cache.CACHE.data["reminder"][REMINDER_ID] = {
            **_expense_reminder(start_date="2026-07-10"),
            "incomeAccount": destination_account_id,
            "income": 100,
        }
        cache.CACHE.data["reminderMarker"] = {
            "processed-transfer": {
                "id": "processed-transfer",
                "reminder": REMINDER_ID,
                "date": "2026-07-10",
                "state": "processed",
                "income": 100,
                "outcome": 100,
            }
        }

        unlinked = self._analyze_budget()

        self.assertEqual(unlinked["summary"]["transfers"]["actual_out"], 100)
        self.assertEqual(unlinked["summary"]["transfers"]["remaining_net"], 0)
        self.assertEqual(unlinked["summary"]["balance_breakdown"]["current_expense"], 100)
        self.assertEqual(unlinked["summary"]["balance_breakdown"]["remaining_plan"], 0)
        self.assertEqual(unlinked["transfers"][0]["reason"], "balance_to_balance_neutral")
        self.assertEqual(
            unlinked["transfers"][0]["fallback"]["reason"],
            "unlinked_processed_marker_fallback",
        )
        self.assertEqual(unlinked["transfers"][0]["net"], -100)
        self.assertEqual(unlinked["transfers"][0]["event"]["status"], "processed")
        self.assertEqual(unlinked["transfers"][0]["fallback"]["status"], "processed")
        self.assertEqual(_forecast_operations([], unlinked["transfers"]), [])

        cache.CACHE.data["transaction"] = {
            "materialized": {
                "id": "materialized",
                "date": "2026-07-10",
                "income": 100,
                "outcome": 100,
                "incomeAccount": destination_account_id,
                "outcomeAccount": ACCOUNT_ID,
                "incomeInstrument": 1,
                "outcomeInstrument": 1,
                "tag": [],
                "reminderMarker": "processed-transfer",
            }
        }

        linked = self._analyze_budget()

        self.assertEqual(linked["summary"]["transfers"]["actual_out"], 0)
        self.assertEqual(linked["summary"]["transfers"]["remaining_net"], 0)
        self.assertEqual(linked["summary"]["balance_breakdown"]["current_expense"], 0)
        self.assertEqual(linked["summary"]["balance_breakdown"]["remaining_plan"], 0)
        marker_row = next(
            item for item in linked["transfers"] if item["event"]["id"] == "processed-transfer"
        )
        self.assertEqual(marker_row["reason"], "balance_to_balance_neutral")
        self.assertNotIn("fallback", marker_row)

    def test_actual_inbound_transfer_preserves_current_remaining_balance_identity(self):
        external_account_id = "88888888-8888-8888-8888-888888888888"
        cache.CACHE.data["account"][external_account_id] = {
            "id": external_account_id,
            "user": 1,
            "instrument": 1,
            "title": "External savings",
            "type": "checking",
            "balance": 0,
            "inBalance": False,
            "savings": True,
            "archive": False,
        }
        cache.CACHE.data["transaction"] = {
            "inbound": {
                "id": "inbound",
                "date": "2026-07-10",
                "income": 100,
                "outcome": 100,
                "incomeAccount": ACCOUNT_ID,
                "outcomeAccount": external_account_id,
                "incomeInstrument": 1,
                "outcomeInstrument": 1,
                "tag": [],
            }
        }

        result = self._analyze_budget()
        summary = result["summary"]
        breakdown = summary["balance_breakdown"]

        self.assertEqual(summary["transfers"]["actual_in"], 100)
        self.assertEqual(summary["transfers"]["actual_net"], -100)
        self.assertEqual(breakdown["current_expense"], -100)
        self.assertEqual(breakdown["remaining_plan"], 0)
        self.assertEqual(
            summary["balance"],
            summary["opening_balance"]["total"]
            + summary["income"]["for_balance"]
            + summary["exchange_difference"]["fact"]
            - breakdown["current_expense"]
            - breakdown["remaining_plan"],
        )

    def test_zero_locked_budget_does_not_reserve_planned_marker(self):
        cache.CACHE.data["budget"][self._budget_key(TAG_ID)] = _budget_entry(TAG_ID, 0, outcome_lock=True)
        cache.CACHE.data["reminder"][REMINDER_ID] = _expense_reminder()
        cache.CACHE.data["reminderMarker"] = {
            "planned": _marker("planned", "2026-07-25"),
        }

        result = self._analyze_budget()

        self.assertEqual(result["summary"]["expense"]["for_balance"], 0)

    def test_all_budget_is_not_added_to_plans_category_total(self):
        cache.CACHE.data["budget"][self._budget_key(domain.ALL_CATEGORIES_ID)] = _budget_entry(
            domain.ALL_CATEGORIES_ID,
            1000,
            outcome_lock=True,
        )

        result = self._analyze_budget()

        self.assertEqual(result["summary"]["expense"]["budget"], 1000)
        self.assertEqual(result["summary"]["expense"]["category_budget"], 0)
        self.assertEqual(result["summary"]["expense"]["aggregate_budget"], 1000)
        self.assertEqual(result["summary"]["expense"]["remaining"], 0)
        self.assertEqual(result["summary"]["expense"]["for_balance"], 0)

    def test_parent_actual_is_kept_when_child_has_remaining_reserve(self):
        cache.CACHE.data["tag"][TAG_2_ID]["parent"] = TAG_ID
        cache.CACHE.data["transaction"] = {
            "parent-actual": _expense_transaction("parent-actual", 50),
        }
        cache.CACHE.data["budget"][self._budget_key(TAG_2_ID)] = _budget_entry(TAG_2_ID, 200)

        result = self._analyze_budget()

        self.assertEqual(result["summary"]["expense"]["actual"], 50)
        self.assertEqual(result["summary"]["expense"]["for_balance"], 250)

    def test_locked_parent_stops_child_budget_propagation_without_hiding_child_reserve(self):
        cache.CACHE.data["tag"][TAG_2_ID]["parent"] = TAG_ID
        cache.CACHE.data["budget"] = {
            self._budget_key(TAG_ID): _budget_entry(TAG_ID, 150, outcome_lock=True),
            self._budget_key(TAG_2_ID): _budget_entry(TAG_2_ID, 200),
        }

        result = self._analyze_budget()

        self.assertEqual(result["summary"]["expense"]["for_balance"], 200)

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

    def test_forecast_works_without_calendar_and_includes_archived_in_balance_accounts(self):
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
            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(budget_tools, "_today", return_value="2026-07-20"), \
                 patch.object(validation, "_today", return_value="2026-07-20"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 20}):
                    result = json.loads(asyncio.run(tools.tool_analyze_budget_detailed({
                        "period": "billing_period",
                        "show_calendar": False,
                        "show_forecast": True,
                    })))

        self.assertNotIn("calendar", result)
        self.assertEqual(result["forecast"][0]["date"], "2026-07-20")
        self.assertEqual(result["forecast"][-1]["date"], "2026-08-19")
        self.assertTrue(all("amount" in point for point in result["forecast"]))


if __name__ == "__main__":
    unittest.main()
