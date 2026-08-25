import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.analytics.balance_trend import INSIGHT_TYPES, render_balance_trend
from zenmoney.errors import ToolError


class BalanceTrendContractTests(unittest.TestCase):
    def test_no_selected_accounts_returns_empty_zero_trend(self):
        result = render_balance_trend(
            [{"date": "2026-08-01", "balance": "100"}],
            selected_account_ids=[],
            history=True,
            current_date="2026-08-03",
            current_balance="120",
        )

        self.assertEqual(result["points"], [])
        self.assertEqual(result["y_axis"], {"min": Decimal("0"), "max": Decimal("0")})
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["reason"], "NO_SELECTED_ACCOUNTS")
        self.assertNotIn("insight_type", result)

    def test_disabled_history_returns_empty_zero_trend(self):
        result = render_balance_trend(
            [{"date": "2026-08-01", "balance": "100"}],
            selected_account_ids=["card"],
            history=False,
            current_date="2026-08-03",
            current_balance="120",
        )

        self.assertEqual(result["points"], [])
        self.assertEqual(result["y_axis"], {"min": Decimal("0"), "max": Decimal("0")})
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["reason"], "HISTORY_DISABLED")
        self.assertNotIn("insight_type", result)

    def test_points_include_current_day_and_zero_in_axis_bounds(self):
        result = render_balance_trend(
            [
                {"date": "2026-08-02", "balance": "150"},
                {"date": "2026-08-01", "balance": "100"},
            ],
            selected_account_ids=["card"],
            history=True,
            current_date="2026-08-03",
            current_balance="50",
            currency_filter="USER",
            currency="RUB",
        )

        self.assertEqual(
            [point["date"] for point in result["points"]],
            ["2026-08-01", "2026-08-02", "2026-08-03"],
        )
        self.assertEqual(result["y_axis"], {"min": Decimal("0"), "max": Decimal("150")})
        self.assertEqual(result["points"][0]["normalized_y"], Decimal("0.6666666666666666666666666667"))
        self.assertEqual(result["points"][2]["diff_from_start"], Decimal("-50"))
        self.assertEqual(result["points"][2]["relative_diff"], Decimal("-0.5"))
        self.assertEqual(result["metadata"]["currency_filter"], {"mode": "USER", "currency": "RUB"})
        self.assertEqual(result["metadata"]["insight_types"], list(INSIGHT_TYPES))
        self.assertEqual(result["insight_type"], "DECREASED")

    def test_current_day_replaces_different_same_day_history_point(self):
        result = render_balance_trend(
            [{"date": "2026-08-03", "balance": "100"}],
            selected_account_ids=["card"],
            history=True,
            current_date="2026-08-03",
            current_balance="125",
            currency_filter="POPULAR",
            currency="USD",
        )

        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["points"][0]["balance"], Decimal("125"))
        self.assertEqual(result["metadata"]["currency_filter"], {"mode": "POPULAR", "currency": "USD"})

    def test_relative_diff_is_omitted_when_start_balance_is_not_positive(self):
        result = render_balance_trend(
            [
                {"date": "2026-08-01", "balance": "0"},
                {"date": "2026-08-02", "balance": "10"},
            ],
            selected_account_ids=["card"],
            history=True,
            current_date="2026-08-02",
        )

        self.assertNotIn("relative_diff", result["points"][0])
        self.assertNotIn("relative_diff", result["points"][1])
        self.assertEqual(result["insight_type"], "INCREASED")

    def test_invalid_currency_filter_and_float_balance_are_rejected(self):
        with self.assertRaises(ToolError) as caught:
            render_balance_trend(
                [],
                selected_account_ids=["card"],
                history=True,
                current_date="2026-08-01",
                currency_filter="ALL",
            )
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

        with self.assertRaises(ToolError) as caught:
            render_balance_trend(
                [{"date": "2026-08-01", "balance": 1.1}],
                selected_account_ids=["card"],
                history=True,
                current_date="2026-08-01",
            )
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
