import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.analytics.comparison import build_income_outcome_comparison
from zenmoney.errors import ToolError


class IncomeOutcomeComparisonContractTests(unittest.TestCase):
    def test_whole_period_uses_totals_and_absolute_chart_weights(self):
        result = build_income_outcome_comparison(
            [
                {"key": "current", "title": "Current", "income": "200", "outcome": "-50"},
                {"key": "previous", "title": "Previous", "income": "0", "outcome": "25"},
            ],
            period_days=31,
            mode="AVERAGE_VALUES",
        )

        self.assertNotIn("mode", result)
        self.assertEqual(result["items"][0]["income"], Decimal("200"))
        self.assertEqual(result["items"][0]["outcome"], Decimal("-50"))
        self.assertEqual(result["items"][0]["residue"], Decimal("0"))
        self.assertEqual(result["items"][0]["percentage"], Decimal("-25.00"))
        self.assertEqual(result["items"][0]["chart"]["denominator"], Decimal("200"))
        self.assertEqual(result["items"][0]["chart"]["income_weight"], Decimal("1"))
        self.assertEqual(result["items"][0]["chart"]["outcome_weight"], Decimal("0.25"))
        self.assertEqual(result["items"][0]["chart"]["residue_weight"], Decimal("0"))
        self.assertEqual(result["items"][1]["percentage"], Decimal("100"))

    def test_residue_is_rendered_with_its_own_ratio_weight(self):
        result = build_income_outcome_comparison(
            [{"key": "current", "title": "Current", "income": "200", "outcome": "50", "residue": "-25"}],
            period_days=31,
        )

        self.assertEqual(result["items"][0]["residue"], Decimal("-25"))
        self.assertEqual(result["items"][0]["chart"]["residue_weight"], Decimal("0.125"))

    def test_average_values_are_exposed_but_not_calculated_from_raw_days(self):
        result = build_income_outcome_comparison(
            [
                {
                    "key": "quarter",
                    "title": "Quarter",
                    "income": Decimal("10"),
                    "outcome": Decimal("5"),
                }
            ],
            period_days=90,
            mode="AVERAGE_VALUES",
        )

        self.assertEqual(result["mode"], "AVERAGE_VALUES")
        self.assertEqual(result["available_modes"], ["WHOLE_PERIOD", "AVERAGE_VALUES"])
        self.assertEqual(result["items"][0]["income"], Decimal("10"))
        self.assertEqual(result["items"][0]["outcome"], Decimal("5"))
        self.assertEqual(result["items"][0]["percentage"], Decimal("50.0"))

    def test_average_values_reject_raw_period_division(self):
        with self.assertRaises(ToolError) as caught:
            build_income_outcome_comparison(
                [{"key": "quarter", "title": "Quarter", "income": "900", "outcome": "450", "days": 90}],
                period_days=90,
                mode="AVERAGE_VALUES",
            )

        self.assertEqual(caught.exception.code, "UNSUPPORTED_CALCULATION")

    def test_zero_income_and_outcome_percentage_is_zero(self):
        result = build_income_outcome_comparison(
            [{"key": "empty", "title": "Empty", "income": "0", "outcome": "0"}],
            period_days=30,
        )

        self.assertEqual(result["items"][0]["percentage"], Decimal("0"))
        self.assertEqual(result["items"][0]["chart"]["denominator"], Decimal("0"))
        self.assertEqual(result["items"][0]["chart"]["income_weight"], Decimal("0"))
        self.assertEqual(result["items"][0]["chart"]["outcome_weight"], Decimal("0"))
        self.assertEqual(result["items"][0]["chart"]["residue_weight"], Decimal("0"))

    def test_float_money_is_rejected_to_keep_decimal_math_explicit(self):
        with self.assertRaises(ToolError) as caught:
            build_income_outcome_comparison(
                [{"key": "bad", "title": "Bad", "income": 1.1, "outcome": 0}],
                period_days=30,
            )

        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
