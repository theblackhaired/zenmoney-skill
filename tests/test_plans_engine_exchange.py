import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.errors import ToolError
from zenmoney.plans.exchange import calculate_exchange_difference


class ExchangeDifferenceContractTests(unittest.TestCase):
    def setUp(self):
        self.rates = {
            ("USD", "2026-08-01"): Decimal("80"),
            ("USD", "2026-08-20"): Decimal("85"),
            ("USD", "2026-08-31"): Decimal("90"),
        }

    def convert(self, amount, source_instrument, target_instrument, on_date):
        amount = Decimal(amount)
        if source_instrument == target_instrument:
            return amount
        return amount * self.rates[(source_instrument, on_date)]

    def calculate(self, **overrides):
        arguments = {
            "opening_holdings": [
                {"instrument": "USD", "balance": "100", "date": "2026-08-01"},
            ],
            "target_holdings": [
                {"instrument": "USD", "balance": "100", "date": "2026-08-20"},
            ],
            "excluded_transfer_income": [],
            "excluded_transfer_expense": [],
            "income_facts": [],
            "expense_facts": [],
            "target_instrument": "RUB",
            "period_end_date": "2026-08-31",
            "exclude_opening_balance": False,
            "convert": self.convert,
        }
        arguments.update(overrides)
        return calculate_exchange_difference(**arguments)

    def test_rate_increase_is_positive_exchange_difference(self):
        result = self.calculate()

        self.assertEqual(result["id"], "EXCHANGE_DIFF")
        self.assertEqual(result["fact"], Decimal("500"))
        self.assertEqual(result["budget"], Decimal("1000"))
        self.assertEqual(result["residue"], Decimal("500"))
        self.assertEqual(result["components"]["openingBalance"], Decimal("8000"))
        self.assertEqual(result["components"]["targetBalance"], Decimal("8500"))

    def test_rate_decrease_is_negative_exchange_difference(self):
        self.rates[("USD", "2026-08-20")] = Decimal("75")
        self.rates[("USD", "2026-08-31")] = Decimal("70")

        result = self.calculate()

        self.assertEqual(result["fact"], Decimal("-500"))
        self.assertEqual(result["budget"], Decimal("-1000"))
        self.assertEqual(result["residue"], Decimal("-500"))

    def test_exclude_opening_removes_unchanged_pre_period_holding(self):
        result = self.calculate(exclude_opening_balance=True)

        self.assertEqual(result["fact"], Decimal("0"))
        self.assertEqual(result["budget"], Decimal("0"))
        self.assertEqual(result["residue"], Decimal("0"))
        self.assertEqual(result["components"]["openingBalance"], Decimal("0"))
        self.assertEqual(result["components"]["targetBalance"], Decimal("0"))

    def test_exclude_opening_values_only_period_native_delta(self):
        result = self.calculate(
            exclude_opening_balance=True,
            target_holdings=[
                {"instrument": "USD", "balance": "110", "date": "2026-08-20"},
            ],
        )

        self.assertEqual(result["fact"], Decimal("850"))
        self.assertEqual(result["budget"], Decimal("0"))
        self.assertEqual(result["residue"], Decimal("50"))

    def test_excluded_transfer_and_fact_signs_match_apk_formula(self):
        rub_holding = [{"instrument": "RUB", "balance": "0", "date": "2026-08-01"}]
        rub_target = [{"instrument": "RUB", "balance": "0", "date": "2026-08-20"}]
        result = self.calculate(
            opening_holdings=rub_holding,
            target_holdings=rub_target,
            excluded_transfer_income=[
                {"instrument": "RUB", "amount": "10", "date": "2026-08-10"},
            ],
            excluded_transfer_expense=[
                {"instrument": "RUB", "amount": "4", "date": "2026-08-11"},
            ],
            income_facts=[
                {"instrument": "RUB", "amount": "7", "date": "2026-08-12"},
            ],
            expense_facts=[
                {"instrument": "RUB", "amount": "2", "date": "2026-08-13"},
            ],
        )

        self.assertEqual(result["fact"], Decimal("-11"))
        self.assertEqual(result["components"]["excludedTransferIncome"], Decimal("10"))
        self.assertEqual(result["components"]["excludedTransferExpense"], Decimal("4"))
        self.assertEqual(result["components"]["incomeFacts"], Decimal("7"))
        self.assertEqual(result["components"]["expenseFacts"], Decimal("2"))

    def test_one_currency_balanced_movements_have_zero_exchange_difference(self):
        result = self.calculate(
            opening_holdings=[
                {"instrument": "RUB", "balance": "100", "date": "2026-08-01"},
            ],
            target_holdings=[
                {"instrument": "RUB", "balance": "120", "date": "2026-08-20"},
            ],
            income_facts=[
                {"instrument": "RUB", "amount": "20", "date": "2026-08-10"},
            ],
        )

        self.assertEqual(result["fact"], Decimal("0"))
        self.assertEqual(result["budget"], Decimal("0"))
        self.assertEqual(result["residue"], Decimal("0"))

    def test_budget_and_residue_are_period_end_revaluations(self):
        result = self.calculate(target_holdings=[
            {"instrument": "USD", "balance": "150", "date": "2026-08-20"},
        ])

        self.assertEqual(result["fact"], Decimal("4750"))
        self.assertEqual(result["budget"], Decimal("1000"))
        self.assertEqual(result["residue"], Decimal("750"))
        self.assertEqual(
            result["components"]["periodEndOpeningBalance"],
            Decimal("9000"),
        )
        self.assertEqual(
            result["components"]["periodEndTargetBalance"],
            Decimal("13500"),
        )

    def test_non_exchange_plan_totals_are_zero(self):
        result = self.calculate()

        for field in ("factExtra", "planned", "expired", "processed"):
            with self.subTest(field=field):
                self.assertEqual(result[field], Decimal("0"))

    def test_converter_receives_original_instrument_values_and_component_dates(self):
        calls = []

        def convert(amount, source_instrument, target_instrument, on_date):
            calls.append((amount, source_instrument, target_instrument, on_date))
            return amount

        self.calculate(
            opening_holdings=[
                {"instrument": 2, "balance": "1", "date": "2026-08-01"},
            ],
            target_holdings=[
                {"instrument": 2, "balance": "1", "date": "2026-08-20"},
            ],
            target_instrument=1,
            convert=convert,
        )

        self.assertIn((Decimal("1"), 2, 1, "2026-08-01"), calls)
        self.assertIn((Decimal("1"), 2, 1, "2026-08-20"), calls)
        self.assertIn((Decimal("1"), 2, 1, "2026-08-31"), calls)

    def test_missing_required_row_fields_fail_explicitly(self):
        cases = [
            ("opening_holdings", [{"balance": 1, "date": "2026-08-01"}]),
            ("opening_holdings", [{"instrument": "USD", "date": "2026-08-01"}]),
            ("opening_holdings", [{"instrument": "USD", "balance": 1}]),
            ("income_facts", [{"amount": 1, "date": "2026-08-01"}]),
            ("income_facts", [{"instrument": "USD", "date": "2026-08-01"}]),
            ("income_facts", [{"instrument": "USD", "amount": 1}]),
        ]
        for argument, rows in cases:
            with self.subTest(argument=argument, rows=rows):
                with self.assertRaises(ToolError) as caught:
                    self.calculate(**{argument: rows})
                self.assertEqual(caught.exception.code, "INVALID_EXCHANGE_DIFFERENCE_INPUT")

    def test_missing_top_level_instrument_or_date_fails_explicitly(self):
        for field, value in (("target_instrument", None), ("period_end_date", None)):
            with self.subTest(field=field):
                with self.assertRaises(ToolError) as caught:
                    self.calculate(**{field: value})
                self.assertEqual(caught.exception.code, "INVALID_EXCHANGE_DIFFERENCE_INPUT")


if __name__ == "__main__":
    unittest.main()
