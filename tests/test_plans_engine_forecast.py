import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.errors import InvalidArgumentError
from zenmoney.plans.forecast import build_daily_forecast


class DailyForecastContractTests(unittest.TestCase):
    def test_decimal_inputs_stay_decimal_through_distribution(self):
        result = build_daily_forecast(
            residue=Decimal("10.00"),
            planned_operations=[
                {
                    "date": "2026-04-02",
                    "amount": Decimal("1.00"),
                    "status": "planned",
                }
            ],
            start_date="2026-04-01",
            end_date="2026-04-03",
            cutoff_date="2026-04-01",
        )

        self.assertEqual(result["base_daily_amount"], Decimal("3.00"))
        self.assertEqual(
            [point["amount"] for point in result["points"]],
            [Decimal("3.00"), Decimal("4.00"), Decimal("3.00")],
        )
        self.assertTrue(all(isinstance(point["amount"], Decimal) for point in result["points"]))

    def test_all_money_inputs_normalize_to_decimal_including_numeric_strings(self):
        result = build_daily_forecast(
            residue="10.50",
            planned_operations=[
                {"date": "2026-04-02", "amount": "1.50", "status": "planned"}
            ],
            start_date="2026-04-01",
            end_date="2026-04-03",
            cutoff_date="2026-04-01",
        )

        self.assertEqual(result["residue"], Decimal("10.50"))
        self.assertEqual(result["planned_remaining"], Decimal("1.50"))
        self.assertEqual(result["base_daily_amount"], Decimal("3.00"))
        self.assertTrue(all(isinstance(point["amount"], Decimal) for point in result["points"]))

        finite_float = build_daily_forecast(
            residue=10.5,
            planned_operations=[
                {"date": "2026-04-01", "amount": 0.5, "status": "planned"}
            ],
            start_date="2026-04-01",
            end_date="2026-04-02",
            cutoff_date="2026-04-01",
        )
        self.assertEqual(finite_float["residue"], Decimal("10.5"))
        self.assertEqual(finite_float["planned_remaining"], Decimal("0.5"))

    def test_invalid_or_nonfinite_forecast_money_is_domain_error(self):
        invalid_values = [None, True, float("nan"), float("inf"), "bad"]
        for value in invalid_values:
            with self.subTest(residue=value):
                with self.assertRaises(InvalidArgumentError):
                    build_daily_forecast(
                        residue=value,
                        planned_operations=[],
                        start_date="2026-04-01",
                        end_date="2026-04-02",
                        cutoff_date="2026-04-01",
                    )
            with self.subTest(planned=value):
                with self.assertRaises(InvalidArgumentError):
                    build_daily_forecast(
                        residue=10,
                        planned_operations=[
                            {
                                "date": "2026-04-01",
                                "amount": value,
                                "status": "planned",
                            }
                        ],
                        start_date="2026-04-01",
                        end_date="2026-04-02",
                        cutoff_date="2026-04-01",
                    )

    def test_negative_exchange_difference_residue_clamps_to_zero(self):
        result = build_daily_forecast(
            residue=Decimal("-0.01"),
            planned_operations=[],
            start_date="2026-04-01",
            end_date="2026-04-02",
            cutoff_date="2026-04-01",
        )

        self.assertEqual(result["residue"], Decimal("0"))
        self.assertEqual(result["base_daily_amount"], Decimal("0"))
        self.assertEqual(
            [point["amount"] for point in result["points"]],
            [Decimal("0"), Decimal("0")],
        )

    def test_operations_before_cutoff_and_without_planned_state_are_not_remaining(self):
        result = build_daily_forecast(
            residue=60,
            planned_operations=[
                {"date": "2026-04-01", "amount": 30, "status": "planned"},
                {"date": "2026-04-02", "amount": 40},
            ],
            start_date="2026-04-01",
            end_date="2026-04-03",
            cutoff_date="2026-04-02",
        )

        self.assertEqual(result["planned_remaining"], 0)
        self.assertEqual([point["amount"] for point in result["points"]], [0, 30, 30])

    def test_past_days_are_zero_and_remaining_residue_is_distributed_from_cutoff(self):
        result = build_daily_forecast(
            residue=100,
            planned_operations=[
                {"date": "2026-04-04", "amount": 40, "status": "planned"},
                {"date": "2026-04-05", "amount": 50, "status": "processed"},
                {
                    "date": "2026-04-05",
                    "amount": 60,
                    "status": "planned",
                    "deleted": True,
                },
            ],
            start_date="2026-04-01",
            end_date="2026-04-05",
            cutoff_date="2026-04-03",
        )

        self.assertEqual(result["planned_remaining"], 40)
        self.assertEqual(result["remaining_days"], 3)
        self.assertEqual(result["base_daily_amount"], 20)
        self.assertEqual(
            [point["amount"] for point in result["points"]],
            [0, 0, 20, 60, 20],
        )
        self.assertEqual(
            [point["cumulative"] for point in result["points"]],
            [0, 0, 20, 80, 100],
        )

    def test_future_period_distributes_across_every_day_and_adds_each_plan_once(self):
        result = build_daily_forecast(
            residue=90,
            planned_operations=[
                {"date": "2026-05-02", "amount": 10, "state": "planned"},
                {"date": "2026-05-02", "amount": 20, "state": "planned"},
            ],
            start_date="2026-05-01",
            end_date="2026-05-03",
            cutoff_date="2026-04-20",
        )

        self.assertEqual(result["base_daily_amount"], 20)
        self.assertEqual(
            [point["planned_amount"] for point in result["points"]],
            [0, 30, 0],
        )
        self.assertEqual(
            [point["amount"] for point in result["points"]],
            [20, 50, 20],
        )
        self.assertEqual(result["points"][-1]["cumulative"], 90)

    def test_wholly_past_period_has_zero_forecast(self):
        result = build_daily_forecast(
            residue=70,
            planned_operations=[
                {"date": "2026-03-31", "amount": 70, "status": "planned"}
            ],
            start_date="2026-03-30",
            end_date="2026-03-31",
            cutoff_date="2026-04-01",
        )

        self.assertEqual(result["planned_remaining"], 0)
        self.assertEqual(result["remaining_days"], 0)
        self.assertEqual(result["base_daily_amount"], 0)
        self.assertEqual([point["amount"] for point in result["points"]], [0, 0])

    def test_final_point_is_clamped_to_full_residue(self):
        result = build_daily_forecast(
            residue=50,
            planned_operations=[
                {"date": "2026-04-01", "amount": 80, "status": "planned"}
            ],
            start_date="2026-04-01",
            end_date="2026-04-02",
            cutoff_date="2026-04-01",
        )

        self.assertEqual(result["base_daily_amount"], 0)
        self.assertEqual(result["points"][0]["planned_amount"], 80)
        self.assertEqual(result["points"][0]["amount"], 50)
        self.assertEqual(result["points"][-1]["cumulative"], 50)

    def test_leap_day_and_month_end_dates_are_not_skipped(self):
        result = build_daily_forecast(
            residue=30,
            planned_operations=[],
            start_date="2024-02-28",
            end_date="2024-03-01",
            cutoff_date="2024-02-28",
        )

        self.assertEqual(
            [point["date"] for point in result["points"]],
            ["2024-02-28", "2024-02-29", "2024-03-01"],
        )
        self.assertEqual([point["amount"] for point in result["points"]], [10, 10, 10])

        month_end = build_daily_forecast(
            residue=30,
            planned_operations=[],
            start_date="2026-01-29",
            end_date="2026-01-31",
            cutoff_date="2026-01-29",
        )
        self.assertEqual(
            [point["date"] for point in month_end["points"]],
            ["2026-01-29", "2026-01-30", "2026-01-31"],
        )

    def test_show_calendar_does_not_change_forecast_math(self):
        args = {
            "residue": 100,
            "planned_operations": [
                {"date": "2026-04-30", "amount": 40, "status": "planned"}
            ],
            "start_date": "2026-04-29",
            "end_date": "2026-05-01",
            "cutoff_date": "2026-04-29",
        }

        without_calendar = build_daily_forecast(**args, show_calendar=False)
        with_calendar = build_daily_forecast(**args, show_calendar=True)

        self.assertEqual(with_calendar, without_calendar)


if __name__ == "__main__":
    unittest.main()
