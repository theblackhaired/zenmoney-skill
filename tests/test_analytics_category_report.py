import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.analytics.category_report import render_category_report
from zenmoney.errors import ToolError


class CategoryReportContractTests(unittest.TestCase):
    def test_category_budget_mode_builds_tree_and_parent_budget_from_children(self):
        result = render_category_report(
            [
                {"category_id": "groceries", "amount": "40"},
                {"category_id": "cafes", "amount": "10"},
            ],
            categories=[
                {"id": "food", "title": "Food", "parent": None, "icon": "fork", "color": "#f00"},
                {"id": "groceries", "title": "Groceries", "parent": "food", "icon": "cart", "color": "#0f0"},
                {"id": "cafes", "title": "Cafes", "parent": "food", "icon": "cup", "color": "#00f"},
            ],
            budgets={"groceries": "100", "cafes": "20", "food": "999"},
            budget_method="BUDGET",
        )

        food = result["items"][0]
        self.assertEqual(result["budget_method"], "BUDGET")
        self.assertEqual(food["key"], "food")
        self.assertEqual(food["full_path"], "Food")
        self.assertEqual(food["icon"], "fork")
        self.assertEqual(food["color"], "#f00")
        self.assertEqual(food["amount"], Decimal("50"))
        self.assertEqual(food["budget"], Decimal("120"))
        self.assertEqual(food["budget_method"], "BUDGET")
        self.assertEqual(food["budget_diff"], Decimal("70"))
        self.assertEqual(food["budget_percent"], Decimal("58.33333333333333333333333333"))
        self.assertEqual(food["children"][0]["full_path"], "Food / Cafes")
        self.assertEqual(food["children"][1]["full_path"], "Food / Groceries")

    def test_mean_uses_explicit_budget_first_then_complete_nonzero_comparison_average(self):
        result = render_category_report(
            [
                {"category_id": "groceries", "amount": "40"},
                {"category_id": "cafes", "amount": "10"},
                {"category_id": "fuel", "amount": "5"},
            ],
            categories=[
                {"id": "groceries", "title": "Groceries"},
                {"id": "cafes", "title": "Cafes"},
                {"id": "fuel", "title": "Fuel"},
            ],
            budgets={"groceries": "100"},
            comparison_periods=[
                {"groceries": "5", "cafes": "10", "fuel": "5"},
                {"groceries": "7", "cafes": "30", "fuel": "0"},
            ],
            budget_method="MEAN",
        )

        by_key = {item["key"]: item for item in result["items"]}
        self.assertEqual(by_key["groceries"]["budget"], Decimal("100"))
        self.assertEqual(by_key["groceries"]["budget_method"], "BUDGET")
        self.assertEqual(by_key["cafes"]["budget"], Decimal("20"))
        self.assertEqual(by_key["cafes"]["budget_method"], "MEAN")
        self.assertEqual(by_key["fuel"]["budget"], Decimal("0"))
        self.assertEqual(by_key["fuel"]["budget_method"], "MEAN")
        self.assertEqual(by_key["fuel"]["budget_percent"], Decimal("0"))

    def test_payee_forces_mean_even_when_budget_method_is_budget(self):
        result = render_category_report(
            [{"payee": "Store", "amount": "25"}],
            budgets={},
            comparison_periods=[{"Store": "10"}, {"Store": "30"}],
            group_by="PAYEE",
            budget_method="BUDGET",
        )

        self.assertEqual(result["group_by"], "PAYEE")
        self.assertEqual(result["budget_method"], "MEAN")
        self.assertEqual(result["items"][0]["budget"], Decimal("20"))
        self.assertEqual(result["items"][0]["budget_diff"], Decimal("-5"))
        self.assertEqual(result["items"][0]["budget_percent"], Decimal("-25.00"))

    def test_duplicate_category_key_and_float_money_fail_explicitly(self):
        with self.assertRaises(ToolError) as caught:
            render_category_report(
                [],
                categories=[
                    {"id": "food", "title": "Food"},
                    {"id": "food", "title": "Food Duplicate"},
                ],
            )
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

        with self.assertRaises(ToolError) as caught:
            render_category_report([{"category_id": "food", "amount": 1.1}])
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
