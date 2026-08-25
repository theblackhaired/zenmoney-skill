import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, config, dispatch, tools, validation


ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
TAG_ID = "22222222-2222-2222-2222-222222222222"
MISSING_PARENT_ID = "33333333-3333-3333-3333-333333333333"
CHILD_ID = "44444444-4444-4444-4444-444444444444"


def _budget_config() -> dict:
    return {
        "budget_mode": "income_vs_expense",
        "plan_settings_override": [],
        "difference_calculation_mode": "NONE",
        "accounts_meta": {},
        "round_balance_to_integer": True,
    }


def _account(*, instrument: int = 1) -> dict:
    return {
        "id": ACCOUNT_ID,
        "user": 1,
        "instrument": instrument,
        "title": "Main",
        "type": "ccard",
        "balance": 1000,
        "creditLimit": 0,
        "inBalance": True,
        "archive": False,
    }


def _expense_transaction() -> dict:
    return {
        "id": "tx-expense",
        "date": "2026-07-10",
        "income": 0,
        "outcome": 100,
        "incomeAccount": ACCOUNT_ID,
        "outcomeAccount": ACCOUNT_ID,
        "incomeInstrument": 1,
        "outcomeInstrument": 1,
        "tag": [TAG_ID],
    }


class BudgetEdgeContractTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Ruble", "rate": 1},
        }
        cache.CACHE.data["user"] = {"1": {"id": 1}}
        cache.CACHE.data["account"] = {ACCOUNT_ID: _account()}
        cache.CACHE.data["tag"] = {
            TAG_ID: {"id": TAG_ID, "title": "Food", "parent": None},
        }

    def _run_analyze(self, args: dict) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(_budget_config()), encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(cache.CACHE, "load", lambda: None), \
                 patch.object(dispatch, "_sync", AsyncMock(return_value=None)), \
                 patch.object(dispatch, "_close_client", AsyncMock(return_value=None)), \
                 patch.object(tools, "_migrate_account_meta", lambda: None), \
                 patch.object(validation, "_today", return_value="2026-07-15"), \
                 patch.object(budget_tools, "_today", return_value="2026-07-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                return json.loads(asyncio.run(tools._run_tool("analyze_budget_detailed", args)))

    def test_returns_unknown_currency_when_included_account_currency_is_not_synced(self):
        cache.CACHE.data["account"] = {ACCOUNT_ID: _account(instrument=999)}
        cache.CACHE.data["transaction"] = {"tx-expense": _expense_transaction()}

        result = self._run_analyze({
            "period": "billing_period",
            "show_forecast": False,
            "show_calendar": False,
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "UNKNOWN_CURRENCY")

    def test_future_billing_period_uses_recursive_plans_opening_contract(self):
        result = self._run_analyze({
            "period": "billing_period",
            "period_offset": 1,
            "show_forecast": False,
            "show_calendar": False,
        })

        self.assertNotIn("status", result)
        self.assertEqual(result["summary"]["period"]["offset"], 1)
        self.assertEqual(result["summary"]["opening_balance"]["source"], "excluded")

    def test_future_included_opening_uses_previous_period_summary_balance(self):
        config_payload = _budget_config()
        config_payload["plan_settings_override"] = ["includeOpeningBalance"]
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(config_payload), encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(cache.CACHE, "load", lambda: None), \
                 patch.object(dispatch, "_sync", AsyncMock(return_value=None)), \
                 patch.object(dispatch, "_close_client", AsyncMock(return_value=None)), \
                 patch.object(tools, "_migrate_account_meta", lambda: None), \
                 patch.object(validation, "_today", return_value="2026-07-15"), \
                 patch.object(budget_tools, "_today", return_value="2026-07-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                result = json.loads(
                    asyncio.run(
                        tools._run_tool(
                            "analyze_budget_detailed",
                            {
                                "period": "billing_period",
                                "period_offset": 1,
                                "show_forecast": False,
                                "show_calendar": False,
                            },
                        )
                    )
                )

        self.assertNotIn("status", result)
        opening = result["summary"]["opening_balance"]
        self.assertEqual(opening["source"], "previous_day_summary")
        self.assertEqual(opening["total"], 1000)
        self.assertEqual(
            opening["recursion_policy"],
            {
                "plan_balance_mode": "EXCLUDE_OPENING_BALANCE",
                "plan_settings": ["INCLUDE_OPENING_BALANCE"],
            },
        )

    def test_previous_period_with_forecast_returns_zero_daily_forecast(self):
        result = self._run_analyze({
            "period": "billing_period",
            "period_offset": -1,
            "show_forecast": True,
            "show_calendar": False,
        })

        self.assertNotIn("status", result)
        self.assertTrue(result["forecast"])
        self.assertTrue(all(point["amount"] == 0 for point in result["forecast"]))

    def test_future_forecast_excludes_planned_income_from_expense_residue(self):
        cache.CACHE.data["reminder"] = {
            "expense-reminder": {
                "id": "expense-reminder",
                "outcomeAccount": ACCOUNT_ID,
                "outcome": 100,
                "incomeAccount": ACCOUNT_ID,
                "income": 0,
                "tag": [TAG_ID],
            },
            "income-reminder": {
                "id": "income-reminder",
                "incomeAccount": ACCOUNT_ID,
                "income": 1000,
                "outcomeAccount": ACCOUNT_ID,
                "outcome": 0,
                "tag": [TAG_ID],
            },
        }
        cache.CACHE.data["reminderMarker"] = {
            "expense-marker": {
                "id": "expense-marker",
                "reminder": "expense-reminder",
                "date": "2026-08-10",
                "state": "planned",
                "outcome": 100,
                "income": 0,
            },
            "income-marker": {
                "id": "income-marker",
                "reminder": "income-reminder",
                "date": "2026-08-11",
                "state": "planned",
                "income": 1000,
                "outcome": 0,
            },
        }

        result = self._run_analyze({
            "period": "billing_period",
            "period_offset": 1,
            "show_forecast": True,
            "show_calendar": False,
        })

        by_date = {point["date"]: point for point in result["forecast"]}
        self.assertEqual(by_date["2026-08-10"]["planned_amount"], 100)
        self.assertEqual(by_date["2026-08-11"]["planned_amount"], 0)
        self.assertEqual(result["forecast"][-1]["cumulative"], 100)

    def test_income_only_future_period_yields_zero_forecast_residue(self):
        cache.CACHE.data["reminder"] = {
            "income-reminder": {
                "id": "income-reminder",
                "incomeAccount": ACCOUNT_ID,
                "income": 1000,
                "outcomeAccount": ACCOUNT_ID,
                "outcome": 0,
                "tag": [TAG_ID],
            },
        }
        cache.CACHE.data["reminderMarker"] = {
            "income-marker": {
                "id": "income-marker",
                "reminder": "income-reminder",
                "date": "2026-08-11",
                "state": "planned",
                "income": 1000,
                "outcome": 0,
            },
        }

        result = self._run_analyze({
            "period": "billing_period",
            "period_offset": 1,
            "show_forecast": True,
            "show_calendar": False,
        })

        self.assertTrue(all(point["amount"] == 0 for point in result["forecast"]))
        self.assertEqual(result["forecast"][-1]["cumulative"], 0)

    def test_parent_category_own_planned_marker_reaches_calendar(self):
        cache.CACHE.data["tag"] = {
            TAG_ID: {"id": TAG_ID, "title": "Parent", "parent": None},
            CHILD_ID: {"id": CHILD_ID, "title": "Child", "parent": TAG_ID},
        }
        cache.CACHE.data["reminder"] = {
            "parent-reminder": {
                "id": "parent-reminder",
                "outcomeAccount": ACCOUNT_ID,
                "outcome": 80,
                "incomeAccount": ACCOUNT_ID,
                "income": 0,
                "tag": [TAG_ID],
            },
            "child-reminder": {
                "id": "child-reminder",
                "outcomeAccount": ACCOUNT_ID,
                "outcome": 20,
                "incomeAccount": ACCOUNT_ID,
                "income": 0,
                "tag": [CHILD_ID],
            },
        }
        cache.CACHE.data["reminderMarker"] = {
            "parent-marker": {
                "id": "parent-marker",
                "reminder": "parent-reminder",
                "date": "2026-07-20",
                "state": "planned",
                "outcome": 80,
                "income": 0,
            },
            "child-marker": {
                "id": "child-marker",
                "reminder": "child-reminder",
                "date": "2026-07-21",
                "state": "planned",
                "outcome": 20,
                "income": 0,
            },
        }

        result = self._run_analyze({
            "period": "billing_period",
            "show_forecast": True,
            "show_calendar": True,
        })

        calendar_amounts = {
            (item["date"], item["category"]): item["amount"]
            for item in result["calendar"]
            if item["type"] == "expense"
        }
        self.assertEqual(calendar_amounts[("2026-07-20", "Parent")], 80)
        self.assertEqual(calendar_amounts[("2026-07-21", "Child")], 20)
        forecast_by_date = {point["date"]: point for point in result["forecast"]}
        self.assertEqual(forecast_by_date["2026-07-20"]["planned_amount"], 80)
        self.assertEqual(forecast_by_date["2026-07-21"]["planned_amount"], 20)
        self.assertEqual(result["forecast"][-1]["cumulative"], 100)

    def test_forecast_transfer_markers_do_not_count_as_plans_payments(self):
        loan_account = "55555555-5555-5555-5555-555555555555"
        cache.CACHE.data["account"][loan_account] = {
            "id": loan_account,
            "user": 1,
            "instrument": 1,
            "title": "Credit card",
            "type": "ccard",
            "balance": -5000,
            "creditLimit": 100000,
            "inBalance": False,
            "archive": False,
        }
        cache.CACHE.data["reminder"] = {
            "loan-reminder": {
                "id": "loan-reminder",
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": loan_account,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "tag": [],
            }
        }
        cache.CACHE.data["reminderMarker"] = {
            "forecast-loan-marker": {
                "id": "forecast-loan-marker",
                "reminder": "loan-reminder",
                "date": "2026-07-20",
                "state": "planned",
                "isForecast": True,
                "outcome": 10000,
                "income": 10000,
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": loan_account,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "tag": [],
            }
        }

        result = self._run_analyze({
            "period": "billing_period",
            "show_forecast": True,
            "show_calendar": True,
        })

        self.assertEqual(result["summary"]["transfers"]["out"], 0)
        self.assertEqual(result["summary"]["balance"], 0)
        self.assertEqual(result["transfers"], [])
        self.assertTrue(all(point["planned_amount"] == 0 for point in result["forecast"]))

    def test_ordinary_planned_transfer_marker_still_counts_as_plan_payment(self):
        loan_account = "55555555-5555-5555-5555-555555555555"
        cache.CACHE.data["account"][loan_account] = {
            "id": loan_account,
            "user": 1,
            "instrument": 1,
            "title": "Credit card",
            "type": "ccard",
            "balance": -5000,
            "creditLimit": 100000,
            "inBalance": False,
            "archive": False,
        }
        cache.CACHE.data["reminder"] = {
            "loan-reminder": {
                "id": "loan-reminder",
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": loan_account,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "tag": [],
            }
        }
        cache.CACHE.data["reminderMarker"] = {
            "planned-loan-marker": {
                "id": "planned-loan-marker",
                "reminder": "loan-reminder",
                "date": "2026-07-20",
                "state": "planned",
                "outcome": 10000,
                "income": 10000,
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": loan_account,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "tag": [],
            }
        }

        result = self._run_analyze({
            "period": "billing_period",
            "show_forecast": True,
            "show_calendar": True,
        })

        self.assertEqual(result["summary"]["transfers"]["out"], 10000)
        self.assertEqual(result["summary"]["balance"], -10000)
        self.assertEqual(len(result["transfers"]), 1)
        forecast_by_date = {point["date"]: point for point in result["forecast"]}
        self.assertEqual(forecast_by_date["2026-07-20"]["planned_amount"], 10000)

    def test_forecast_category_marker_does_not_count_as_plans_payment(self):
        cache.CACHE.data["reminder"] = {
            "forecast-category-reminder": {
                "id": "forecast-category-reminder",
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": ACCOUNT_ID,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "tag": [TAG_ID],
            }
        }
        cache.CACHE.data["reminderMarker"] = {
            "forecast-category-marker": {
                "id": "forecast-category-marker",
                "reminder": "forecast-category-reminder",
                "date": "2026-07-20",
                "state": "planned",
                "isForecast": True,
                "outcome": 80,
                "income": 0,
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": ACCOUNT_ID,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "tag": [TAG_ID],
            }
        }

        result = self._run_analyze({
            "period": "billing_period",
            "show_forecast": True,
            "show_calendar": True,
        })

        self.assertEqual(result["summary"]["expense"]["planned"], 0)
        self.assertEqual(result["summary"]["expense"]["for_balance"], 0)
        self.assertEqual(result["calendar"], [])
        self.assertTrue(all(point["planned_amount"] == 0 for point in result["forecast"]))

    def test_deleted_transfer_marker_does_not_count_as_fact_or_plan(self):
        loan_account = "55555555-5555-5555-5555-555555555555"
        cache.CACHE.data["account"][loan_account] = {
            "id": loan_account,
            "user": 1,
            "instrument": 1,
            "title": "Credit card",
            "type": "ccard",
            "balance": -5000,
            "creditLimit": 100000,
            "inBalance": False,
            "archive": False,
        }
        cache.CACHE.data["reminder"] = {
            "deleted-loan-reminder": {
                "id": "deleted-loan-reminder",
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": loan_account,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "tag": [],
            }
        }
        cache.CACHE.data["reminderMarker"] = {
            "deleted-loan-marker": {
                "id": "deleted-loan-marker",
                "reminder": "deleted-loan-reminder",
                "date": "2026-07-20",
                "state": "deleted",
                "outcome": 10000,
                "income": 10000,
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": loan_account,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "tag": [],
            }
        }

        result = self._run_analyze({
            "period": "billing_period",
            "show_forecast": True,
            "show_calendar": True,
        })

        self.assertEqual(result["summary"]["transfers"]["out"], 0)
        self.assertEqual(result["summary"]["balance"], 0)
        self.assertEqual(result["transfers"], [])
        self.assertEqual(result["calendar"], [])

    def test_preserves_category_when_parent_reference_is_missing(self):
        cache.CACHE.data["tag"] = {
            TAG_ID: {"id": TAG_ID, "title": "Food", "parent": MISSING_PARENT_ID},
        }
        cache.CACHE.data["transaction"] = {"tx-expense": _expense_transaction()}

        result = self._run_analyze({
            "period": "billing_period",
            "show_forecast": False,
            "show_calendar": False,
        })

        self.assertNotIn("status", result)
        self.assertEqual(len(result["expenses"]), 1)
        self.assertEqual(result["expenses"][0]["category_id"], TAG_ID)
        self.assertEqual(result["expenses"][0]["category_name"], "Food")
        self.assertEqual(result["summary"]["expense"]["actual"], 100)


if __name__ == "__main__":
    unittest.main()
