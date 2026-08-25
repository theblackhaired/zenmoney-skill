import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.plans import CategoryBucket, PlanCategoryRow, PlanRowSide, calculate_row


PARENT = CategoryBucket("parent", "Parent", None)
CHILD = CategoryBucket("child", "Child", "parent")


class PlanReserveTests(unittest.TestCase):
    def test_all_monetary_fields_are_decimal_and_reject_nonfinite_or_bool(self):
        side = PlanRowSide(
            fact=1,
            planned=2.5,
            processed=Decimal("3.75"),
            explicit_budget=4,
            effective_budget=5,
            residue=6,
        )

        for field in (
            "fact",
            "fact_with_refund",
            "planned",
            "processed",
            "explicit_budget",
            "effective_budget",
            "residue",
        ):
            self.assertIsInstance(getattr(side, field), Decimal)

        for invalid in (True, float("nan"), Decimal("Infinity")):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(ValueError, "finite number"),
            ):
                PlanRowSide(fact=invalid)

    def test_fact_with_refund_is_distinct_and_drives_own_residue(self):
        result = calculate_row(
            PlanCategoryRow(
                category=PARENT,
                income=PlanRowSide(
                    fact=100,
                    fact_with_refund=80,
                    explicit_budget=100,
                ),
                outcome=PlanRowSide(),
            )
        )

        self.assertEqual(result.income.fact, Decimal("100"))
        self.assertEqual(result.income.fact_with_refund, Decimal("80"))
        self.assertEqual(result.income.residue, Decimal("20"))

    def test_unlocked_and_locked_leaf_formulas_apply_to_both_sides(self):
        row = PlanCategoryRow(
            category=PARENT,
            income=PlanRowSide(
                fact=250, planned=100, processed=50, explicit_budget=1000
            ),
            outcome=PlanRowSide(
                fact=250,
                planned=100,
                processed=50,
                explicit_budget=1000,
                lock=True,
            ),
        )

        result = calculate_row(row)

        self.assertEqual(result.income.effective_budget, 1150)
        self.assertEqual(result.income.residue, 900)
        self.assertEqual(result.outcome.effective_budget, 1000)
        self.assertEqual(result.outcome.residue, 750)

    def test_near_zero_effective_budget_has_zero_residue(self):
        result = calculate_row(
            PlanCategoryRow(
                category=PARENT,
                income=PlanRowSide(fact=5, explicit_budget=0.009),
                outcome=PlanRowSide(),
            )
        )

        self.assertEqual(result.income.effective_budget, Decimal("0.009"))
        self.assertEqual(result.income.residue, Decimal(0))

    def test_residue_significance_threshold_is_exact_for_both_signs(self):
        cases = (
            ("0.009", "-1", "0"),
            ("-0.009", "-1", "0"),
            ("0.01", "0", "0.01"),
            ("-0.01", "-1", "0.99"),
        )
        for budget, fact, expected_residue in cases:
            with self.subTest(budget=budget):
                result = calculate_row(
                    PlanCategoryRow(
                        category=PARENT,
                        income=PlanRowSide(
                            fact=Decimal(fact),
                            explicit_budget=Decimal(budget),
                        ),
                        outcome=PlanRowSide(),
                    )
                )
                self.assertEqual(result.income.residue, Decimal(expected_residue))

    def test_unlocked_parent_rolls_activity_and_child_effective_budget(self):
        child = PlanCategoryRow(
            category=CHILD,
            income=PlanRowSide(
                fact=250, planned=100, processed=50, explicit_budget=200
            ),
            outcome=PlanRowSide(fact=80, planned=20, explicit_budget=100),
        )
        parent = PlanCategoryRow(
            category=PARENT,
            income=PlanRowSide(explicit_budget=500),
            outcome=PlanRowSide(explicit_budget=400),
            children=(child,),
        )

        result = calculate_row(parent)

        self.assertEqual(result.income.fact, 250)
        self.assertEqual(result.income.planned, 100)
        self.assertEqual(result.income.processed, 50)
        self.assertEqual(result.income.effective_budget, 850)
        self.assertEqual(result.income.residue, 600)
        self.assertEqual(result.outcome.fact, 80)
        self.assertEqual(result.outcome.planned, 20)
        self.assertEqual(result.outcome.effective_budget, 520)
        self.assertEqual(result.outcome.residue, 440)

    def test_locked_parent_rolls_activity_but_not_child_effective_budget(self):
        child = PlanCategoryRow(
            category=CHILD,
            income=PlanRowSide(
                fact=250, planned=100, processed=50, explicit_budget=200
            ),
            outcome=PlanRowSide(fact=80, planned=20, explicit_budget=100),
        )
        parent = PlanCategoryRow(
            category=PARENT,
            income=PlanRowSide(explicit_budget=500, lock=True),
            outcome=PlanRowSide(explicit_budget=400, lock=True),
            children=(child,),
        )

        result = calculate_row(parent)

        self.assertEqual(result.income.fact, 250)
        self.assertEqual(result.income.planned, 100)
        self.assertEqual(result.income.processed, 50)
        self.assertEqual(result.income.effective_budget, 500)
        self.assertEqual(result.income.residue, 250)
        self.assertEqual(result.outcome.fact, 80)
        self.assertEqual(result.outcome.planned, 20)
        self.assertEqual(result.outcome.effective_budget, 400)
        self.assertEqual(result.outcome.residue, 320)

    def test_subtree_residue_is_maximum_of_own_and_children_sum(self):
        children = (
            PlanCategoryRow(
                category=CategoryBucket("child-1", "Child 1", "parent"),
                income=PlanRowSide(explicit_budget=100),
                outcome=PlanRowSide(),
            ),
            PlanCategoryRow(
                category=CategoryBucket("child-2", "Child 2", "parent"),
                income=PlanRowSide(explicit_budget=200),
                outcome=PlanRowSide(),
            ),
        )
        result = calculate_row(
            PlanCategoryRow(
                category=PARENT,
                income=PlanRowSide(explicit_budget=100, lock=True),
                outcome=PlanRowSide(),
                children=children,
            )
        )

        self.assertEqual(result.income.residue, 300)

    def test_locked_child_keeps_own_explicit_budget_while_activity_reaches_parent(self):
        child = PlanCategoryRow(
            category=CHILD,
            income=PlanRowSide(
                fact=25,
                planned=10,
                processed=5,
                explicit_budget=100,
                lock=True,
            ),
            outcome=PlanRowSide(),
        )
        result = calculate_row(
            PlanCategoryRow(
                category=PARENT,
                income=PlanRowSide(),
                outcome=PlanRowSide(),
                children=(child,),
            )
        )

        self.assertTrue(result.children[0].income.lock)
        self.assertEqual(result.children[0].income.effective_budget, Decimal("100"))
        self.assertEqual(result.income.fact, Decimal("25"))
        self.assertEqual(result.income.planned, Decimal("10"))
        self.assertEqual(result.income.processed, Decimal("5"))
        self.assertEqual(result.income.effective_budget, Decimal("100"))
        self.assertEqual(result.income.residue, Decimal("75"))

    def test_three_level_tree_rolls_activity_through_every_ancestor(self):
        grandchild = PlanCategoryRow(
            category=CategoryBucket("grandchild", "Grandchild", "child"),
            income=PlanRowSide(fact=32, planned=8, processed=4),
            outcome=PlanRowSide(fact=16, planned=2, processed=1),
        )
        child = PlanCategoryRow(
            category=CHILD,
            income=PlanRowSide(),
            outcome=PlanRowSide(),
            children=(grandchild,),
        )
        result = calculate_row(
            PlanCategoryRow(
                category=PARENT,
                income=PlanRowSide(),
                outcome=PlanRowSide(),
                children=(child,),
            )
        )

        self.assertEqual(result.income.fact, Decimal("32"))
        self.assertEqual(result.income.fact_with_refund, Decimal("32"))
        self.assertEqual(result.income.planned, Decimal("8"))
        self.assertEqual(result.income.processed, Decimal("4"))
        self.assertEqual(result.outcome.fact, Decimal("16"))
        self.assertEqual(result.outcome.planned, Decimal("2"))
        self.assertEqual(result.outcome.processed, Decimal("1"))

    def test_three_level_budget_propagation_stops_at_locked_middle_parent(self):
        grandchild = PlanCategoryRow(
            category=CategoryBucket("grandchild", "Grandchild", "child"),
            income=PlanRowSide(explicit_budget=200),
            outcome=PlanRowSide(),
        )
        locked_child = PlanCategoryRow(
            category=CHILD,
            income=PlanRowSide(explicit_budget=100, lock=True),
            outcome=PlanRowSide(),
            children=(grandchild,),
        )
        result = calculate_row(
            PlanCategoryRow(
                category=PARENT,
                income=PlanRowSide(explicit_budget=50),
                outcome=PlanRowSide(),
                children=(locked_child,),
            )
        )

        self.assertEqual(
            result.children[0].children[0].income.effective_budget, Decimal("200")
        )
        self.assertEqual(result.children[0].income.effective_budget, Decimal("100"))
        self.assertEqual(result.income.effective_budget, Decimal("150"))

    def test_decimal_exchange_like_values_flow_through_reserve_without_float_loss(self):
        result = calculate_row(
            PlanCategoryRow(
                category=PARENT,
                income=PlanRowSide(
                    fact=Decimal("32.00"),
                    planned=Decimal("0.10"),
                    explicit_budget=Decimal("40.00"),
                ),
                outcome=PlanRowSide(),
            )
        )

        self.assertEqual(result.income.effective_budget, Decimal("40.10"))
        self.assertEqual(result.income.residue, Decimal("8.10"))
        self.assertIsInstance(result.income.residue, Decimal)


if __name__ == "__main__":
    unittest.main()
