import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, config, tools, validation


ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
PARENT_ID = "22222222-2222-2222-2222-222222222222"
CHILD_ID = "33333333-3333-3333-3333-333333333333"
REMINDER_ID = "44444444-4444-4444-4444-444444444444"


class BudgetIncomeLockContractTests(unittest.TestCase):
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
                "type": "checking",
                "balance": 0,
                "inBalance": True,
                "archive": False,
            },
        }
        cache.CACHE.data["tag"] = {
            PARENT_ID: {"id": PARENT_ID, "title": "Income", "parent": None},
            CHILD_ID: {"id": CHILD_ID, "title": "Salary", "parent": PARENT_ID},
        }

    @staticmethod
    def _budget(tag_id: str, income: int, *, locked: bool) -> dict:
        return {
            "user": 1,
            "tag": tag_id,
            "date": "2026-07-01",
            "income": income,
            "incomeLock": locked,
            "outcome": 0,
            "outcomeLock": False,
        }

    @staticmethod
    def _income_transaction(amount: int, *, tag_id: str = CHILD_ID) -> dict:
        return {
            "id": "income-actual",
            "date": "2026-07-10",
            "income": amount,
            "outcome": 0,
            "incomeAccount": ACCOUNT_ID,
            "outcomeAccount": ACCOUNT_ID,
            "incomeInstrument": 1,
            "outcomeInstrument": 1,
            "tag": [tag_id],
        }

    @staticmethod
    def _income_reminder(*, tag_id: str = CHILD_ID) -> dict:
        return {
            "id": REMINDER_ID,
            "user": 1,
            "incomeInstrument": 1,
            "incomeAccount": ACCOUNT_ID,
            "income": 100,
            "outcomeInstrument": 1,
            "outcomeAccount": ACCOUNT_ID,
            "outcome": 0,
            "tag": [tag_id],
            "interval": "month",
            "step": 1,
            "points": [0],
            "startDate": "2026-07-20",
        }

    @staticmethod
    def _marker(marker_id: str, amount: int, state: str) -> dict:
        return {
            "id": marker_id,
            "reminder": REMINDER_ID,
            "date": "2026-07-20",
            "state": state,
            "income": amount,
            "outcome": 0,
        }

    def _analyze(self) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({
                "budget_mode": "income_vs_expense",
                "plan_settings_override": [],
                "difference_calculation_mode": "NONE",
                "accounts_meta": {},
                "round_balance_to_integer": True,
            }), encoding="utf-8")
            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value="2026-07-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                return json.loads(asyncio.run(tools.tool_analyze_budget_detailed({
                    "period": "billing_period",
                    "show_forecast": False,
                    "show_calendar": False,
                })))

    def _seed_income_activity(self) -> None:
        cache.CACHE.data["transaction"] = {
            "income-actual": self._income_transaction(250),
        }
        cache.CACHE.data["reminder"] = {
            REMINDER_ID: self._income_reminder(),
        }
        cache.CACHE.data["reminderMarker"] = {
            "planned": self._marker("planned", 100, "planned"),
            "processed": self._marker("processed", 50, "processed"),
        }

    def test_unlocked_income_budget_adds_planned_and_processed_markers(self):
        self._seed_income_activity()
        budget = self._budget(CHILD_ID, 1000, locked=False)
        cache.CACHE.data["budget"] = {cache.Cache._budget_key(budget): budget}

        result = self._analyze()

        child = result["income"][0]["children"][0]
        self.assertEqual(child["actual"], 250)
        self.assertEqual(child["planned"], 100)
        self.assertEqual(child["processed"], 50)
        self.assertEqual(child["explicit_budget"], 1000)
        self.assertEqual(child["effective_budget"], 1150)
        self.assertEqual(child["residue"], 900)
        self.assertFalse(child["income_lock"])

        summary = result["summary"]["income"]
        self.assertEqual(summary["actual"], 250)
        self.assertEqual(summary["planned"], 100)
        self.assertEqual(summary["processed"], 50)
        self.assertEqual(summary["explicit_budget"], 1000)
        self.assertEqual(summary["effective_budget"], 1150)
        self.assertEqual(summary["residue"], 900)
        self.assertEqual(summary["for_balance"], 1150)
        self.assertEqual(result["summary"]["balance_breakdown"]["total_income"], 1150)

    def test_locked_income_budget_keeps_explicit_value(self):
        self._seed_income_activity()
        budget = self._budget(CHILD_ID, 1000, locked=True)
        cache.CACHE.data["budget"] = {cache.Cache._budget_key(budget): budget}

        result = self._analyze()

        child = result["income"][0]["children"][0]
        self.assertEqual(child["explicit_budget"], 1000)
        self.assertEqual(child["effective_budget"], 1000)
        self.assertEqual(child["residue"], 750)
        self.assertTrue(child["income_lock"])
        self.assertEqual(result["summary"]["income"]["for_balance"], 1000)

    def test_income_budget_row_is_preserved_without_activity(self):
        budget = self._budget(CHILD_ID, 700, locked=True)
        cache.CACHE.data["budget"] = {cache.Cache._budget_key(budget): budget}

        result = self._analyze()

        child = result["income"][0]["children"][0]
        self.assertEqual(child["actual"], 0)
        self.assertEqual(child["explicit_budget"], 700)
        self.assertEqual(child["effective_budget"], 700)
        self.assertEqual(child["residue"], 700)
        self.assertEqual(result["summary"]["income"]["for_balance"], 700)

    def test_uncategorized_income_budget_is_not_dropped(self):
        budget = self._budget(None, 300, locked=True)
        cache.CACHE.data["budget"] = {cache.Cache._budget_key(budget): budget}

        result = self._analyze()

        uncategorized = result["income"][0]
        self.assertEqual(uncategorized["category_id"], "uncategorized")
        self.assertEqual(uncategorized["category_name"], "Без категории")
        self.assertEqual(uncategorized["explicit_budget"], 300)
        self.assertEqual(result["summary"]["income"]["for_balance"], 300)

    def test_locked_parent_suppresses_child_budget_but_rolls_up_activity(self):
        self._seed_income_activity()
        parent_budget = self._budget(PARENT_ID, 500, locked=True)
        child_budget = self._budget(CHILD_ID, 200, locked=False)
        cache.CACHE.data["budget"] = {
            cache.Cache._budget_key(parent_budget): parent_budget,
            cache.Cache._budget_key(child_budget): child_budget,
        }

        result = self._analyze()

        parent = result["income"][0]
        child = parent["children"][0]
        self.assertEqual(child["effective_budget"], 350)
        self.assertEqual(parent["actual"], 250)
        self.assertEqual(parent["planned"], 100)
        self.assertEqual(parent["processed"], 50)
        self.assertEqual(parent["explicit_budget"], 500)
        self.assertEqual(parent["effective_budget"], 500)
        self.assertEqual(parent["residue"], 250)

        summary = result["summary"]["income"]
        self.assertEqual(summary["explicit_budget"], 700)
        self.assertEqual(summary["effective_budget"], 500)
        self.assertEqual(summary["residue"], 250)
        self.assertEqual(summary["for_balance"], 500)

    def test_unlocked_parent_rolls_up_child_effective_budget_once(self):
        self._seed_income_activity()
        parent_budget = self._budget(PARENT_ID, 500, locked=False)
        child_budget = self._budget(CHILD_ID, 200, locked=False)
        cache.CACHE.data["budget"] = {
            cache.Cache._budget_key(parent_budget): parent_budget,
            cache.Cache._budget_key(child_budget): child_budget,
        }

        result = self._analyze()

        parent = result["income"][0]
        self.assertEqual(parent["explicit_budget"], 500)
        self.assertEqual(parent["effective_budget"], 850)
        self.assertEqual(parent["residue"], 600)
        self.assertEqual(result["summary"]["income"]["for_balance"], 850)


if __name__ == "__main__":
    unittest.main()
