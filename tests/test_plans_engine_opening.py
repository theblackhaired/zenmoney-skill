import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.plans.opening import resolve_opening_balance
from zenmoney.errors import InvalidArgumentError


def _native_balance(amount=Decimal("0")):
    return {
        "by_account": {"cash": {"instrument": 1, "amount": amount}},
        "by_instrument": {1: amount},
    }


class OpeningBalanceContractTests(unittest.TestCase):
    def test_historical_opening_reverses_every_account_side_after_start(self):
        accounts = [
            {
                "id": "archived",
                "instrument": 1,
                "balance": 100,
                "inBalance": True,
                "archive": True,
            },
            {
                "id": "off-balance",
                "instrument": 1,
                "balance": 900,
                "inBalance": False,
                "archive": False,
            },
        ]
        transactions = [
            {
                "id": "income",
                "date": "2026-04-10",
                "incomeAccount": "archived",
                "income": 30,
                "outcomeAccount": "off-balance",
                "outcome": 30,
            },
            {
                "id": "outcome",
                "date": "2026-04-11",
                "outcomeAccount": "archived",
                "outcome": 20,
                "incomeAccount": "off-balance",
                "income": 20,
            },
            {
                "id": "before-start",
                "date": "2026-03-31",
                "incomeAccount": "archived",
                "income": 500,
            },
            {
                "id": "deleted",
                "date": "2026-04-12",
                "incomeAccount": "archived",
                "income": 999,
                "deleted": True,
            },
        ]

        result = resolve_opening_balance(
            accounts=accounts,
            transactions=transactions,
            start_date="2026-04-01",
            today="2026-04-20",
            plan_balance_mode="BALANCE",
            plan_settings=[],
        )

        self.assertTrue(result["included"])
        self.assertEqual(result["source"], "reversed_current_balance")
        self.assertEqual(
            result["balance"]["by_account"],
            {"archived": {"instrument": 1, "amount": 90}},
        )
        self.assertEqual(result["balance"]["by_instrument"], {1: Decimal("90")})
        self.assertIsInstance(result["balance"]["by_account"]["archived"]["amount"], Decimal)

    def test_start_equal_to_today_uses_historical_reversal_without_recursion(self):
        calls = []
        result = resolve_opening_balance(
            accounts=[
                {
                    "id": "cash",
                    "instrument": 1,
                    "balance": 125,
                    "inBalance": True,
                }
            ],
            transactions=[
                {
                    "id": "today-income",
                    "date": "2026-04-20",
                    "incomeAccount": "cash",
                    "income": 25,
                }
            ],
            start_date="2026-04-20",
            today="2026-04-20",
            plan_balance_mode="BALANCE",
            plan_settings=[],
            previous_day_summary=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertEqual(result["source"], "reversed_current_balance")
        self.assertEqual(result["balance"]["by_instrument"], {1: Decimal("100")})
        self.assertEqual(calls, [])

    def test_native_buckets_keep_unlike_instruments_separate(self):
        result = resolve_opening_balance(
            accounts=[
                {"id": "rub", "instrument": 1, "balance": 100, "inBalance": True},
                {"id": "usd", "instrument": 2, "balance": 5, "inBalance": True},
                {"id": "outside", "instrument": 1, "balance": 999, "inBalance": False},
            ],
            transactions=[],
            start_date="2026-04-01",
            today="2026-04-20",
            plan_balance_mode="BALANCE",
            plan_settings=[],
        )

        self.assertEqual(
            result["balance"]["by_instrument"],
            {1: Decimal("100"), 2: Decimal("5")},
        )
        self.assertNotIn("outside", result["balance"]["by_account"])
        self.assertNotIn("total", result["balance"])

    def test_in_balance_account_without_native_instrument_is_rejected(self):
        with self.assertRaises(InvalidArgumentError) as caught:
            resolve_opening_balance(
                accounts=[{"id": "cash", "balance": 100, "inBalance": True}],
                transactions=[],
                start_date="2026-04-01",
                today="2026-04-20",
                plan_balance_mode="BALANCE",
                plan_settings=[],
            )

        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(caught.exception.details["account_id"], "cash")

    def test_in_balance_money_is_required_finite_non_bool_and_normalized(self):
        invalid_balances = [None, True, float("nan"), float("inf"), "not-money"]
        for value in invalid_balances:
            with self.subTest(value=value):
                account = {"id": "cash", "instrument": 1, "inBalance": True}
                if value is not None:
                    account["balance"] = value
                with self.assertRaises(InvalidArgumentError):
                    resolve_opening_balance(
                        accounts=[account],
                        transactions=[],
                        start_date="2026-04-01",
                        today="2026-04-20",
                        plan_balance_mode="BALANCE",
                        plan_settings=[],
                    )

        normalized = resolve_opening_balance(
            accounts=[
                {
                    "id": "cash",
                    "instrument": 1,
                    "balance": "10.25",
                    "inBalance": True,
                }
            ],
            transactions=[],
            start_date="2026-04-01",
            today="2026-04-20",
            plan_balance_mode="BALANCE",
            plan_settings=[],
        )
        self.assertEqual(
            normalized["balance"]["by_account"]["cash"]["amount"],
            Decimal("10.25"),
        )

    def test_transaction_money_is_required_finite_non_bool_and_nonnegative(self):
        invalid_values = [None, True, float("nan"), float("inf"), "bad", -1]
        for value in invalid_values:
            with self.subTest(value=value):
                transaction = {
                    "id": "bad-money",
                    "date": "2026-04-10",
                    "incomeAccount": "cash",
                    "income": value,
                    "outcome": 0,
                }
                with self.assertRaises(InvalidArgumentError):
                    resolve_opening_balance(
                        accounts=[
                            {
                                "id": "cash",
                                "instrument": 1,
                                "balance": 100,
                                "inBalance": True,
                            }
                        ],
                        transactions=[transaction],
                        start_date="2026-04-01",
                        today="2026-04-20",
                        plan_balance_mode="BALANCE",
                        plan_settings=[],
                    )

        with self.assertRaises(InvalidArgumentError):
            resolve_opening_balance(
                accounts=[
                    {"id": "cash", "instrument": 1, "balance": 100, "inBalance": True}
                ],
                transactions=[
                    {
                        "id": "missing-money",
                        "date": "2026-04-10",
                        "incomeAccount": "cash",
                        "outcome": 0,
                    }
                ],
                start_date="2026-04-01",
                today="2026-04-20",
                plan_balance_mode="BALANCE",
                plan_settings=[],
            )

    def test_nonzero_side_with_missing_or_unknown_account_is_rejected(self):
        cases = [
            (
                {"id": "missing", "date": "2026-04-10", "income": 10},
                "income",
                None,
            ),
            (
                {
                    "id": "unknown",
                    "date": "2026-04-10",
                    "outcome": 10,
                    "outcomeAccount": "not-synced",
                },
                "outcome",
                "not-synced",
            ),
        ]
        for transaction, side, account_id in cases:
            with self.subTest(transaction=transaction["id"]):
                with self.assertRaises(InvalidArgumentError) as caught:
                    resolve_opening_balance(
                        accounts=[
                            {
                                "id": "cash",
                                "instrument": 1,
                                "balance": 100,
                                "inBalance": True,
                            }
                        ],
                        transactions=[transaction],
                        start_date="2026-04-01",
                        today="2026-04-20",
                        plan_balance_mode="BALANCE",
                        plan_settings=[],
                    )

                self.assertEqual(caught.exception.details["transaction_id"], transaction["id"])
                self.assertEqual(caught.exception.details["side"], side)
                self.assertEqual(caught.exception.details["account_id"], account_id)

    def test_excluded_opening_is_zero_and_does_not_recurse(self):
        calls = []

        result = resolve_opening_balance(
            accounts=[],
            transactions=[],
            start_date="2026-05-01",
            today="2026-04-20",
            plan_balance_mode="EXCLUDE_OPENING_BALANCE",
            plan_settings=[],
            previous_day_summary=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertEqual(
            result,
            {
                "included": False,
                "source": "excluded",
                "balance": {"by_account": {}, "by_instrument": {}},
                "recursion_policy": None,
            },
        )
        self.assertEqual(calls, [])

    def test_future_opening_with_only_include_preserves_current_policy(self):
        calls = []
        expected_balance = {
            "by_account": {"cash": {"instrument": 1, "amount": 120}},
            "by_instrument": {1: 120},
        }

        def previous_day_summary(day, *, plan_balance_mode, plan_settings):
            calls.append((day, plan_balance_mode, plan_settings))
            return {"balance": expected_balance}

        result = resolve_opening_balance(
            accounts=[],
            transactions=[],
            start_date="2026-05-01",
            today="2026-04-20",
            plan_balance_mode="EXCLUDE_OPENING_BALANCE",
            plan_settings=["INCLUDE_OPENING_BALANCE"],
            previous_day_summary=previous_day_summary,
        )

        self.assertEqual(
            calls,
            [
                (
                    "2026-04-30",
                    "EXCLUDE_OPENING_BALANCE",
                    frozenset({"INCLUDE_OPENING_BALANCE"}),
                )
            ],
        )
        self.assertEqual(result["balance"], expected_balance)
        self.assertEqual(
            result["recursion_policy"],
            {
                "plan_balance_mode": "EXCLUDE_OPENING_BALANCE",
                "plan_settings": ["INCLUDE_OPENING_BALANCE"],
            },
        )

    def test_future_opening_with_transfer_exclusions_recurses_under_balance_policy(self):
        calls = []

        def previous_day_summary(day, *, plan_balance_mode, plan_settings):
            calls.append((day, plan_balance_mode, plan_settings))
            return {"balance": _native_balance(Decimal("450"))}

        result = resolve_opening_balance(
            accounts=[],
            transactions=[],
            start_date="2026-03-01",
            today="2026-02-28",
            plan_balance_mode="EXCLUDE_OPENING_BALANCE",
            plan_settings=[
                "INCLUDE_OPENING_BALANCE",
                "EXCLUDE_TRANSFER_FROM_SAVINGS",
            ],
            previous_day_summary=previous_day_summary,
        )

        self.assertEqual(calls, [("2026-02-28", "BALANCE", frozenset())])
        self.assertEqual(result["balance"], _native_balance(Decimal("450")))
        self.assertEqual(
            result["recursion_policy"],
            {"plan_balance_mode": "BALANCE", "plan_settings": []},
        )

    def test_future_balance_mode_preserves_transfer_exclusions(self):
        calls = []

        def previous_day_summary(day, *, plan_balance_mode, plan_settings):
            calls.append((day, plan_balance_mode, plan_settings))
            return {"balance": _native_balance(Decimal("10"))}

        result = resolve_opening_balance(
            accounts=[],
            transactions=[],
            start_date="2026-05-01",
            today="2026-04-20",
            plan_balance_mode="BALANCE",
            plan_settings=["EXCLUDE_TRANSFER_TO_LOANS"],
            previous_day_summary=previous_day_summary,
        )

        self.assertEqual(
            calls,
            [("2026-04-30", "BALANCE", frozenset({"EXCLUDE_TRANSFER_TO_LOANS"}))],
        )
        self.assertEqual(
            result["recursion_policy"],
            {
                "plan_balance_mode": "BALANCE",
                "plan_settings": ["EXCLUDE_TRANSFER_TO_LOANS"],
            },
        )

    def test_future_opening_without_callback_is_invalid_argument(self):
        with self.assertRaises(InvalidArgumentError) as caught:
            resolve_opening_balance(
                accounts=[],
                transactions=[],
                start_date="2026-05-01",
                today="2026-04-20",
                plan_balance_mode="BALANCE",
                plan_settings=[],
            )

        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_future_opening_also_rejects_missing_native_instrument(self):
        with self.assertRaises(InvalidArgumentError):
            resolve_opening_balance(
                accounts=[{"id": "cash", "balance": 100, "inBalance": True}],
                transactions=[],
                start_date="2026-05-01",
                today="2026-04-20",
                plan_balance_mode="BALANCE",
                plan_settings=[],
                previous_day_summary=lambda *args, **kwargs: {"balance": 100},
            )

    def test_future_opening_rejects_missing_native_balance(self):
        with self.assertRaises(InvalidArgumentError):
            resolve_opening_balance(
                accounts=[{"id": "cash", "instrument": 1, "inBalance": True}],
                transactions=[],
                start_date="2026-05-01",
                today="2026-04-20",
                plan_balance_mode="BALANCE",
                plan_settings=[],
                previous_day_summary=lambda *args, **kwargs: {
                    "balance": _native_balance(Decimal("100"))
                },
            )

    def test_future_callback_requires_uniform_native_holdings_shape(self):
        invalid_balances = [
            100,
            {},
            {"by_account": {}, "by_instrument": {1: 100}},
            {
                "by_account": {"cash": {"instrument": 1, "amount": 100}},
                "by_instrument": {1: 99},
            },
        ]
        for balance in invalid_balances:
            with self.subTest(balance=balance):
                with self.assertRaises(InvalidArgumentError):
                    resolve_opening_balance(
                        accounts=[],
                        transactions=[],
                        start_date="2026-05-01",
                        today="2026-04-20",
                        plan_balance_mode="BALANCE",
                        plan_settings=[],
                        previous_day_summary=lambda *args, **kwargs: {"balance": balance},
                    )

        normalized = resolve_opening_balance(
            accounts=[],
            transactions=[],
            start_date="2026-05-01",
            today="2026-04-20",
            plan_balance_mode="BALANCE",
            plan_settings=[],
            previous_day_summary=lambda *args, **kwargs: {
                "balance": _native_balance("12.50")
            },
        )
        self.assertEqual(normalized["balance"], _native_balance(Decimal("12.50")))

        with self.assertRaises(InvalidArgumentError):
            resolve_opening_balance(
                accounts=[],
                transactions=[],
                start_date="2026-05-01",
                today="2026-04-20",
                plan_balance_mode="BALANCE",
                plan_settings=[],
                previous_day_summary=lambda *args, **kwargs: SimpleNamespace(
                    balance=_native_balance(Decimal("12.50"))
                ),
            )


if __name__ == "__main__":
    unittest.main()
