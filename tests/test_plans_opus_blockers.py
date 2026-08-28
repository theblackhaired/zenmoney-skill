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
from zenmoney.domain import ALL_CATEGORIES_ID
from zenmoney.errors import UnsupportedCalculationError


class PlansOpusBlockerTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["user"] = {"1": {"id": 1}}
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "rate": 1},
            "2": {"id": 2, "shortTitle": "USD", "rate": 90},
        }
        cache.CACHE.data["account"] = {
            "rub": {
                "id": "rub",
                "user": 1,
                "instrument": 1,
                "title": "RUB",
                "type": "checking",
                "balance": 1000,
                "inBalance": True,
                "savings": False,
                "archive": False,
            }
        }

    def _analyze(self, config_payload=None):
        payload = config_payload or {
            "budget_mode": "income_vs_expense",
            "plan_settings_override": [],
            "accounts_meta": {},
            "round_balance_to_integer": False,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with (
                patch.object(budget_tools, "_cfg_path", config_path),
                patch.object(validation, "_today", return_value="2026-04-15"),
                patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}),
            ):
                return json.loads(
                    asyncio.run(
                        tools.tool_analyze_budget_detailed(
                            {
                                "period": "billing_period",
                                "show_forecast": False,
                                "show_calendar": False,
                            }
                        )
                    )
                )

    def test_balance_to_balance_transfer_uses_exchange_difference_in_mixed_currency(self):
        cache.CACHE.data["account"]["usd"] = {
            "id": "usd",
            "user": 1,
            "instrument": 2,
            "title": "USD",
            "type": "checking",
            "balance": 10,
            "inBalance": True,
            "savings": False,
            "archive": False,
        }
        cache.CACHE.data["transaction"] = {
            "fx": {
                "id": "fx",
                "date": "2026-04-10",
                "outcomeAccount": "rub",
                "outcome": 90,
                "incomeAccount": "usd",
                "income": 1,
                "tag": [],
            }
        }

        result = self._analyze()

        self.assertEqual(result["summary"]["exchange_difference"]["currency"], "RUB")
        self.assertIn("fact", result["summary"]["exchange_difference"])

    def test_uncategorized_expense_budget_is_preserved_as_tree_row(self):
        budget = {
            "user": 1,
            "tag": None,
            "date": "2026-04-01",
            "income": 0,
            "incomeLock": False,
            "outcome": 500,
            "outcomeLock": True,
        }
        cache.CACHE.data["budget"] = {cache.Cache._budget_key(budget): budget}

        result = self._analyze()

        self.assertEqual(len(result["expenses"]), 1)
        self.assertEqual(result["expenses"][0]["category_id"], "uncategorized")
        self.assertEqual(result["expenses"][0]["category_name"], "Без категории")
        self.assertEqual(result["expenses"][0]["budget"], 500)
        self.assertEqual(result["summary"]["expense"]["category_budget"], 500)

    def test_income_totals_match_tree_and_report_all_separately(self):
        cache.CACHE.data["tag"] = {
            "salary": {"id": "salary", "title": "Salary", "parent": None}
        }
        budgets = [
            {
                "user": 1,
                "tag": "salary",
                "date": "2026-04-01",
                "income": 100,
                "incomeLock": True,
                "outcome": 0,
                "outcomeLock": False,
            },
            {
                "user": 1,
                "tag": ALL_CATEGORIES_ID,
                "date": "2026-04-01",
                "income": 999,
                "incomeLock": True,
                "outcome": 0,
                "outcomeLock": False,
            },
        ]
        cache.CACHE.data["budget"] = {
            cache.Cache._budget_key(budget): budget for budget in budgets
        }

        result = self._analyze()

        def sum_tree(nodes, field):
            return sum(
                node[field] + sum_tree(node.get("children", []), field)
                for node in nodes
            )

        summary = result["summary"]["income"]
        self.assertEqual(summary["explicit_budget"], sum_tree(result["income"], "explicit_budget"))
        self.assertEqual(summary["explicit_budget"], 100)
        self.assertEqual(summary["effective_budget"], 100)
        self.assertEqual(summary["aggregate_budget"], 999)
        self.assertEqual(summary["for_balance"], 100)

    def test_unknown_budget_category_fails_explicitly(self):
        budget = {
            "user": 1,
            "tag": "missing-tag",
            "date": "2026-04-01",
            "income": 100,
            "incomeLock": False,
            "outcome": 0,
            "outcomeLock": False,
        }
        cache.CACHE.data["budget"] = {cache.Cache._budget_key(budget): budget}

        with self.assertRaises(UnsupportedCalculationError) as caught:
            self._analyze()

        self.assertEqual(caught.exception.details["reason"], "unknown_budget_category")
        self.assertEqual(caught.exception.details["category_id"], "missing-tag")

    def test_unclassified_transaction_that_changes_perimeter_fails_explicitly(self):
        cache.CACHE.data["transaction"] = {
            "ambiguous": {
                "id": "ambiguous",
                "date": "2026-04-10",
                "outcomeAccount": "rub",
                "outcome": 40,
                "incomeAccount": "rub",
                "income": 100,
                "tag": [],
            }
        }

        with self.assertRaises(UnsupportedCalculationError) as caught:
            self._analyze()

        self.assertEqual(caught.exception.details["reason"], "unclassified_balance_change")
        self.assertEqual(caught.exception.details["transaction_id"], "ambiguous")
        self.assertEqual(caught.exception.details["perimeter_delta"], 60)

    def test_planned_transfer_preserves_reminder_marker_id(self):
        cache.CACHE.data["account"]["outside"] = {
            "id": "outside",
            "user": 1,
            "instrument": 1,
            "title": "Outside",
            "type": "checking",
            "balance": 0,
            "inBalance": False,
            "savings": True,
            "archive": False,
        }
        cache.CACHE.data["reminder"] = {
            "transfer-reminder": {
                "id": "transfer-reminder",
                "outcomeAccount": "outside",
                "outcome": 100,
                "incomeAccount": "rub",
                "income": 100,
                "tag": [],
            }
        }
        cache.CACHE.data["reminderMarker"] = {
            "marker-42": {
                "id": "marker-42",
                "reminder": "transfer-reminder",
                "date": "2026-04-20",
                "outcome": 100,
                "income": 100,
                "state": "planned",
            }
        }

        result = self._analyze()

        self.assertEqual(result["transfers"][0]["event"]["id"], "marker-42")

    def test_budget_tools_does_not_duplicate_transport_write_confirmation(self):
        self.assertFalse(hasattr(budget_tools, "_ensure_write_confirmed"))

    def test_single_currency_reports_exchange_difference_as_explicit_zero_term(self):
        result = self._analyze()

        self.assertEqual(result["summary"]["exchange_difference"], {
            "fact": 0,
            "budget": 0,
            "residue": 0,
            "currency": "RUB",
        })
        self.assertEqual(result["summary"]["balance_breakdown"]["exchange_difference_fact"], 0)
        self.assertIn("+ 0", result["summary"]["balance_breakdown"]["formula"])


if __name__ == "__main__":
    unittest.main()
