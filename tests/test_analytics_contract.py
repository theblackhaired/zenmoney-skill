import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import cache, dispatch, tools


RUB_ACCOUNT_ID = "acct-rub"
KZT_ACCOUNT_ID = "acct-kzt"
UNKNOWN_ACCOUNT_ID = "acct-unknown"
FOOD_TAG_ID = "tag-food"
SIDE_TAG_ID = "tag-side"
SALARY_TAG_ID = "tag-salary"

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
        },
        KZT_ACCOUNT_ID: {
            "id": KZT_ACCOUNT_ID,
            "user": 1,
            "instrument": 2,
            "title": "KZT Card",
            "type": "ccard",
            "balance": 0,
        },
        UNKNOWN_ACCOUNT_ID: {
            "id": UNKNOWN_ACCOUNT_ID,
            "user": 1,
            "instrument": 999,
            "title": "Unknown Currency Card",
            "type": "ccard",
            "balance": 0,
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
                    merchant="merchant-store",
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
            merchants={"merchant-store": {"id": "merchant-store", "title": "Store"}},
        )

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "merchant",
        })

        self.assertEqual({group["key"] for group in result["groups"]}, {"merchant:merchant-store", "payee:Store"})

    def test_groups_keep_currency_identity(self):
        _seed_reference_cache(_money_flow_transactions())

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
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
            "tx-unknown-currency": _tx(
                "tx-unknown-currency",
                income=0,
                outcome=100,
                account_id=UNKNOWN_ACCOUNT_ID,
                instrument_id=999,
                tags=[FOOD_TAG_ID],
            ),
        })

        result = _call_analytics({
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "report": "outcome",
            "group_by": "category",
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


if __name__ == "__main__":
    unittest.main()
