import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.analytics.category_difference import apply_category_difference
from zenmoney.errors import ToolError


class CategoryDifferenceContractTests(unittest.TestCase):
    def test_none_preserves_raw_income_and_outcome(self):
        result = apply_category_difference(
            income={"food": "30"},
            outcome={"food": "100"},
            categories={"food": {"id": "food", "showIncome": False, "showOutcome": True}},
            mode="NONE",
        )

        self.assertEqual(result["income"]["food"], Decimal("30"))
        self.assertEqual(result["outcome"]["food"], Decimal("100"))

    def test_refunds_subtract_opposite_operation_only_for_single_side_category(self):
        result = apply_category_difference(
            income={"food": "30", "salary": "100", "both": "20"},
            outcome={"food": "100", "salary": "10", "both": "5"},
            categories={
                "food": {"id": "food", "showIncome": False, "showOutcome": True},
                "salary": {"id": "salary", "showIncome": True, "showOutcome": False},
                "both": {"id": "both", "showIncome": True, "showOutcome": True},
            },
            mode="REFUNDS",
        )

        self.assertEqual(result["income"]["food"], Decimal("0"))
        self.assertEqual(result["outcome"]["food"], Decimal("70"))
        self.assertEqual(result["income"]["salary"], Decimal("90"))
        self.assertEqual(result["outcome"]["salary"], Decimal("0"))
        self.assertEqual(result["income"]["both"], Decimal("20"))
        self.assertEqual(result["outcome"]["both"], Decimal("5"))

    def test_refunds_rejects_missing_category_visibility(self):
        with self.assertRaises(ToolError) as caught:
            apply_category_difference(
                income={"food": "30"},
                outcome={"food": "100"},
                categories={"food": {"id": "food"}},
                mode="REFUNDS",
            )
        self.assertEqual(caught.exception.code, "UNSUPPORTED_CALCULATION")

    def test_income_outcome_mode_nets_after_parent_aggregation(self):
        result = apply_category_difference(
            income={"child": "100"},
            outcome={"parent": "70"},
            categories={
                "parent": {"id": "parent", "parent": None},
                "child": {"id": "child", "parent": "parent"},
            },
            mode="INCOME_OUTCOME_AND_REFUNDS",
        )

        self.assertEqual(result["totals"]["income"]["parent"], Decimal("30"))
        self.assertEqual(result["totals"]["outcome"]["parent"], Decimal("0"))
        self.assertEqual(result["income"]["child"], Decimal("100"))
        self.assertEqual(result["income"]["parent"], Decimal("-70"))
        self.assertEqual(result["outcome"]["parent"], Decimal("0"))


if __name__ == "__main__":
    unittest.main()
