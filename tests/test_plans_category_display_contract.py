import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.plans.models import CategoryBucket, PlanCategoryRow, PlanRowSide
from zenmoney.plans.render import _row_json
from zenmoney.plans.reserve import calculate_row


ZERO = PlanRowSide()


def _row(
    category_id: str,
    name: str,
    *,
    fact: int | float | str | Decimal = 0,
    planned: int | float | str | Decimal = 0,
    processed: int | float | str | Decimal = 0,
    budget: int | float | str | Decimal = 0,
    lock: bool = False,
    children: tuple[PlanCategoryRow, ...] = (),
) -> PlanCategoryRow:
    return PlanCategoryRow(
        category=CategoryBucket(category_id, name, None),
        income=ZERO,
        outcome=PlanRowSide(
            fact=Decimal(str(fact)),
            planned=Decimal(str(planned)),
            processed=Decimal(str(processed)),
            explicit_budget=Decimal(str(budget)),
            lock=lock,
        ),
        children=children,
    )


class PlansCategoryDisplayContractTests(unittest.TestCase):
    def test_leaf_exposes_plan_and_available_amount(self):
        calculated = calculate_row(_row("products", "Products", fact="1388.39", budget=10000))

        result = _row_json(calculated, "outcome")

        self.assertEqual(result["plan"], 10000)
        self.assertEqual(result["remaining"], Decimal("8611.61"))
        self.assertEqual(result["overspend"], 0)
        self.assertEqual(result["reserve_remaining"], Decimal("8611.61"))

    def test_parent_display_remaining_uses_total_plan_minus_total_fact(self):
        calculated = calculate_row(
            _row(
                "food",
                "Food",
                children=(
                    _row("delivery", "Delivery", fact=7386, planned=36000, processed=9000),
                    _row("products", "Products", fact=1388, budget=10000),
                    _row("fastfood", "Fast food", fact=1292),
                ),
            )
        )

        result = _row_json(calculated, "outcome")

        self.assertEqual(result["actual"], 10066)
        self.assertEqual(result["plan"], 55000)
        self.assertEqual(result["remaining"], 44934)
        self.assertEqual(result["overspend"], 0)
        self.assertEqual(result["reserve_remaining"], 46226)

    def test_parent_exposes_overspend_against_effective_plan(self):
        calculated = calculate_row(
            _row(
                "business",
                "Business",
                budget=504,
                children=(
                    _row("infrastructure", "Infrastructure", fact=24150, processed=24150),
                    _row("accounting", "Accounting", fact=5811, budget=5000),
                    _row("other", "Other", fact=868),
                ),
            )
        )

        result = _row_json(calculated, "outcome")

        self.assertEqual(result["actual"], 30829)
        self.assertEqual(result["plan"], 29654)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["overspend"], 1175)
        self.assertEqual(result["reserve_remaining"], 0)

    def test_locked_parent_display_plan_does_not_include_child_budget(self):
        calculated = calculate_row(
            _row(
                "locked-parent",
                "Locked parent",
                budget=150,
                lock=True,
                children=(
                    _row("child", "Child", fact=200, budget=200),
                ),
            )
        )

        result = _row_json(calculated, "outcome")

        self.assertEqual(result["actual"], 200)
        self.assertEqual(result["plan"], 150)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["overspend"], 50)
        self.assertEqual(result["reserve_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
