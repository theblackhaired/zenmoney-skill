import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import cache, dispatch, tools


RUB_ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
KZT_ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
UNKNOWN_ACCOUNT_ID = "33333333-3333-3333-3333-333333333333"
ARCHIVED_ACCOUNT_ID = "44444444-4444-4444-4444-444444444444"
MISSING_INBALANCE_ACCOUNT_ID = "55555555-5555-5555-5555-555555555555"
FOOD_TAG_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SIDE_TAG_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SALARY_TAG_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
MERCHANT_STORE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
MISSING_ENTITY_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

LEGACY_OR_CAMEL_KEYS = {
    "type",
    "reportType",
    "metric",
    "groupBy",
    "total",
    "count",
    "grandTotalByCurrency",
    "totalsByCurrency",
    "scalarTotal",
    "currencyPolicy",
    "tagPolicy",
    "hasMixedCurrencies",
}


def _call_analytics(args: dict) -> dict:
    return json.loads(asyncio.run(tools.tool_get_analytics(args)))


def _run_analytics(args: dict) -> dict:
    mock_sync = AsyncMock(return_value=None)
    mock_close = AsyncMock(return_value=None)
    with patch.object(cache.CACHE, "load", lambda: None), \
         patch.object(dispatch, "_sync", mock_sync), \
         patch.object(dispatch, "_close_client", mock_close), \
         patch.object(tools, "_migrate_account_meta", lambda: None):
        return json.loads(asyncio.run(tools._run_tool("get_analytics", args)))


def _seed_reference_cache(transactions: dict[str, dict], merchants: dict[str, dict] | None = None) -> None:
    cache.CACHE = cache.Cache()
    cache.CACHE.data["instrument"] = {
        "1": {"id": 1, "shortTitle": "RUB", "title": "Russian Ruble"},
        "2": {"id": 2, "shortTitle": "KZT", "title": "Tenge"},
    }
    cache.CACHE.data["account"] = {
        RUB_ACCOUNT_ID: {
            "id": RUB_ACCOUNT_ID,
            "user": 1,
            "instrument": 1,
            "title": "RUB Card",
            "type": "ccard",
            "balance": 0,
            "inBalance": True,
            "archive": False,
        },
        KZT_ACCOUNT_ID: {
            "id": KZT_ACCOUNT_ID,
            "user": 1,
            "instrument": 2,
            "title": "KZT Card",
            "type": "ccard",
            "balance": 0,
            "inBalance": True,
            "archive": False,
        },
        UNKNOWN_ACCOUNT_ID: {
            "id": UNKNOWN_ACCOUNT_ID,
            "user": 1,
            "instrument": 1,
            "title": "Off-Balance Card",
            "type": "ccard",
            "balance": 0,
            "inBalance": False,
            "archive": False,
        },
        ARCHIVED_ACCOUNT_ID: {
            "id": ARCHIVED_ACCOUNT_ID,
            "user": 1,
            "instrument": 1,
            "title": "Archived Card",
            "type": "ccard",
            "balance": 0,
            "inBalance": True,
            "archive": True,
        },
        MISSING_INBALANCE_ACCOUNT_ID: {
            "id": MISSING_INBALANCE_ACCOUNT_ID,
            "user": 1,
            "instrument": 1,
            "title": "Legacy Card Without InBalance",
            "type": "ccard",
            "balance": 0,
            "archive": False,
        },
    }
    cache.CACHE.data["tag"] = {
        FOOD_TAG_ID: {"id": FOOD_TAG_ID, "title": "Food", "parent": None},
        SIDE_TAG_ID: {"id": SIDE_TAG_ID, "title": "Reimbursable", "parent": None},
        SALARY_TAG_ID: {"id": SALARY_TAG_ID, "title": "Salary", "parent": None},
    }
    cache.CACHE.data["merchant"] = merchants or {}
    cache.CACHE.data["transaction"] = transactions


def _tx(
    tx_id: str,
    *,
    income: float,
    outcome: float,
    account_id: str,
    instrument_id: int,
    date: str = "2026-07-05",
    tags: list[str] | None = None,
    merchant: str | None = None,
    payee: str | None = None,
    deleted: bool = False,
) -> dict:
    tx = {
        "id": tx_id,
        "date": date,
        "income": income,
        "outcome": outcome,
        "incomeAccount": account_id,
        "outcomeAccount": account_id,
        "incomeInstrument": instrument_id,
        "outcomeInstrument": instrument_id,
        "tag": tags or [],
    }
    if merchant is not None:
        tx["merchant"] = merchant
    if payee is not None:
        tx["payee"] = payee
    if deleted:
        tx["deleted"] = True
    return tx


def _money_flow_transactions() -> dict[str, dict]:
    return {
        "tx-expense-rub": _tx(
            "tx-expense-rub",
            income=0,
            outcome=100,
            account_id=RUB_ACCOUNT_ID,
            instrument_id=1,
            tags=[FOOD_TAG_ID],
        ),
        "tx-income-rub": _tx(
            "tx-income-rub",
            income=300,
            outcome=0,
            account_id=RUB_ACCOUNT_ID,
            instrument_id=1,
            tags=[SALARY_TAG_ID],
            date="2026-07-06",
        ),
        "tx-expense-kzt": _tx(
            "tx-expense-kzt",
            income=0,
            outcome=50,
            account_id=KZT_ACCOUNT_ID,
            instrument_id=2,
            tags=[FOOD_TAG_ID],
            date="2026-07-07",
        ),
        "tx-income-kzt": _tx(
            "tx-income-kzt",
            income=70,
            outcome=0,
            account_id=KZT_ACCOUNT_ID,
            instrument_id=2,
            tags=[SALARY_TAG_ID],
            date="2026-07-08",
        ),
    }


def _assert_no_legacy_or_camel_keys(test: unittest.TestCase, value) -> None:
    if isinstance(value, dict):
        forbidden = LEGACY_OR_CAMEL_KEYS & set(value)
        test.assertEqual(forbidden, set())
        for nested in value.values():
            _assert_no_legacy_or_camel_keys(test, nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_legacy_or_camel_keys(test, nested)


class GetAnalyticsSelectorContractTests(unittest.TestCase):
    def setUp(self):
        _seed_reference_cache(_money_flow_transactions())

    def test_no_report_selector_returns_invalid_argument(self):
        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "group_by": "category",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_type_selector_is_rejected_even_with_report(self):
        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "type": "expense",
            "group_by": "category",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_metric_selector_is_rejected_even_with_report(self):
        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "metric": "outcome",
            "group_by": "category",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_scalar_total_selector_is_rejected_even_with_report(self):
        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "scalar_total": True,
            "group_by": "category",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_turnover_report_returns_unsupported_calculation(self):
        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "turnover",
            "group_by": "category",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "UNSUPPORTED_CALCULATION")

    def test_empty_report_is_rejected(self):
        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "",
            "group_by": "category",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_empty_currency_mode_is_rejected(self):
        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "currency_mode": "",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")


class GetAnalyticsValueContractTests(unittest.TestCase):
    def setUp(self):
        _seed_reference_cache(_money_flow_transactions())

    def test_income_report_values_use_income_amounts(self):
        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "income",
            "group_by": "category",
        })

        self.assertEqual(result["totals"]["by_currency"]["RUB"]["value"], 300)

    def test_outcome_report_values_use_outcome_amounts(self):
        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "account_scope": "all",
        })

        self.assertEqual(result["totals"]["by_currency"]["KZT"]["value"], 50)

    def test_net_report_values_use_income_minus_outcome(self):
        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "net",
            "group_by": "category",
        })

        self.assertEqual(
            {currency: total["value"] for currency, total in result["totals"]["by_currency"].items()},
            {"KZT": 20, "RUB": 200},
        )

    def test_group_value_matches_selected_report(self):
        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "net",
            "group_by": "category",
        })

        rub_food = [
            group for group in result["groups"]
            if group["key"] == f"category:{FOOD_TAG_ID}" and group["currency"] == "RUB"
        ][0]
        self.assertEqual(rub_food["value"], -100)


class GetAnalyticsTotalsContractTests(unittest.TestCase):
    def test_split_total_mode_returns_by_currency_totals(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "currency_mode": "split",
        })

        self.assertEqual(
            {currency: total["value"] for currency, total in result["totals"]["by_currency"].items()},
            {"KZT": 50, "RUB": 100},
        )

    def test_scalar_total_mode_returns_single_currency_value(self):
        _seed_reference_cache({
            "tx-expense-rub": _tx(
                "tx-expense-rub",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "currency_mode": "scalar",
        })

        self.assertEqual(
            result["totals"],
            {"currency": "RUB", "income": 0, "outcome": 100, "value": 100, "transaction_count": 1},
        )

    def test_mixed_currency_scalar_total_returns_structured_error(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "currency_mode": "scalar",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "MIXED_CURRENCY")

    def test_empty_split_total_mode_returns_empty_by_currency(self):
        _seed_reference_cache({})

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "currency_mode": "split",
        })

        self.assertEqual(result["totals"], {"by_currency": {}})

    def test_empty_scalar_total_mode_returns_unknown_currency_zero(self):
        _seed_reference_cache({})

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "currency_mode": "scalar",
        })

        self.assertEqual(
            result["totals"],
            {"currency": None, "income": 0, "outcome": 0, "value": 0, "transaction_count": 0},
        )


class GetAnalyticsShapeContractTests(unittest.TestCase):
    def test_response_uses_snake_case_without_legacy_or_camel_aliases(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "account_scope": "all",
        })

        self.assertIn("transaction_count", result)
        self.assertIn("group_by", result)
        _assert_no_legacy_or_camel_keys(self, result)


class GetAnalyticsGroupingContractTests(unittest.TestCase):
    def test_category_group_key_uses_primary_category_id(self):
        _seed_reference_cache({
            "tx-expense-rub": _tx(
                "tx-expense-rub",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "account_scope": "all",
        })

        self.assertEqual(result["groups"][0]["key"], f"category:{FOOD_TAG_ID}")

    def test_merchant_and_payee_keys_do_not_collide(self):
        _seed_reference_cache(
            {
                "tx-merchant": _tx(
                    "tx-merchant",
                    income=0,
                    outcome=100,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    merchant=MERCHANT_STORE_ID,
                    payee="Store",
                ),
                "tx-payee": _tx(
                    "tx-payee",
                    income=0,
                    outcome=50,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    payee="Store",
                    date="2026-07-06",
                ),
            },
            merchants={MERCHANT_STORE_ID: {"id": MERCHANT_STORE_ID, "title": "Store"}},
        )

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "merchant",
        })

        self.assertEqual({group["key"] for group in result["groups"]}, {f"merchant:{MERCHANT_STORE_ID}", "payee:Store"})

    def test_groups_keep_currency_identity(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "account_scope": "all",
        })

        currencies = {(group["key"], group["currency"]) for group in result["groups"]}
        self.assertIn((f"category:{FOOD_TAG_ID}", "RUB"), currencies)
        self.assertIn((f"category:{FOOD_TAG_ID}", "KZT"), currencies)

    def test_groups_sort_by_currency_then_value_descending(self):
        _seed_reference_cache({
            "tx-food-small": _tx(
                "tx-food-small",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
            "tx-side-large": _tx(
                "tx-side-large",
                income=0,
                outcome=150,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[SIDE_TAG_ID],
            ),
            "tx-kzt": _tx(
                "tx-kzt",
                income=0,
                outcome=50,
                account_id=KZT_ACCOUNT_ID,
                instrument_id=2,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
        })

        self.assertEqual(
            [(group["currency"], group["key"]) for group in result["groups"]],
            [("KZT", f"category:{FOOD_TAG_ID}"), ("RUB", f"category:{SIDE_TAG_ID}"), ("RUB", f"category:{FOOD_TAG_ID}")],
        )

    def test_multi_tag_transaction_counts_once_under_primary_category(self):
        _seed_reference_cache({
            "tx-multi-tag": _tx(
                "tx-multi-tag",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID, SIDE_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
        })

        self.assertEqual([group["key"] for group in result["groups"]], [f"category:{FOOD_TAG_ID}"])


class GetAnalyticsTransactionContractTests(unittest.TestCase):
    def test_transaction_side_instrument_has_priority_over_account_currency(self):
        _seed_reference_cache({
            "tx-side-currency": {
                "id": "tx-side-currency",
                "date": "2026-07-05",
                "income": 0,
                "outcome": 100,
                "incomeAccount": RUB_ACCOUNT_ID,
                "outcomeAccount": RUB_ACCOUNT_ID,
                "incomeInstrument": 1,
                "outcomeInstrument": 2,
                "tag": [FOOD_TAG_ID],
            },
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
        })

        self.assertEqual(result["groups"][0]["currency"], "KZT")

    def test_unknown_transaction_currency_uses_unknown_bucket(self):
        _seed_reference_cache({
            "tx-unknown-currency": {
                "id": "tx-unknown-currency",
                "date": "2026-07-05",
                "income": 0,
                "outcome": 100,
                "incomeAccount": "missing-account",
                "outcomeAccount": "missing-account",
                "incomeInstrument": 999,
                "outcomeInstrument": 999,
                "tag": [FOOD_TAG_ID],
            },
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "account_scope": "all",
        })

        self.assertEqual(result["groups"][0]["currency"], "UNKNOWN")

    def test_transfers_and_deleted_transactions_are_excluded(self):
        _seed_reference_cache({
            "tx-expense": _tx(
                "tx-expense",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
            "tx-transfer": {
                "id": "tx-transfer",
                "date": "2026-07-06",
                "income": 50,
                "outcome": 50,
                "incomeAccount": KZT_ACCOUNT_ID,
                "outcomeAccount": RUB_ACCOUNT_ID,
                "incomeInstrument": 2,
                "outcomeInstrument": 1,
                "tag": [FOOD_TAG_ID],
            },
            "tx-deleted": _tx(
                "tx-deleted",
                income=0,
                outcome=200,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
                deleted=True,
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
        })

        self.assertEqual(result["transaction_count"], 1)


class GetAnalyticsFilterContractTests(unittest.TestCase):
    def test_account_scope_in_balance_includes_archived_and_excludes_off_balance_accounts(self):
        _seed_reference_cache({
            "tx-active": _tx(
                "tx-active",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
            "tx-off-balance": _tx(
                "tx-off-balance",
                income=0,
                outcome=30,
                account_id=UNKNOWN_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
            "tx-archived": _tx(
                "tx-archived",
                income=0,
                outcome=40,
                account_id=ARCHIVED_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "account",
            "account_scope": "in_balance",
        })

        self.assertEqual(
            [group["key"] for group in result["groups"]],
            [f"account:{RUB_ACCOUNT_ID}", f"account:{ARCHIVED_ACCOUNT_ID}"],
        )
        self.assertEqual(result["totals"]["by_currency"]["RUB"]["value"], 140)

    def test_default_account_scope_excludes_account_missing_in_balance_field(self):
        _seed_reference_cache({
            "tx-active": _tx(
                "tx-active",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
            "tx-missing-in-balance": _tx(
                "tx-missing-in-balance",
                income=0,
                outcome=30,
                account_id=MISSING_INBALANCE_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "account",
        })

        self.assertEqual([group["key"] for group in result["groups"]], [f"account:{RUB_ACCOUNT_ID}"])

    def test_in_balance_account_scope_excludes_account_missing_in_balance_field(self):
        _seed_reference_cache({
            "tx-active": _tx(
                "tx-active",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
            "tx-missing-in-balance": _tx(
                "tx-missing-in-balance",
                income=0,
                outcome=30,
                account_id=MISSING_INBALANCE_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "account",
            "account_scope": "in_balance",
        })

        self.assertEqual([group["key"] for group in result["groups"]], [f"account:{RUB_ACCOUNT_ID}"])

    def test_all_account_scope_includes_account_missing_in_balance_field(self):
        _seed_reference_cache({
            "tx-missing-in-balance": _tx(
                "tx-missing-in-balance",
                income=0,
                outcome=30,
                account_id=MISSING_INBALANCE_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "account",
            "account_scope": "all",
        })

        self.assertEqual([group["key"] for group in result["groups"]], [f"account:{MISSING_INBALANCE_ACCOUNT_ID}"])

    def test_account_scope_selected_filters_to_requested_account_ids(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "account",
            "account_scope": "selected",
            "account_ids": [KZT_ACCOUNT_ID],
        })

        self.assertEqual([group["key"] for group in result["groups"]], [f"account:{KZT_ACCOUNT_ID}"])

    def test_account_scope_all_includes_transactions_without_known_account(self):
        _seed_reference_cache({
            "tx-missing-account": {
                "id": "tx-missing-account",
                "date": "2026-07-05",
                "income": 0,
                "outcome": 25,
                "incomeAccount": "missing-account",
                "outcomeAccount": "missing-account",
                "incomeInstrument": 1,
                "outcomeInstrument": 1,
                "tag": [FOOD_TAG_ID],
            },
            "tx-known-account": _tx(
                "tx-known-account",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "account",
            "account_scope": "all",
        })

        self.assertIn("account:unknown", [group["key"] for group in result["groups"]])

    def test_response_echoes_filter_contract_and_filter_policies(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "account_scope": "selected",
            "account_ids": [RUB_ACCOUNT_ID],
            "category_scope": "selected",
            "category_ids": [FOOD_TAG_ID],
            "category_role": "primary",
            "merchant_scope": "selected",
            "payees": ["Store"],
        })

        self.assertEqual(
            result["filters"],
            {
                "account": {"scope": "selected", "ids": [RUB_ACCOUNT_ID]},
                "category": {"scope": "selected", "ids": [FOOD_TAG_ID], "role": "primary"},
                "merchant": {"scope": "selected", "ids": [], "payees": ["Store"]},
            },
        )
        self.assertEqual(result["policies"]["account_filter"], "report_side")
        self.assertEqual(result["policies"]["category_filter"], "exact_tag_id")
        self.assertEqual(result["policies"]["merchant_identity"], "merchant_then_payee_exact")

    def test_omitted_category_role_echoes_any(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
        })

        self.assertEqual(result["filters"]["category"]["role"], "any")

    def test_category_role_with_all_category_scope_is_rejected(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "category_scope": "all",
            "category_role": "primary",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_category_scope_selected_primary_matches_only_primary_tag(self):
        _seed_reference_cache({
            "tx-primary": _tx(
                "tx-primary",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID, SIDE_TAG_ID],
            ),
            "tx-additional": _tx(
                "tx-additional",
                income=0,
                outcome=50,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[SALARY_TAG_ID, FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "category_scope": "selected",
            "category_ids": [FOOD_TAG_ID],
            "category_role": "primary",
        })

        self.assertEqual(result["transaction_count"], 1)
        self.assertEqual(result["groups"][0]["value"], 100)

    def test_category_scope_selected_additional_matches_only_additional_tag(self):
        _seed_reference_cache({
            "tx-primary": _tx(
                "tx-primary",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID, SIDE_TAG_ID],
            ),
            "tx-additional": _tx(
                "tx-additional",
                income=0,
                outcome=50,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[SALARY_TAG_ID, FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "category_scope": "selected",
            "category_ids": [FOOD_TAG_ID],
            "category_role": "additional",
        })

        self.assertEqual(result["transaction_count"], 1)
        self.assertEqual(result["groups"][0]["key"], f"category:{SALARY_TAG_ID}")

    def test_category_role_any_matches_primary_and_additional_tags(self):
        _seed_reference_cache({
            "tx-primary": _tx(
                "tx-primary",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID, SIDE_TAG_ID],
            ),
            "tx-additional": _tx(
                "tx-additional",
                income=0,
                outcome=50,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[SALARY_TAG_ID, FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "category_scope": "selected",
            "category_ids": [FOOD_TAG_ID],
            "category_role": "any",
        })

        self.assertEqual(result["transaction_count"], 2)

    def test_category_scope_all_groups_empty_tags_as_uncategorized(self):
        _seed_reference_cache({
            "tx-uncategorized": _tx(
                "tx-uncategorized",
                income=0,
                outcome=20,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
            ),
            "tx-categorized": _tx(
                "tx-categorized",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "category_scope": "all",
        })

        self.assertIn("category:uncategorized", [group["key"] for group in result["groups"]])

    def test_unknown_observed_tag_preserves_category_key_and_unknown_name(self):
        _seed_reference_cache({
            "tx-unknown-tag": _tx(
                "tx-unknown-tag",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                tags=[MISSING_ENTITY_ID],
            ),
            "tx-empty-tags": _tx(
                "tx-empty-tags",
                income=0,
                outcome=50,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "category_scope": "all",
        })

        by_key = {group["key"]: group for group in result["groups"]}
        self.assertEqual(by_key[f"category:{MISSING_ENTITY_ID}"]["name"], "Unknown Category")
        self.assertEqual(by_key["category:uncategorized"]["name"], "Uncategorized")

    def test_merchant_scope_selected_merchant_uses_merchant_precedence(self):
        _seed_reference_cache(
            {
                "tx-merchant": _tx(
                    "tx-merchant",
                    income=0,
                    outcome=100,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    merchant=MERCHANT_STORE_ID,
                    payee="Store",
                ),
                "tx-payee": _tx(
                    "tx-payee",
                    income=0,
                    outcome=50,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    payee="Store",
                ),
            },
            merchants={MERCHANT_STORE_ID: {"id": MERCHANT_STORE_ID, "title": "Store"}},
        )

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "merchant",
            "merchant_scope": "selected",
            "merchant_ids": [MERCHANT_STORE_ID],
        })

        self.assertEqual([group["key"] for group in result["groups"]], [f"merchant:{MERCHANT_STORE_ID}"])

    def test_merchant_scope_selected_payee_matches_payee_fallback_only(self):
        _seed_reference_cache(
            {
                "tx-merchant": _tx(
                    "tx-merchant",
                    income=0,
                    outcome=100,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    merchant=MERCHANT_STORE_ID,
                    payee="Store",
                ),
                "tx-payee": _tx(
                    "tx-payee",
                    income=0,
                    outcome=50,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    payee="Store",
                ),
            },
            merchants={MERCHANT_STORE_ID: {"id": MERCHANT_STORE_ID, "title": "Store"}},
        )

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "merchant",
            "merchant_scope": "selected",
            "payees": ["Store"],
        })

        self.assertEqual([group["key"] for group in result["groups"]], ["payee:Store"])

    def test_merchant_scope_selected_combines_merchant_ids_and_payees_with_or(self):
        _seed_reference_cache(
            {
                "tx-merchant": _tx(
                    "tx-merchant",
                    income=0,
                    outcome=100,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    merchant=MERCHANT_STORE_ID,
                    payee="Store",
                ),
                "tx-payee": _tx(
                    "tx-payee",
                    income=0,
                    outcome=50,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    payee="Cafe",
                ),
            },
            merchants={MERCHANT_STORE_ID: {"id": MERCHANT_STORE_ID, "title": "Store"}},
        )

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "merchant",
            "merchant_scope": "selected",
            "merchant_ids": [MERCHANT_STORE_ID],
            "payees": ["Cafe"],
        })

        self.assertEqual({group["key"] for group in result["groups"]}, {f"merchant:{MERCHANT_STORE_ID}", "payee:Cafe"})

    def test_merchant_scope_payee_filter_is_case_sensitive(self):
        _seed_reference_cache({
            "tx-payee": _tx(
                "tx-payee",
                income=0,
                outcome=50,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                payee="Store",
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "merchant",
            "merchant_scope": "selected",
            "payees": ["store"],
        })

        self.assertEqual(result["transaction_count"], 0)

    def test_merchant_payee_fallbacks_are_nfc_normalized_for_grouping_and_filtering(self):
        composed = "Caf\u00e9"
        decomposed = "Cafe\u0301"
        _seed_reference_cache({
            "tx-composed": _tx(
                "tx-composed",
                income=0,
                outcome=100,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                payee=composed,
            ),
            "tx-decomposed": _tx(
                "tx-decomposed",
                income=0,
                outcome=50,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                payee=decomposed,
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "merchant",
            "merchant_scope": "selected",
            "payees": [composed],
        })

        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["key"], f"payee:{composed}")
        self.assertEqual(result["groups"][0]["name"], composed)
        self.assertEqual(result["groups"][0]["value"], 150)

    def test_merchant_scope_all_groups_no_merchant_and_no_payee_as_unknown(self):
        _seed_reference_cache({
            "tx-unknown-place": _tx(
                "tx-unknown-place",
                income=0,
                outcome=20,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
            ),
            "tx-payee": _tx(
                "tx-payee",
                income=0,
                outcome=50,
                account_id=RUB_ACCOUNT_ID,
                instrument_id=1,
                payee="Store",
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "merchant",
            "merchant_scope": "all",
        })

        self.assertIn("merchant:unknown", [group["key"] for group in result["groups"]])

    def test_scope_combinations_are_applied_with_and_semantics(self):
        _seed_reference_cache(
            {
                "tx-match": _tx(
                    "tx-match",
                    income=0,
                    outcome=100,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    tags=[FOOD_TAG_ID, SIDE_TAG_ID],
                    payee="Store",
                ),
                "tx-wrong-category": _tx(
                    "tx-wrong-category",
                    income=0,
                    outcome=50,
                    account_id=RUB_ACCOUNT_ID,
                    instrument_id=1,
                    tags=[FOOD_TAG_ID],
                    payee="Store",
                ),
                "tx-wrong-account": _tx(
                    "tx-wrong-account",
                    income=0,
                    outcome=70,
                    account_id=KZT_ACCOUNT_ID,
                    instrument_id=2,
                    tags=[FOOD_TAG_ID, SIDE_TAG_ID],
                    payee="Store",
                ),
            },
        )

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "account_scope": "selected",
            "account_ids": [RUB_ACCOUNT_ID],
            "category_scope": "selected",
            "category_ids": [SIDE_TAG_ID],
            "category_role": "additional",
            "merchant_scope": "selected",
            "payees": ["Store"],
        })

        self.assertEqual(result["transaction_count"], 1)
        self.assertEqual(result["totals"]["by_currency"]["RUB"]["value"], 100)

    def test_filter_with_no_matches_returns_empty_split_result(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "account_scope": "selected",
            "account_ids": [RUB_ACCOUNT_ID],
            "merchant_scope": "selected",
            "payees": ["No such payee"],
        })

        self.assertEqual(result["transaction_count"], 0)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["totals"], {"by_currency": {}})

    def test_selected_account_scope_rejects_unknown_account_id(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "account_scope": "selected",
            "account_ids": [MISSING_ENTITY_ID],
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "ENTITY_NOT_FOUND")

    def test_selected_account_scope_allows_archived_account_id(self):
        _seed_reference_cache({
            "tx-archived": _tx(
                "tx-archived",
                income=0,
                outcome=40,
                account_id=ARCHIVED_ACCOUNT_ID,
                instrument_id=1,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "account",
            "account_scope": "selected",
            "account_ids": [ARCHIVED_ACCOUNT_ID],
        })

        self.assertEqual([group["key"] for group in result["groups"]], [f"account:{ARCHIVED_ACCOUNT_ID}"])

    def test_selected_category_scope_rejects_unknown_category_id(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "category_scope": "selected",
            "category_ids": [MISSING_ENTITY_ID],
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "ENTITY_NOT_FOUND")

    def test_selected_merchant_scope_rejects_empty_selection(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "merchant_scope": "selected",
            "merchant_ids": [],
            "payees": [],
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_account_scope_rejects_invalid_mode(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "account_scope": "everything",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_category_scope_rejects_invalid_match_type(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "category_scope": "selected",
            "category_ids": [FOOD_TAG_ID],
            "category_role": "secondary",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_selected_account_scope_ids_must_be_a_list(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "account_scope": "selected",
            "account_ids": RUB_ACCOUNT_ID,
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_selected_account_scope_rejects_non_uuid_account_id(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "account_scope": "selected",
            "account_ids": ["not-a-uuid"],
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_UUID")

    def test_selected_category_scope_rejects_non_uuid_category_id(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "category_scope": "selected",
            "category_ids": ["not-a-uuid"],
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "INVALID_UUID")

    def test_filtered_single_currency_result_supports_scalar_currency_mode(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "currency_mode": "scalar",
            "account_scope": "selected",
            "account_ids": [RUB_ACCOUNT_ID],
        })

        self.assertEqual(result["totals"]["currency"], "RUB")
        self.assertEqual(result["totals"]["value"], 100)

    def test_filtered_mixed_currency_result_rejects_scalar_currency_mode(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _run_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
            "currency_mode": "scalar",
            "category_scope": "selected",
            "category_ids": [FOOD_TAG_ID],
            "category_role": "primary",
        })

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "MIXED_CURRENCY")


if __name__ == "__main__":
    unittest.main()
