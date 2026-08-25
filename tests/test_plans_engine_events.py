import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.errors import InvalidArgumentError, UnsupportedCalculationError
from zenmoney.plans import (
    ALL_CATEGORIES_ID,
    UNCATEGORIZED_CATEGORY_ID,
    category_bucket,
    event_from_reminder_marker,
    event_from_transaction,
)


ACCOUNT_FROM = "11111111-1111-1111-1111-111111111111"
ACCOUNT_TO = "22222222-2222-2222-2222-222222222222"
CATEGORY_ID = "33333333-3333-3333-3333-333333333333"


def _account(account_id: str, *, in_balance: bool, archive: bool = False) -> dict:
    return {
        "id": account_id,
        "instrument": 1,
        "inBalance": in_balance,
        "archive": archive,
        "type": "checking",
        "subtype": "savings" if account_id == ACCOUNT_TO else None,
        "savings": account_id == ACCOUNT_TO,
        "creditLimit": 0,
    }


class PlanEventTests(unittest.TestCase):
    def setUp(self):
        self.accounts = {
            ACCOUNT_FROM: _account(ACCOUNT_FROM, in_balance=True, archive=True),
            ACCOUNT_TO: _account(ACCOUNT_TO, in_balance=False),
        }

    def test_transaction_event_preserves_both_sides_and_does_not_filter_archived_account(
        self,
    ):
        event = event_from_transaction(
            {
                "id": "tx-1",
                "date": "2026-08-20",
                "outcomeAccount": ACCOUNT_FROM,
                "outcome": 100,
                "outcomeInstrument": 1,
                "incomeAccount": ACCOUNT_TO,
                "income": 95,
                "incomeInstrument": 2,
                "tag": [CATEGORY_ID],
                "isForecast": False,
            },
            self.accounts,
        )

        self.assertEqual(event.source_id, "tx-1")
        self.assertEqual(event.source_type, "transaction")
        self.assertEqual(event.date, "2026-08-20")
        self.assertEqual(event.kind, "transfer")
        self.assertEqual(event.category_ids, (CATEGORY_ID,))
        self.assertIsNone(event.marker_state)
        self.assertFalse(event.is_forecast)
        self.assertEqual(event.outcome_side.account_id, ACCOUNT_FROM)
        self.assertEqual(event.outcome_side.amount, Decimal("100"))
        self.assertIsInstance(event.outcome_side.amount, Decimal)
        self.assertEqual(event.outcome_side.currency, 1)
        self.assertTrue(event.outcome_side.in_balance)
        self.assertTrue(event.outcome_side.archived)
        self.assertEqual(event.income_side.account_id, ACCOUNT_TO)
        self.assertEqual(event.income_side.amount, 95)
        self.assertEqual(event.income_side.currency, 2)
        self.assertFalse(event.income_side.in_balance)
        self.assertTrue(event.income_side.savings)

    def test_transaction_amounts_reject_bool_and_nonfinite_values(self):
        for invalid in (True, float("nan"), float("inf"), Decimal("-Infinity")):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(InvalidArgumentError, "finite non-negative"),
            ):
                event_from_transaction(
                    {
                        "id": "invalid-amount",
                        "date": "2026-08-20",
                        "outcomeAccount": ACCOUNT_FROM,
                        "outcome": invalid,
                        "incomeAccount": ACCOUNT_FROM,
                        "income": 0,
                    },
                    self.accounts,
                )

    def test_reminder_marker_event_preserves_marker_state_forecast_and_reminder_fallbacks(
        self,
    ):
        reminder = {
            "id": "reminder-1",
            "outcomeAccount": ACCOUNT_FROM,
            "incomeAccount": ACCOUNT_TO,
            "outcomeInstrument": 1,
            "incomeInstrument": 2,
            "tag": [CATEGORY_ID],
        }
        marker = {
            "id": "marker-1",
            "date": "2026-08-25",
            "outcome": 300,
            "income": 285,
            "state": "processed",
            "isForecast": True,
        }

        event = event_from_reminder_marker(reminder, marker, self.accounts)

        self.assertEqual(event.source_id, "marker-1")
        self.assertEqual(event.source_type, "reminder_marker")
        self.assertEqual(event.kind, "transfer")
        self.assertEqual(event.category_ids, (CATEGORY_ID,))
        self.assertEqual(event.marker_state, "processed")
        self.assertTrue(event.is_forecast)
        self.assertEqual(event.outcome_side.amount, 300)
        self.assertEqual(event.income_side.amount, 285)

    def test_reminder_marker_without_explicit_state_fails(self):
        with self.assertRaisesRegex(InvalidArgumentError, "state"):
            event_from_reminder_marker(
                {
                    "id": "reminder-1",
                    "outcomeAccount": ACCOUNT_FROM,
                    "outcomeInstrument": 1,
                },
                {
                    "id": "marker-without-state",
                    "date": "2026-08-25",
                    "outcome": 300,
                    "income": 0,
                },
                self.accounts,
            )

    def test_one_sided_events_are_classified_as_income_or_outcome(self):
        income = event_from_transaction(
            {
                "id": "income",
                "date": "2026-08-20",
                "outcomeAccount": ACCOUNT_FROM,
                "outcome": 0,
                "incomeAccount": ACCOUNT_FROM,
                "income": 10,
            },
            self.accounts,
        )
        outcome = event_from_transaction(
            {
                "id": "outcome",
                "date": "2026-08-20",
                "outcomeAccount": ACCOUNT_FROM,
                "outcome": 10,
                "incomeAccount": ACCOUNT_FROM,
                "income": 0,
            },
            self.accounts,
        )

        self.assertEqual(income.kind, "income")
        self.assertEqual(outcome.kind, "outcome")

    def test_balance_changing_event_with_unknown_account_fails_explicitly(self):
        with self.assertRaisesRegex(UnsupportedCalculationError, "unknown account"):
            event_from_transaction(
                {
                    "id": "broken",
                    "date": "2026-08-20",
                    "outcomeAccount": "missing",
                    "outcome": 10,
                    "income": 0,
                },
                self.accounts,
            )

    def test_event_without_a_monetary_side_fails_explicitly(self):
        with self.assertRaisesRegex(InvalidArgumentError, "monetary side"):
            event_from_transaction(
                {
                    "id": "empty",
                    "date": "2026-08-20",
                    "outcomeAccount": ACCOUNT_FROM,
                    "outcome": 0,
                    "incomeAccount": ACCOUNT_FROM,
                    "income": 0,
                },
                self.accounts,
            )


class CategoryBucketTests(unittest.TestCase):
    def setUp(self):
        self.categories = {
            CATEGORY_ID: {
                "id": CATEGORY_ID,
                "title": "Food",
                "parent": None,
            },
        }

    def test_preserves_known_all_and_uncategorized_as_distinct_buckets(self):
        known = category_bucket(CATEGORY_ID, self.categories)
        aggregate = category_bucket(ALL_CATEGORIES_ID, self.categories)
        uncategorized = category_bucket(None, self.categories)

        self.assertEqual(known.category_id, CATEGORY_ID)
        self.assertEqual(known.name, "Food")
        self.assertEqual(aggregate.category_id, ALL_CATEGORIES_ID)
        self.assertEqual(aggregate.name, "ALL (aggregate)")
        self.assertEqual(uncategorized.category_id, UNCATEGORIZED_CATEGORY_ID)
        self.assertEqual(uncategorized.name, "Uncategorized")
        self.assertEqual(
            len({known.category_id, aggregate.category_id, uncategorized.category_id}),
            3,
        )

    def test_unknown_category_fails_explicitly(self):
        with self.assertRaisesRegex(
            UnsupportedCalculationError, "missing from the synced category tree"
        ):
            category_bucket("missing", self.categories)


if __name__ == "__main__":
    unittest.main()
