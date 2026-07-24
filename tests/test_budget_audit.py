import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, config, domain, tools


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _budget_config() -> dict:
    return {
        "budget_mode_configured": True,
        "budget_mode": "income_vs_expense",
        "budget_modes": {
            "income_vs_expense": {
                "label": "Income vs Expense",
                "description": "Test config",
                "count_all_movements": False,
                "income": {
                    "from_savings": True,
                    "from_credit": False,
                    "from_debt": False,
                    "from_other_off_balance": False,
                },
                "expense": {
                    "to_savings": False,
                    "to_credit": True,
                    "to_debt": False,
                    "to_other_off_balance": False,
                },
            }
        },
        "accounts_meta": {},
        "round_balance_to_integer": True,
    }


class AnalyzeBudgetDetailedCurrencyAuditTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble", "rate": 1},
            "2": {"id": 2, "shortTitle": "USD", "title": "US Dollar", "rate": 90},
        }
        cache.CACHE.data["account"] = {
            "acct-rub": {
                "id": "acct-rub",
                "user": 1,
                "instrument": 1,
                "title": "RUB Card",
                "type": "ccard",
                "balance": 1000,
                "creditLimit": 0,
                "inBalance": True,
                "savings": False,
                "archive": False,
            },
            "acct-usd": {
                "id": "acct-usd",
                "user": 1,
                "instrument": 2,
                "title": "USD Card",
                "type": "ccard",
                "balance": 50,
                "creditLimit": 0,
                "inBalance": True,
                "savings": False,
                "archive": False,
            },
        }
        cache.CACHE.data["transaction"] = {
            "tx-rub-income": {
                "id": "tx-rub-income",
                "date": "2026-04-10",
                "income": 100,
                "outcome": 0,
                "incomeAccount": "acct-rub",
                "outcomeAccount": "acct-rub",
                "incomeInstrument": 1,
                "outcomeInstrument": 1,
                "tag": [],
            },
            "tx-usd-income": {
                "id": "tx-usd-income",
                "date": "2026-04-11",
                "income": 10,
                "outcome": 0,
                "incomeAccount": "acct-usd",
                "outcomeAccount": "acct-usd",
                "incomeInstrument": 2,
                "outcomeInstrument": 2,
                "tag": [],
            },
        }

    def test_rejects_mixed_currency_scalar_aggregates(self):
        config_payload = _budget_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(config_payload), encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path):
                with self.assertRaisesRegex(ValueError, "mixed currencies"):
                    asyncio.run(
                        tools.tool_analyze_budget_detailed(
                            {
                                "start_date": "2026-04-01",
                                "end_date": "2026-04-30",
                            }
                        )
                    )


class AnalyzeBudgetDetailedTransferAmountTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble", "rate": 1},
            "2": {"id": 2, "shortTitle": "USD", "title": "US Dollar", "rate": 90},
        }
        cache.CACHE.data["account"] = {
            "acct-rub": {
                "id": "acct-rub",
                "user": 1,
                "instrument": 1,
                "title": "RUB Card",
                "type": "ccard",
                "balance": 1000,
                "creditLimit": 0,
                "inBalance": True,
                "savings": False,
                "archive": False,
            },
            "acct-usd-savings": {
                "id": "acct-usd-savings",
                "user": 1,
                "instrument": 2,
                "title": "USD Savings",
                "type": "checking",
                "balance": 10,
                "creditLimit": 0,
                "inBalance": False,
                "savings": True,
                "archive": False,
            },
        }
        cache.CACHE.data["transaction"] = {
            "tx-transfer-in": {
                "id": "tx-transfer-in",
                "date": "2026-04-12",
                "income": 100,
                "outcome": 1,
                "incomeAccount": "acct-rub",
                "outcomeAccount": "acct-usd-savings",
                "incomeInstrument": 1,
                "outcomeInstrument": 2,
                "tag": [],
            }
        }
        cache.CACHE.data["reminder"] = {
            "rem-transfer-in": {
                "id": "rem-transfer-in",
                "user": 1,
                "changed": 1,
                "incomeInstrument": 1,
                "incomeAccount": "acct-rub",
                "income": 200,
                "outcomeInstrument": 2,
                "outcomeAccount": "acct-usd-savings",
                "outcome": 2,
                "tag": [],
                "merchant": None,
                "payee": "FX top-up",
                "comment": None,
                "interval": "month",
                "step": 1,
                "points": [20],
                "startDate": "2026-04-01",
                "endDate": None,
                "notify": True,
            }
        }
        cache.CACHE.data["reminderMarker"] = {
            "marker-transfer-in": {
                "id": "marker-transfer-in",
                "user": 1,
                "changed": 1,
                "incomeInstrument": 1,
                "incomeAccount": "acct-rub",
                "income": 200,
                "outcomeInstrument": 2,
                "outcomeAccount": "acct-usd-savings",
                "outcome": 2,
                "tag": [],
                "merchant": None,
                "payee": "FX top-up",
                "comment": None,
                "date": "2026-04-20",
                "reminder": "rem-transfer-in",
                "state": "planned",
                "notify": True,
            }
        }

    def test_uses_in_balance_side_amount_for_transfer_summary_and_forecast(self):
        config_payload = _budget_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(config_payload), encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path):
                result = json.loads(
                    asyncio.run(
                        tools.tool_analyze_budget_detailed(
                            {
                                "start_date": "2026-04-01",
                                "end_date": "2026-04-30",
                            }
                        )
                    )
                )

        self.assertEqual(result["summary"]["transfers"]["out"], 0)
        self.assertEqual(result["summary"]["transfers"]["in"], 300)
        self.assertEqual(result["summary"]["transfers"]["net"], -300)
        self.assertEqual(
            result["forecast"],
            [
                {"date": "2026-04-12", "balance": 1100, "operations_count": 1},
                {"date": "2026-04-20", "balance": 1300, "operations_count": 1},
            ],
        )


class DirtyCacheFixtureTests(unittest.TestCase):
    def _load_fixture_cache(self) -> cache.Cache:
        loaded_cache = cache.Cache()
        fixture_path = FIXTURES / "dirty_cache.json"
        with patch.object(config, "CACHE_PATH", fixture_path):
            loaded_cache.load()
        cache.CACHE = loaded_cache
        return loaded_cache

    def test_fixture_load_preserves_realistic_shapes(self):
        loaded_cache = self._load_fixture_cache()

        self.assertEqual(loaded_cache.server_timestamp, 424242)
        self.assertTrue(loaded_cache.get_account("acct-archived")["archive"])
        self.assertFalse(loaded_cache.get_account("acct-usd-savings")["inBalance"])
        self.assertEqual(
            sorted(marker["state"] for marker in loaded_cache.reminder_markers()),
            ["planned", "processed"],
        )
        self.assertEqual(domain._category_full_path("food-home"), "Food / Groceries")
        self.assertEqual(domain._category_full_path("travel-home"), "Travel / Groceries")

    def test_fixture_backed_read_tools_handle_archived_accounts(self):
        self._load_fixture_cache()

        visible_accounts = json.loads(asyncio.run(tools.tool_get_accounts({})))
        all_accounts = json.loads(asyncio.run(tools.tool_get_accounts({"include_archived": True})))

        self.assertEqual(
            {account["title"] for account in visible_accounts},
            {"Main Card", "USD Savings"},
        )
        self.assertEqual(
            {account["title"] for account in all_accounts},
            {"Main Card", "USD Savings", "Archived Cash"},
        )


if __name__ == "__main__":
    unittest.main()
