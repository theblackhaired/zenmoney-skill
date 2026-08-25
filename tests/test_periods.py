import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _periods():
    from zenmoney import periods

    return periods


class PeriodResolverBillingContractTests(unittest.TestCase):
    def test_billing_period_uses_half_open_internal_end_and_inclusive_public_end(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "billing_period", "period_offset": 0},
            today="2026-04-20",
            billing_start_day=20,
        )

        self.assertEqual(resolved["period"], "billing_period")
        self.assertEqual(resolved["period_offset"], 0)
        self.assertEqual(resolved["start_date"], "2026-04-20")
        self.assertEqual(resolved["end_exclusive"], "2026-05-20")
        self.assertEqual(resolved["start_date"], "2026-04-20")
        self.assertEqual(resolved["end_date"], "2026-05-19")

    def test_billing_period_before_start_day_resolves_to_previous_anchor(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "billing_period", "period_offset": 0},
            today="2026-04-19",
            billing_start_day=20,
        )

        self.assertEqual(resolved["start_date"], "2026-03-20")
        self.assertEqual(resolved["end_exclusive"], "2026-04-20")
        self.assertEqual(resolved["end_date"], "2026-04-19")

    def test_previous_billing_period_is_explicit_offset_not_budget_month_inference(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "billing_period", "period_offset": -1},
            today="2026-04-26",
            billing_start_day=20,
        )

        self.assertEqual(resolved["period_offset"], -1)
        self.assertEqual(resolved["start_date"], "2026-03-20")
        self.assertEqual(resolved["end_exclusive"], "2026-04-20")
        self.assertEqual(resolved["end_date"], "2026-04-19")

    def test_billing_start_day_29_rolls_over_non_leap_february_instead_of_clamping(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "billing_period", "period_offset": 0},
            today="2026-02-28",
            billing_start_day=29,
        )

        self.assertEqual(resolved["start_date"], "2026-01-29")
        self.assertEqual(resolved["end_exclusive"], "2026-03-01")
        self.assertEqual(resolved["end_date"], "2026-02-28")

    def test_billing_start_day_29_uses_real_leap_day_in_leap_february(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "billing_period", "period_offset": 0},
            today="2024-02-29",
            billing_start_day=29,
        )

        self.assertEqual(resolved["start_date"], "2024-02-29")
        self.assertEqual(resolved["end_exclusive"], "2024-03-29")
        self.assertEqual(resolved["end_date"], "2024-03-28")

    def test_billing_start_day_30_non_leap_february_boundary_is_march_first(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "billing_period", "period_offset": 0},
            today="2026-03-01",
            billing_start_day=30,
        )

        self.assertEqual(resolved["start_date"], "2026-03-01")
        self.assertEqual(resolved["end_exclusive"], "2026-03-30")
        self.assertEqual(resolved["end_date"], "2026-03-29")
        self.assertEqual(resolved["budget_month_anchor"], "2026-02-01")

    def test_billing_start_day_31_non_leap_february_boundary_is_march_first(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "billing_period", "period_offset": 0},
            today="2026-03-01",
            billing_start_day=31,
        )

        self.assertEqual(resolved["start_date"], "2026-03-01")
        self.assertEqual(resolved["end_exclusive"], "2026-03-31")
        self.assertEqual(resolved["end_date"], "2026-03-30")
        self.assertEqual(resolved["budget_month_anchor"], "2026-02-01")

    def test_billing_start_day_31_next_boundary_after_logical_february_is_march_thirty_first(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "billing_period", "period_offset": 0},
            today="2026-03-31",
            billing_start_day=31,
        )

        self.assertEqual(resolved["start_date"], "2026-03-31")
        self.assertEqual(resolved["end_exclusive"], "2026-05-01")
        self.assertEqual(resolved["end_date"], "2026-04-30")
        self.assertEqual(resolved["budget_month_anchor"], "2026-03-01")


class PeriodResolverCalendarContractTests(unittest.TestCase):
    def test_week_period_uses_explicit_monday_first_weekday(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "week", "period_offset": 0},
            today="2026-04-22",
            first_weekday=0,
        )

        self.assertEqual(resolved["start_date"], "2026-04-20")
        self.assertEqual(resolved["end_exclusive"], "2026-04-27")
        self.assertEqual(resolved["end_date"], "2026-04-26")

    def test_week_period_uses_explicit_sunday_first_weekday(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "week", "period_offset": 0},
            today="2026-04-22",
            first_weekday=6,
        )

        self.assertEqual(resolved["start_date"], "2026-04-19")
        self.assertEqual(resolved["end_exclusive"], "2026-04-26")
        self.assertEqual(resolved["end_date"], "2026-04-25")

    def test_previous_calendar_month_is_explicit_offset(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "month", "period_offset": -1},
            today="2026-04-22",
        )

        self.assertEqual(resolved["start_date"], "2026-03-01")
        self.assertEqual(resolved["end_exclusive"], "2026-04-01")
        self.assertEqual(resolved["end_date"], "2026-03-31")

    def test_current_calendar_year_resolves_to_whole_year(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"period": "year", "period_offset": 0},
            today="2026-04-22",
        )

        self.assertEqual(resolved["start_date"], "2026-01-01")
        self.assertEqual(resolved["end_exclusive"], "2027-01-01")
        self.assertEqual(resolved["end_date"], "2026-12-31")

    def test_custom_period_preserves_exact_inclusive_public_range(self):
        periods = _periods()

        resolved = periods.resolve_period(
            {"start_date": "2026-04-15", "end_date": "2026-04-20"},
            today="2026-04-22",
        )

        self.assertEqual(resolved["period"], "custom")
        self.assertEqual(resolved["period_offset"], 0)
        self.assertEqual(resolved["start_date"], "2026-04-15")
        self.assertEqual(resolved["end_exclusive"], "2026-04-21")
        self.assertEqual(resolved["end_date"], "2026-04-20")


class PeriodResolverValidationContractTests(unittest.TestCase):
    def test_mixed_period_and_custom_range_selectors_are_rejected(self):
        periods = _periods()

        with self.assertRaises(periods.InvalidPeriodSelectorError):
            periods.resolve_period(
                {
                    "period": "month",
                    "start_date": "2026-04-01",
                    "end_date": "2026-04-30",
                },
                today="2026-04-22",
            )

    def test_custom_range_requires_both_start_and_end_dates(self):
        periods = _periods()

        with self.assertRaises(periods.InvalidPeriodSelectorError):
            periods.resolve_period({"start_date": "2026-04-01"}, today="2026-04-22")

    def test_public_period_echoes_resolver_policy(self):
        periods = _periods()
        resolved = periods.resolve_period(
            {"period": "billing_period"},
            today="2026-03-01",
            billing_start_day=31,
        )

        self.assertEqual(
            periods.public_period(resolved),
            {
                "period": "billing_period",
                "offset": 0,
                "start_date": "2026-03-01",
                "end_date": "2026-03-30",
                "end_exclusive": "2026-03-31",
                "billing_start_day": 31,
            },
        )


if __name__ == "__main__":
    unittest.main()
