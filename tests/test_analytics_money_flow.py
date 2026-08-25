import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.analytics.money_flow import build_money_flow
from zenmoney.errors import InvalidArgumentError


RUB = {"1": {"id": 1, "shortTitle": "RUB"}}
ACCOUNTS = {
    "card": {
        "id": "card",
        "instrument": 1,
        "type": "checking",
        "inBalance": True,
    },
    "outside": {
        "id": "outside",
        "instrument": 1,
        "type": "checking",
        "inBalance": False,
    },
    "loan": {
        "id": "loan",
        "instrument": 1,
        "type": "ccard",
        "creditLimit": 100000,
        "inBalance": False,
    },
    "deposit": {
        "id": "deposit",
        "instrument": 1,
        "type": "checking",
        "savings": True,
        "inBalance": False,
    },
    "debt": {
        "id": "debt",
        "instrument": 1,
        "type": "debt",
        "inBalance": False,
    },
}


def tx(tx_id, income, outcome, income_account, outcome_account):
    return {
        "id": tx_id,
        "income": income,
        "outcome": outcome,
        "incomeAccount": income_account,
        "outcomeAccount": outcome_account,
        "incomeInstrument": 1,
        "outcomeInstrument": 1,
    }


class AnalyticsMoneyFlowTests(unittest.TestCase):
    def test_income_expense_and_result_weights(self):
        result = build_money_flow(
            [
                tx("salary", "1000", "0", "card", "card"),
                tx("food", "0", "900", "card", "card"),
            ],
            accounts=ACCOUNTS,
            instruments=RUB,
        )["currencies"]["RUB"]

        self.assertEqual(result["result"], "RESIDUE")
        self.assertEqual(result["income_total"], Decimal("1000"))
        self.assertEqual(result["outcome_total"], Decimal("900"))
        self.assertEqual(result["denominator"], Decimal("1000"))
        self.assertEqual(result["income"][0]["bucket"], "INCOMES")
        self.assertEqual(result["income"][0]["weight"], Decimal("1"))
        self.assertEqual(result["outcome"][0]["bucket"], "EXPENSES")
        self.assertEqual(result["outcome"][0]["weight"], Decimal("0.9"))
        self.assertEqual(result["result_amount"], Decimal("100"))
        self.assertEqual(_amount(result, "outcome", "DIFF"), Decimal("100"))

    def test_overspending_and_no_data_results(self):
        overspending = build_money_flow(
            [tx("food", "0", "10", "card", "card")],
            accounts=ACCOUNTS,
            instruments=RUB,
        )["currencies"]["RUB"]
        self.assertEqual(overspending["result"], "OVERSPENDING")
        self.assertEqual(overspending["result_amount"], Decimal("10"))
        self.assertEqual(_amount(overspending, "income", "DIFF"), Decimal("10"))

        no_data = build_money_flow([], accounts=ACCOUNTS, instruments=RUB)
        self.assertEqual(no_data, {"currencies": {}})

    def test_loan_credit_and_deposit_mapping(self):
        result = build_money_flow(
            [
                tx("borrow", "100", "100", "card", "loan"),
                tx("repay", "50", "50", "loan", "card"),
                tx("save", "25", "25", "deposit", "card"),
                tx("withdraw-save", "10", "10", "card", "deposit"),
            ],
            accounts=ACCOUNTS,
            instruments=RUB,
        )["currencies"]["RUB"]

        self.assertEqual(_amount(result, "income", "LOANS"), Decimal("100"))
        self.assertEqual(_amount(result, "income", "INCOME_TRANSFERS"), Decimal("10"))
        self.assertEqual(_amount(result, "outcome", "LOAN_PAYMENTS"), Decimal("50"))
        self.assertEqual(_amount(result, "outcome", "DEPOSITS"), Decimal("25"))

    def test_debt_and_boundary_transfer_mapping(self):
        result = build_money_flow(
            [
                tx("debt-in", "70", "70", "card", "debt"),
                tx("debt-out", "30", "30", "debt", "card"),
                tx("outside-in", "20", "20", "card", "outside"),
                tx("outside-out", "15", "15", "outside", "card"),
                tx("own", "999", "999", "card", "card"),
                tx("outside-neutral", "888", "888", "outside", "outside"),
            ],
            accounts=ACCOUNTS,
            instruments=RUB,
        )["currencies"]["RUB"]

        self.assertEqual(_amount(result, "income", "DEBTS"), Decimal("70"))
        self.assertEqual(_amount(result, "income", "INCOME_TRANSFERS"), Decimal("20"))
        self.assertEqual(_amount(result, "outcome", "DEBTS"), Decimal("30"))
        self.assertEqual(_amount(result, "outcome", "OUTCOME_TRANSFERS"), Decimal("15"))

    def test_boundary_amounts_do_not_create_fake_transfer_diff(self):
        result = build_money_flow(
            [
                tx("income-more", "105", "100", "card", "outside"),
                tx("outcome-more", "20", "30", "outside", "card"),
            ],
            accounts=ACCOUNTS,
            instruments=RUB,
        )["currencies"]["RUB"]

        self.assertEqual(_amount(result, "income", "INCOME_TRANSFERS"), Decimal("105"))
        self.assertEqual(_amount(result, "outcome", "OUTCOME_TRANSFERS"), Decimal("30"))
        self.assertEqual(_amount(result, "income", "DIFF"), Decimal("0"))
        self.assertEqual(_amount(result, "outcome", "DIFF"), Decimal("75"))

    def test_tiny_positive_weight_clamps_and_cumulative_stays_within_one(self):
        result = build_money_flow(
            [
                tx("salary", "1000", "0", "card", "card"),
                tx("tiny", "1", "1", "deposit", "card"),
                tx("large", "0", "990", "card", "card"),
            ],
            accounts=ACCOUNTS,
            instruments=RUB,
        )["currencies"]["RUB"]

        weights = [row["weight"] for row in result["outcome"]]
        self.assertIn(Decimal("0.01"), weights)
        self.assertEqual(sum(weights, Decimal(0)), Decimal("1.00"))

    def test_rejects_float_and_splits_mixed_currency_boundary_transfer(self):
        with self.assertRaises(InvalidArgumentError):
            build_money_flow(
                [tx("float", 1.1, 0, "card", "card")],
                accounts=ACCOUNTS,
                instruments=RUB,
            )

        mixed = build_money_flow(
            [
                {
                    **tx("fx", "90", "100", "card", "outside"),
                    "incomeInstrument": 1,
                    "outcomeInstrument": 2,
                }
            ],
            accounts=ACCOUNTS,
            instruments={**RUB, "2": {"id": 2, "shortTitle": "USD"}},
        )
        self.assertEqual(_amount(mixed["currencies"]["RUB"], "income", "INCOME_TRANSFERS"), Decimal("90"))
        self.assertNotIn("USD", mixed["currencies"])


def _amount(result, side, bucket):
    return {
        row["bucket"]: row["amount"]
        for row in result[side]
    }.get(bucket, Decimal(0))


if __name__ == "__main__":
    unittest.main()
