import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, config, domain, tools, validation


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _budget_config() -> dict:
    return {
        "budget_mode": "income_vs_expense",
        "plan_settings_override": [],
        "difference_calculation_mode": "NONE",
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

    def test_mixed_currency_scalar_aggregates_use_exchange_difference(self):
        cache.CACHE.data["user"] = {"1": {"id": 1}}
        config_payload = _budget_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(config_payload), encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value="2026-04-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                result = json.loads(
                    asyncio.run(
                        tools.tool_analyze_budget_detailed({"period": "billing_period"})
                    )
                )

        self.assertNotIn("status", result)
        self.assertEqual(result["summary"]["exchange_difference"]["currency"], "RUB")
        self.assertIn("fact", result["summary"]["exchange_difference"])

    def test_missing_synced_plan_preferences_fail_without_complete_local_policy(self):
        for config_payload in ({"accounts_meta": {}}, {"budget_mode": "income_vs_expense"}):
            with self.subTest(config=config_payload), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.json"
                config_path.write_text(json.dumps(config_payload), encoding="utf-8")

                with patch.object(budget_tools, "_cfg_path", config_path), \
                     patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                    with self.assertRaisesRegex(ValueError, "preferences are unavailable"):
                        asyncio.run(tools.tool_analyze_budget_detailed({
                            "period": "billing_period",
                            "show_forecast": False,
                            "show_calendar": False,
                        }))

    def test_billing_rollover_reads_budget_from_logical_calendar_month(self):
        cache.CACHE.data["transaction"] = {}
        cache.CACHE.data["account"]["acct-usd"]["inBalance"] = False

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(_budget_config()), encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value="2026-03-01"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 31}):
                result = json.loads(
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

        self.assertEqual(result["summary"]["period"]["budget_months"], ["2026-02-01"])
        self.assertEqual(result["summary"]["period"]["billing_start_day"], 31)


class AnalyzeBudgetDetailedTransferAmountTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["user"] = {"1": {"id": 1}}
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

            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value="2026-04-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                result = json.loads(
                    asyncio.run(
                        tools.tool_analyze_budget_detailed(
                            {"period": "billing_period"}
                        )
                    )
                )

        self.assertEqual(result["summary"]["transfers"]["out"], 0)
        self.assertEqual(result["summary"]["transfers"]["in"], 300)
        self.assertEqual(result["summary"]["transfers"]["net"], -300)
        self.assertEqual(result["transfers"][0]["event"]["outcome_side"]["currency"], "USD")
        self.assertEqual(result["transfers"][0]["event"]["income_side"]["currency"], "RUB")
        self.assertEqual(result["forecast"][0]["date"], "2026-04-01")
        self.assertEqual(result["forecast"][-1]["date"], "2026-04-30")
        self.assertTrue(all(point["amount"] == 0 for point in result["forecast"]))

    def test_reads_synced_user_plan_mode_and_directed_exclusions(self):
        cache.CACHE.data["user"] = {
            "1": {
                "id": 1,
                "planBalanceMode": "excludeOpeningBalance",
                "planSettings": '["EXCLUDE_TRANSFER_FROM_SAVINGS"]',
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({"accounts_meta": {}}), encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value="2026-04-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                result = json.loads(
                    asyncio.run(tools.tool_analyze_budget_detailed({"period": "billing_period"}))
                )

        self.assertEqual(result["summary"]["budget_mode"], "income_vs_expense")
        self.assertEqual(result["summary"]["plan_balance_mode"], "EXCLUDE_OPENING_BALANCE")
        self.assertEqual(result["summary"]["transfers"]["in"], 0)
        self.assertEqual(result["summary"]["opening_balance"]["total"], 0)
        self.assertEqual(result["transfers"][0]["reason"], "EXCLUDE_TRANSFER_FROM_SAVINGS")

    def test_balance_mode_includes_opening_and_boundary_transfers_but_not_off_balance_spending(self):
        cache.CACHE.data["transaction"]["off-balance-expense"] = {
            "id": "off-balance-expense",
            "date": "2026-04-10",
            "income": 0,
            "outcome": 50,
            "incomeAccount": "acct-usd-savings",
            "outcomeAccount": "acct-usd-savings",
            "incomeInstrument": 2,
            "outcomeInstrument": 2,
            "tag": [],
        }
        config_payload = {
            "budget_mode": "balance_vs_expense",
            "plan_settings_override": ["EXCLUDE_TRANSFER_FROM_SAVINGS"],
            "accounts_meta": {},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(config_payload), encoding="utf-8")

            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value="2026-04-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                result = json.loads(asyncio.run(tools.tool_analyze_budget_detailed({
                    "period": "billing_period",
                    "show_forecast": False,
                    "show_calendar": False,
                })))

        self.assertEqual(result["summary"]["plan_balance_mode"], "BALANCE")
        self.assertEqual(result["summary"]["plan_settings"], [])
        self.assertEqual(result["summary"]["opening_balance"]["total"], 900)
        self.assertEqual(result["summary"]["transfers"]["in"], 300)
        self.assertEqual(result["summary"]["expense"]["actual"], 0)
        self.assertEqual(result["summary"]["balance"], 1200)

    def test_archived_in_balance_account_remains_in_plans_perimeter(self):
        cache.CACHE.data["account"] = {
            "acct-archived": {
                "id": "acct-archived",
                "user": 1,
                "instrument": 1,
                "title": "Archived RUB account",
                "type": "checking",
                "balance": 500,
                "creditLimit": 0,
                "inBalance": True,
                "savings": False,
                "archive": True,
            }
        }
        cache.CACHE.data["transaction"] = {
            "tx-archived-income": {
                "id": "tx-archived-income",
                "date": "2026-04-10",
                "income": 100,
                "outcome": 0,
                "incomeAccount": "acct-archived",
                "outcomeAccount": "acct-archived",
                "incomeInstrument": 1,
                "outcomeInstrument": 1,
                "tag": [],
            }
        }
        cache.CACHE.data["reminder"] = {}
        cache.CACHE.data["reminderMarker"] = {}
        cache.CACHE.data["user"] = {"1": {"id": 1}}

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps({
                    "budget_mode": "balance_vs_expense",
                    "plan_settings_override": [],
                    "accounts_meta": {},
                }),
                encoding="utf-8",
            )

            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value="2026-04-15"), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                result = json.loads(asyncio.run(tools.tool_analyze_budget_detailed({
                    "period": "billing_period",
                    "show_forecast": False,
                    "show_calendar": False,
                })))

        self.assertEqual(result["summary"]["income"]["actual"], 100)
        self.assertEqual(result["summary"]["opening_balance"]["total"], 400)
        self.assertEqual(result["summary"]["balance"], 500)


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
