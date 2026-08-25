import asyncio
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import cache, validation, write_tools
from zenmoney.errors import InvalidArgumentError, InvalidCategoryError, InvalidDateError, InvalidDateRangeError, InvalidMonthError


ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
ACCOUNT_KZT_ID = "22222222-2222-2222-2222-222222222222"
TAG_ID = "33333333-3333-3333-3333-333333333333"
MISSING_TAG_ID = "44444444-4444-4444-4444-444444444444"
TX_ID = "55555555-5555-5555-5555-555555555555"


class WriteContractValidationTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["instrument"] = {
            "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble"},
            "2": {"id": 2, "shortTitle": "KZT", "title": "Tenge"},
        }
        cache.CACHE.data["user"] = {"1": {"id": 1}}
        cache.CACHE.data["account"] = {
            ACCOUNT_ID: {
                "id": ACCOUNT_ID,
                "user": 1,
                "instrument": 1,
                "title": "RUB Card",
                "type": "ccard",
                "balance": 1000,
            },
            ACCOUNT_KZT_ID: {
                "id": ACCOUNT_KZT_ID,
                "user": 1,
                "instrument": 2,
                "title": "KZT Card",
                "type": "ccard",
                "balance": 500,
            },
        }
        cache.CACHE.data["tag"] = {
            TAG_ID: {"id": TAG_ID, "title": "Food", "parent": None},
        }
        cache.CACHE.data["transaction"] = {
            TX_ID: {
                "id": TX_ID,
                "user": 1,
                "outcome": 100,
                "income": 0,
                "outcomeAccount": ACCOUNT_ID,
                "incomeAccount": ACCOUNT_ID,
                "outcomeInstrument": 1,
                "incomeInstrument": 1,
                "date": "2026-07-20",
                "tag": [TAG_ID],
            }
        }

    def test_enums_reject_unknown_transaction_and_account_types(self):
        with self.assertRaisesRegex(InvalidArgumentError, "Invalid type"):
            validation.validate_tool_args("create_transaction", {
                "type": "refund",
                "amount": 100,
                "account_id": ACCOUNT_ID,
            })
        with self.assertRaisesRegex(InvalidArgumentError, "Invalid type"):
            validation.validate_tool_args("create_account", {
                "title": "Brokerage",
                "type": "brokerage",
                "currency_id": 1,
            })

    def test_semantic_date_and_month_validation_reject_calendar_invalid_values(self):
        with self.assertRaises(InvalidDateError):
            validation.validate_tool_args("create_transaction", {
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "date": "2026-02-30",
            })
        with self.assertRaises(InvalidMonthError):
            validation.validate_tool_args("get_budgets", {"month": "2026-13"})

    def test_finite_numeric_validation_rejects_nan_and_infinity(self):
        with self.assertRaisesRegex(InvalidArgumentError, "finite number"):
            validation.validate_tool_args("create_transaction", {
                "type": "expense",
                "amount": math.nan,
                "account_id": ACCOUNT_ID,
            })
        with self.assertRaisesRegex(InvalidArgumentError, "finite number"):
            validation.validate_tool_args("create_account", {
                "title": "Cash",
                "type": "cash",
                "currency_id": 1,
                "balance": math.inf,
            })

    def test_pagination_and_ranges_must_be_nonnegative_and_ordered(self):
        with self.assertRaisesRegex(InvalidArgumentError, "limit must be non-negative"):
            validation.validate_tool_args("get_transactions", {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "limit": -1,
            })
        with self.assertRaisesRegex(InvalidArgumentError, "offset must be non-negative"):
            validation.validate_tool_args("get_merchants", {"offset": -1})
        with self.assertRaisesRegex(InvalidDateRangeError, "start_date"):
            validation.validate_tool_args("get_transactions", {
                "start_date": "2026-07-31",
                "end_date": "2026-07-01",
            })
        with self.assertRaisesRegex(InvalidDateRangeError, "marker_from"):
            validation.validate_tool_args("get_reminders", {
                "marker_from": "2026-07-31",
                "marker_to": "2026-07-01",
            })

    def test_transaction_create_and_update_require_existing_categories(self):
        with self.assertRaisesRegex(InvalidCategoryError, "Category not found"):
            asyncio.run(write_tools.tool_create_transaction({
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "category_ids": [MISSING_TAG_ID],
            }))
        with self.assertRaisesRegex(InvalidCategoryError, "Category not found"):
            asyncio.run(write_tools.tool_update_transaction({
                "id": TX_ID,
                "category_ids": [MISSING_TAG_ID],
            }))

    def test_cross_currency_expense_income_is_rejected_before_write(self):
        writer = AsyncMock()
        with patch.object(write_tools, "_write_diff", writer):
            with self.assertRaisesRegex(InvalidArgumentError, "Cross-currency expense/income"):
                asyncio.run(write_tools.tool_create_transaction({
                    "type": "expense",
                    "amount": 100,
                    "account_id": ACCOUNT_ID,
                    "currency_id": 2,
                }))
            with self.assertRaisesRegex(InvalidArgumentError, "Cross-currency expense/income"):
                asyncio.run(write_tools.tool_create_transaction({
                    "type": "income",
                    "amount": 100,
                    "account_id": ACCOUNT_ID,
                    "currency_id": 2,
                }))

        writer.assert_not_awaited()

    def test_create_and_update_reminder_reject_unknown_intervals(self):
        with self.assertRaisesRegex(InvalidArgumentError, "Invalid interval"):
            validation.validate_tool_args("create_reminder", {
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "interval": "quarter",
            })
        with self.assertRaisesRegex(InvalidArgumentError, "Invalid interval"):
            validation.validate_tool_args("update_reminder", {
                "id": TX_ID,
                "interval": "quarter",
            })

    def test_recurring_step_must_be_positive(self):
        with self.assertRaisesRegex(InvalidArgumentError, "step must be positive"):
            validation.validate_tool_args("create_reminder", {
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "interval": "month",
                "step": 0,
            })
        with self.assertRaisesRegex(InvalidArgumentError, "step must be positive"):
            validation.validate_tool_args("update_reminder", {
                "id": TX_ID,
                "step": 0,
            })
        with self.assertRaisesRegex(InvalidArgumentError, "step must be an integer"):
            validation.validate_tool_args("create_reminder", {
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "interval": "month",
                "step": 1.5,
            })

    def test_numeric_arguments_reject_json_booleans(self):
        with self.assertRaisesRegex(InvalidArgumentError, "amount must be a finite number"):
            validation.validate_tool_args("create_transaction", {
                "type": "expense",
                "amount": True,
                "account_id": ACCOUNT_ID,
            })
        with self.assertRaisesRegex(InvalidArgumentError, "limit must be an integer"):
            validation.validate_tool_args("get_transactions", {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "limit": True,
            })
        with self.assertRaisesRegex(InvalidArgumentError, "currency_id must be an integer"):
            validation.validate_tool_args("create_account", {
                "title": "Card",
                "type": "ccard",
                "currency_id": True,
            })

    def test_reminder_points_must_be_real_int_list_and_less_than_step(self):
        with self.assertRaisesRegex(InvalidArgumentError, "points must be a list"):
            validation.validate_tool_args("create_reminder", {
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "interval": "month",
                "points": "1",
            })
        with self.assertRaisesRegex(InvalidArgumentError, "at least one recurrence offset"):
            validation.validate_tool_args("create_reminder", {
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "interval": "month",
                "points": [],
            })
        with self.assertRaisesRegex(InvalidArgumentError, r"points\[0\] must be an integer"):
            validation.validate_tool_args("create_reminder", {
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "interval": "month",
                "points": [True],
            })
        with self.assertRaisesRegex(InvalidArgumentError, "less than step"):
            validation.validate_tool_args("create_reminder", {
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "interval": "month",
                "step": 3,
                "points": [3],
            })
        with self.assertRaisesRegex(InvalidArgumentError, "less than step"):
            validation.validate_tool_args("update_reminder", {
                "id": TX_ID,
                "step": 3,
                "points": [3],
            })

        normalized = validation.validate_tool_args("create_reminder", {
            "type": "expense",
            "amount": 100,
            "account_id": ACCOUNT_ID,
            "interval": "month",
            "step": 3,
            "points": [0, 2],
        })
        self.assertEqual(normalized["points"], [0, 2])

    def test_get_analytics_group_by_must_be_supported_enum(self):
        with self.assertRaisesRegex(InvalidArgumentError, "Invalid group_by"):
            validation.validate_tool_args("get_analytics", {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "report": "outcome",
                "group_by": "currency",
            })

        normalized = validation.validate_tool_args("get_analytics", {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
        })
        self.assertEqual(normalized["group_by"], "category")

    def test_create_account_credit_limit_must_be_nonnegative(self):
        with self.assertRaisesRegex(InvalidArgumentError, "credit_limit must be non-negative"):
            validation.validate_tool_args("create_account", {
                "title": "Credit Card",
                "type": "ccard",
                "currency_id": 1,
                "credit_limit": -1,
            })

    def test_partial_updates_do_not_inject_category_ids_when_omitted(self):
        tx_args = validation.validate_tool_args("update_transaction", {
            "id": TX_ID,
            "amount": 200,
        })
        reminder_args = validation.validate_tool_args("update_reminder", {
            "id": TX_ID,
            "amount": 200,
        })

        self.assertNotIn("category_ids", tx_args)
        self.assertNotIn("category_ids", reminder_args)

    def test_update_transaction_without_category_ids_preserves_existing_tags(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(write_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(write_tools.tool_update_transaction({
                "id": TX_ID,
                "amount": 200,
            }))

        self.assertEqual(captured["diff"]["transaction"][0]["tag"], [TAG_ID])

    def test_create_transaction_uses_source_account_user(self):
        captured = {}
        cache.CACHE.data["user"] = {
            "1": {"id": 1},
            "99": {"id": 99},
        }
        cache.CACHE.data["account"][ACCOUNT_ID]["user"] = 99

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with patch.object(write_tools, "_write_diff", side_effect=fake_write_diff):
            asyncio.run(write_tools.tool_create_transaction({
                "type": "expense",
                "amount": 100,
                "account_id": ACCOUNT_ID,
                "date": "2026-07-24",
            }))

        self.assertEqual(captured["diff"]["transaction"][0]["user"], 99)


if __name__ == "__main__":
    unittest.main()
