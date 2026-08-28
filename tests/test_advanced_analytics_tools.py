import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import cache
from zenmoney.tools import TOOLS


ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"
FOOD_ID = "00000000-0000-0000-0000-000000000010"


class AdvancedAnalyticsToolsTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE._reset()
        cache.CACHE.data["instrument"] = {
            "1": {
                "id": 1,
                "shortTitle": "RUB",
                "title": "Russian ruble",
                "rate": 1,
            }
        }
        cache.CACHE.data["account"] = {
            ACCOUNT_ID: {
                "id": ACCOUNT_ID,
                "title": "Main",
                "instrument": 1,
                "balance": 150,
                "inBalance": True,
                "archive": False,
                "type": "checking",
            }
        }
        cache.CACHE.data["tag"] = {
            FOOD_ID: {
                "id": FOOD_ID,
                "title": "Food",
                "parent": None,
                "showIncome": False,
                "showOutcome": True,
            }
        }
        cache.CACHE.data["user"] = {"1": {"id": 1}}
        cache.CACHE.data["budget"] = {
            f"1:{FOOD_ID}:2026-07-01": {
                "user": 1,
                "tag": FOOD_ID,
                "date": "2026-07-01",
                "income": 0,
                "outcome": 50,
            }
        }
        cache.CACHE.data["transaction"] = {
            "june-income": self._tx("june-income", "2026-06-05", income=100),
            "june-food": self._tx("june-food", "2026-06-10", outcome=20),
            "july-income": self._tx("july-income", "2026-07-05", income=100),
            "july-food": self._tx("july-food", "2026-07-10", outcome=30, tag=[FOOD_ID]),
        }

    @staticmethod
    def _tx(tx_id, date, *, income=0, outcome=0, tag=None):
        return {
            "id": tx_id,
            "date": date,
            "income": income,
            "outcome": outcome,
            "incomeAccount": ACCOUNT_ID,
            "outcomeAccount": ACCOUNT_ID,
            "incomeInstrument": 1,
            "outcomeInstrument": 1,
            "tag": tag or [],
            "deleted": False,
        }

    @staticmethod
    def _call(name, arguments):
        return json.loads(asyncio.run(TOOLS[name](arguments)))

    def test_public_category_report_uses_period_budget_and_tree(self):
        result = self._call(
            "get_category_report",
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "direction": "OUTCOME",
                "group_by": "TAG",
                "budget_method": "BUDGET",
            },
        )

        self.assertEqual(result["period"]["start_date"], "2026-07-01")
        self.assertEqual(result["currency"], "RUB")
        self.assertEqual(result["difference_calculation_mode"], "REFUNDS")
        self.assertEqual(result["items"][0]["title"], "Food")
        self.assertEqual(result["items"][0]["amount"], 30)
        self.assertEqual(result["items"][0]["budget"], 50)
        self.assertEqual(result["items"][0]["budget_diff"], 20)

    def test_mixed_currency_report_uses_current_diff_rate_without_api_request(self):
        usd_account = "00000000-0000-0000-0000-000000000002"
        cache.CACHE.data["instrument"]["2"] = {
            "id": 2,
            "shortTitle": "USD",
            "title": "US dollar",
            "rate": 80,
        }
        cache.CACHE.data["account"][usd_account] = {
            **cache.CACHE.data["account"][ACCOUNT_ID],
            "id": usd_account,
            "title": "USD",
            "instrument": 2,
        }
        transaction = cache.CACHE.data["transaction"]["july-food"]
        transaction.update({
            "incomeAccount": usd_account,
            "outcomeAccount": usd_account,
            "incomeInstrument": 2,
            "outcomeInstrument": 2,
        })
        get_client = Mock()

        with patch("zenmoney.transport._get_client", get_client):
            result = self._call(
                "get_category_report",
                {
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-31",
                    "direction": "OUTCOME",
                    "group_by": "TAG",
                },
            )

        get_client.assert_not_called()
        self.assertEqual(result["items"][0]["amount"], 2400)
        self.assertEqual(
            result["metadata"]["currency_conversion"],
            {
                "policy": "current_synced_instrument_rate",
                "source": "v8_diff.instrument.rate",
                "historical_exchange_difference": "not_measurable_without_rate_history",
            },
        )

    def test_public_category_report_subtracts_refund_in_refunds_mode(self):
        cache.CACHE.data["transaction"]["july-refund"] = self._tx(
            "july-refund",
            "2026-07-12",
            income=10,
            tag=[FOOD_ID],
        )

        result = self._call(
            "get_category_report",
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "direction": "OUTCOME",
                "group_by": "TAG",
                "difference_calculation_mode": "REFUNDS",
            },
        )

        self.assertEqual(result["items"][0]["amount"], 20)

    def test_public_category_report_does_not_treat_cross_account_income_as_refund(self):
        other_account = "00000000-0000-0000-0000-000000000002"
        cache.CACHE.data["account"][other_account] = {
            **cache.CACHE.data["account"][ACCOUNT_ID],
            "id": other_account,
            "title": "Other",
        }
        refund = self._tx(
            "july-cross-account-income",
            "2026-07-12",
            income=10,
            tag=[FOOD_ID],
        )
        refund["incomeAccount"] = other_account
        cache.CACHE.data["transaction"][refund["id"]] = refund

        result = self._call(
            "get_category_report",
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "direction": "OUTCOME",
                "group_by": "TAG",
                "difference_calculation_mode": "REFUNDS",
            },
        )

        self.assertEqual(result["items"][0]["amount"], 30)

    def test_public_money_flow_keeps_overall_difference_on_opposite_side(self):
        result = self._call(
            "get_money_flow",
            {"start_date": "2026-07-01", "end_date": "2026-07-31"},
        )

        rub = result["currencies"]["RUB"]
        self.assertEqual(rub["result"], "RESIDUE")
        self.assertEqual(rub["result_amount"], 70)
        self.assertIn(
            {"bucket": "DIFF", "amount": 70, "weight": 0.7},
            rub["outcome"],
        )

    def test_public_comparison_builds_current_and_previous_periods(self):
        result = self._call(
            "get_income_outcome_comparison",
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "comparison_periods": 1,
            },
        )

        self.assertEqual(result["currency"], "RUB")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["income"], 100)
        self.assertEqual(result["items"][0]["outcome"], 30)
        self.assertEqual(result["items"][0]["residue"], 70)
        self.assertEqual(result["items"][1]["income"], 100)
        self.assertEqual(result["items"][1]["outcome"], 20)

    def test_public_balance_trend_reconstructs_period_boundaries(self):
        usd_account = "00000000-0000-0000-0000-000000000002"
        cache.CACHE.data["instrument"]["2"] = {
            "id": 2,
            "shortTitle": "USD",
            "title": "US dollar",
            "rate": 80,
        }
        cache.CACHE.data["account"][usd_account] = {
            **cache.CACHE.data["account"][ACCOUNT_ID],
            "id": usd_account,
            "title": "USD",
            "instrument": 2,
            "balance": 1,
        }
        get_client = Mock()

        with patch("zenmoney.transport._get_client", get_client):
            result = self._call(
                "get_balance_trend",
                {"start_date": "2026-07-01", "end_date": "2026-07-31"},
            )

        get_client.assert_not_called()
        self.assertEqual(result["metadata"]["currency_filter"]["currency"], "RUB")
        self.assertEqual(result["points"][0]["balance"], 160)
        self.assertEqual(result["points"][-1]["balance"], 230)
        self.assertEqual(
            result["metadata"]["currency_conversion"]["source"],
            "v8_diff.instrument.rate",
        )
        self.assertEqual(result["insight_type"], "INCREASED")


if __name__ == "__main__":
    unittest.main()
