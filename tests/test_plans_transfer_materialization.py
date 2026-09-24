import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from zenmoney import cache, periods
from zenmoney.plans.context import build_context
from zenmoney.plans.render import render_analysis


def transfer(identifier, source, destination, outcome=1000, income=None, **extra):
    return {
        "id": identifier,
        "date": "2026-07-10",
        "outcomeAccount": source,
        "incomeAccount": destination,
        "outcome": outcome,
        "income": outcome if income is None else income,
        **extra,
    }


class PlansTransferMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = cache.Cache()
        self.snapshot.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "rate": 1},
            "2": {"id": 2, "shortTitle": "USD", "rate": 90},
        }
        self.snapshot.data["account"] = {
            name: {
                "id": name,
                "instrument": currency,
                "inBalance": included,
                "type": "checking",
                "balance": 0,
            }
            for name, currency, included in (
                ("main", 1, True),
                ("second", 1, True),
                ("outside", 1, False),
            )
        }

    def analyze(self):
        period = periods.resolve_period(
            {"period": "billing_period"},
            today="2026-07-15",
            billing_start_day=1,
        )
        context = build_context(
            args={
                "resolved_period": period,
                "show_calendar": True,
                "show_forecast": False,
            },
            cfg={
                "budget_mode": "income_vs_expense",
                "plan_settings_override": [],
                "difference_calculation_mode": "NONE",
            },
            cache=self.snapshot,
            budgets=[],
            today="2026-07-15",
        )
        return render_analysis(context)

    def add_marker(
        self, identifier, source, destination, amount=1000, state="processed"
    ):
        self.snapshot.data["reminder"][identifier] = transfer(
            identifier, source, destination, amount
        )
        self.snapshot.data["reminderMarker"][identifier] = {
            "id": identifier,
            "reminder": identifier,
            "date": "2026-07-10",
            "state": state,
        }

    def test_linked_boundary_markers_do_not_duplicate_actuals_even_across_periods(self):
        for source, destination, direction in (
            ("main", "outside", "out"),
            ("outside", "main", "in"),
        ):
            for date in ("2026-07-10", "2026-06-30", "2026-08-01"):
                with self.subTest(direction=direction, date=date):
                    self.add_marker("marker", source, destination)
                    self.snapshot.data["transaction"] = {
                        "actual": transfer(
                            "actual",
                            source,
                            destination,
                            reminderMarker="marker",
                            date=date,
                        )
                    }
                    result = self.analyze()
                    totals = result["summary"]["transfers"]
                    self.assertEqual(totals["remaining_out"], 0)
                    self.assertEqual(totals["remaining_in"], 0)
                    self.assertEqual(
                        totals["actual_" + direction],
                        1000 if date == "2026-07-10" else 0,
                    )

    def test_deleted_link_and_unlinked_boundary_marker_preserve_remaining_effect(self):
        self.add_marker("marker", "main", "outside")
        for extra in (
            {"deleted": True},
            {"isDeleted": True},
            {"state": "deleted"},
            {"status": "deleted"},
        ):
            with self.subTest(extra=extra):
                self.snapshot.data["transaction"] = {
                    "deleted": transfer(
                        "deleted",
                        "main",
                        "outside",
                        reminderMarker="marker",
                        **extra,
                    )
                }
                self.assertEqual(
                    self.analyze()["summary"]["transfers"]["remaining_out"], 1000
                )

    def test_internal_actual_residual_counts_only_the_same_currency_difference(self):
        for destination, outcome, income, expected_out, expected_in in (
            ("second", 1500, 1000, 500, 0),
            ("second", 1000, 1500, 0, 500),
            ("second", 1000, 1000, 0, 0),
            ("foreign", 1500, 1000, 0, 0),
        ):
            with self.subTest(destination=destination, outcome=outcome, income=income):
                if destination == "foreign":
                    self.snapshot.data["account"]["foreign"] = {
                        "id": "foreign",
                        "instrument": 2,
                        "inBalance": True,
                        "type": "checking",
                        "balance": 0,
                    }
                self.snapshot.data["transaction"] = {
                    "actual": transfer(
                        "actual",
                        "main",
                        destination,
                        outcome,
                        income,
                    )
                }
                result = self.analyze()
                totals = result["summary"]["transfers"]
                self.assertEqual(totals["actual_out"], expected_out)
                self.assertEqual(totals["actual_in"], expected_in)
                self.assertEqual(totals["remaining_net"], 0)
                if destination != "foreign":
                    self.assertEqual(
                        result["summary"]["balance"], expected_in - expected_out
                    )

    def test_combined_boundary_links_and_internal_fee_preserve_balance_identity(self):
        # Entirely synthetic amounts: income 8000, withdrawal 2000, fee 500.
        self.add_marker("incoming", "outside", "main", 8000)
        self.add_marker("outgoing", "main", "outside", 2000)
        self.snapshot.data["transaction"] = {
            "incoming": transfer(
                "income", "outside", "main", 8000, reminderMarker="incoming"
            ),
            "outgoing": transfer(
                "expense", "main", "outside", 2000, reminderMarker="outgoing"
            ),
            "fee": transfer("fee", "main", "second", 1500, 1000),
        }
        result = self.analyze()
        self.assertEqual(result["summary"]["balance"], 5500)
        self.assertEqual(result["summary"]["transfers"]["actual_net"], -5500)
        self.assertEqual(result["summary"]["transfers"]["remaining_net"], 0)
        self.assertEqual(
            result["summary"]["balance_breakdown"]["current_expense"], -5500
        )


if __name__ == "__main__":
    unittest.main()
