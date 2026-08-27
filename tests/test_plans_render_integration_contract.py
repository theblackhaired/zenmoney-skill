import asyncio
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, config, tools, validation
from zenmoney.errors import ApiRequestError, ToolError


RUB = 1
USD = 2
ACCOUNT = "11111111-1111-1111-1111-111111111111"
USD_ACCOUNT = "22222222-2222-2222-2222-222222222222"
PARENT = "33333333-3333-3333-3333-333333333333"
CHILD = "44444444-4444-4444-4444-444444444444"
GRANDCHILD = "55555555-5555-5555-5555-555555555555"


def _config() -> dict:
    return {
        "budget_mode": "balance_vs_expense",
        "plan_settings_override": [],
        "accounts_meta": {},
        "round_balance_to_integer": True,
    }


def _budget(tag_id: str, month: str, outcome: int) -> dict:
    return {
        "user": 1,
        "tag": tag_id,
        "date": f"{month}-01",
        "income": 0,
        "incomeLock": False,
        "outcome": outcome,
        "outcomeLock": False,
    }


class PlansRenderIntegrationContractTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["user"] = {"1": {"id": 1}}
        cache.CACHE.data["instrument"] = {
            str(RUB): {"id": RUB, "shortTitle": "RUB", "title": "Ruble", "rate": 1},
            str(USD): {"id": USD, "shortTitle": "USD", "title": "Dollar", "rate": 70},
        }
        cache.CACHE.data["account"] = {
            ACCOUNT: {
                "id": ACCOUNT,
                "user": 1,
                "instrument": RUB,
                "title": "RUB",
                "type": "checking",
                "balance": 1000,
                "inBalance": True,
                "archive": False,
            }
        }
        cache.CACHE.data["tag"] = {
            PARENT: {"id": PARENT, "title": "Parent", "parent": None},
            CHILD: {"id": CHILD, "title": "Child", "parent": PARENT},
            GRANDCHILD: {"id": GRANDCHILD, "title": "Grandchild", "parent": CHILD},
        }

    def _run(self, *, today: str, cfg: dict | None = None, args: dict | None = None) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(cfg or _config()), encoding="utf-8")
            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(validation, "_today", return_value=today), \
                 patch.object(budget_tools, "_today", return_value=today), \
                 patch.object(config, "_load_config", return_value={"billing_period_start_day": 1}):
                return json.loads(
                    asyncio.run(
                        tools.tool_analyze_budget_detailed(
                            {
                                "period": "billing_period",
                                "show_forecast": False,
                                "show_calendar": False,
                                **(args or {}),
                            }
                        )
                    )
                )

    def test_historical_rates_are_fetched_before_render_and_drive_fx_output(self):
        cache.CACHE.data["account"] = {
            USD_ACCOUNT: {
                "id": USD_ACCOUNT,
                "user": 1,
                "instrument": USD,
                "title": "USD",
                "type": "checking",
                "balance": 100,
                "inBalance": True,
                "archive": False,
            }
        }
        calls = []

        async def fake_api_post(path, payload):
            calls.append((path, payload))
            rows = []
            for predicate in payload["predicates"]:
                start = datetime.date.fromisoformat(predicate["fromDate"])
                end = datetime.date.fromisoformat(predicate["toDate"])
                current = start
                while current <= end:
                    rows.append({
                        "baseInstrument": int(predicate["baseInstrument"]),
                        "quoteInstrument": int(predicate["quoteInstrument"]),
                        "date": current.isoformat(),
                        "rate": 90 if current.isoformat() >= "2026-04-30" else 80,
                    })
                    current += datetime.timedelta(days=1)
            return rows

        with patch("zenmoney.instrument_rates.transport._api_post", side_effect=fake_api_post):
            result = self._run(
                today="2026-05-10",
                args={"period_offset": -1},
            )

        self.assertEqual(calls[0][0], "/instrument-rates/")
        self.assertEqual(result["summary"]["opening_balance"]["total"], 8000)
        self.assertEqual(result["summary"]["exchange_difference"]["fact"], 1000)
        self.assertEqual(result["summary"]["balance"], 9000)
        self.assertEqual(result["summary"]["rate_source"]["historical_rates"], "instrument_rates_api")

    def test_unavailable_historical_rates_use_current_rate_with_provenance(self):
        cache.CACHE.data["account"] = {
            USD_ACCOUNT: {
                "id": USD_ACCOUNT,
                "user": 1,
                "instrument": USD,
                "title": "USD",
                "type": "checking",
                "balance": 100,
                "inBalance": True,
                "archive": False,
            }
        }

        error = ApiRequestError(
            endpoint="/instrument-rates/",
            status_code=401,
            message="ZenMoney API request failed with HTTP 401: /instrument-rates/",
        )
        with patch(
            "zenmoney.instrument_rates.transport._api_post",
            AsyncMock(side_effect=error),
        ):
            result = self._run(today="2026-05-10")

        self.assertEqual(result["summary"]["opening_balance"]["total"], 7000)
        self.assertEqual(result["summary"]["balance"], 7000)
        self.assertEqual(result["summary"]["rate_source"]["historical_rates"], "current_instrument_rate_fallback")
        self.assertEqual(result["metadata"]["instrument_rates"], {
            "policy": "historical_then_current",
            "request_status": "unavailable",
            "requested_pairs": 1,
            "requested_dates": 6,
            "requested_points": 6,
            "historical_points": 0,
            "rows_received": 0,
            "fallback": "current_instrument_rate",
            "endpoint": "/instrument-rates/",
            "status_code": 401,
            "warning": (
                "Historical instrument rates are unavailable; "
                "current synced Instrument.rate values were used"
            ),
        })

    def test_rate_fallback_does_not_hide_invalid_current_rate(self):
        cache.CACHE.data["instrument"][str(USD)]["rate"] = 0
        cache.CACHE.data["account"] = {
            USD_ACCOUNT: {
                "id": USD_ACCOUNT,
                "user": 1,
                "instrument": USD,
                "title": "USD",
                "type": "checking",
                "balance": 100,
                "inBalance": True,
                "archive": False,
            }
        }

        error = ApiRequestError(
            endpoint="/instrument-rates/",
            status_code=401,
            message="ZenMoney API request failed with HTTP 401: /instrument-rates/",
        )
        with patch(
            "zenmoney.instrument_rates.transport._api_post",
            AsyncMock(side_effect=error),
        ), self.assertRaises(ToolError) as caught:
            self._run(today="2026-05-10")

        self.assertEqual(caught.exception.code, "INVALID_INSTRUMENT_RATE")

    def test_non_rate_endpoint_api_error_is_not_converted_to_current_rate_fallback(self):
        cache.CACHE.data["account"] = {
            USD_ACCOUNT: {
                "id": USD_ACCOUNT,
                "user": 1,
                "instrument": USD,
                "title": "USD",
                "type": "checking",
                "balance": 100,
                "inBalance": True,
                "archive": False,
            }
        }

        error = ApiRequestError(endpoint="/v8/diff/", status_code=401)
        with patch(
            "zenmoney.instrument_rates.transport._api_post",
            AsyncMock(side_effect=error),
        ), self.assertRaises(ApiRequestError) as caught:
            self._run(today="2026-05-10")

        self.assertEqual(caught.exception.endpoint, "/v8/diff/")

    def test_transient_rate_api_errors_use_current_rate_fallback(self):
        for status_code in (None, 408, 425, 429, 500):
            with self.subTest(status_code=status_code):
                self.setUp()
                cache.CACHE.data["account"] = {
                    USD_ACCOUNT: {
                        "id": USD_ACCOUNT,
                        "user": 1,
                        "instrument": USD,
                        "title": "USD",
                        "type": "checking",
                        "balance": 100,
                        "inBalance": True,
                        "archive": False,
                    }
                }

                error = ApiRequestError(endpoint="/instrument-rates/", status_code=status_code)
                with patch(
                    "zenmoney.instrument_rates.transport._api_post",
                    AsyncMock(side_effect=error),
                ):
                    result = self._run(today="2026-05-10")

                self.assertEqual(result["summary"]["opening_balance"]["total"], 7000)
                self.assertEqual(result["metadata"]["instrument_rates"]["request_status"], "unavailable")
                self.assertEqual(result["metadata"]["instrument_rates"]["status_code"], status_code)

    def test_client_rate_api_errors_remain_fatal(self):
        for status_code in (400, 403, 404):
            with self.subTest(status_code=status_code):
                self.setUp()
                cache.CACHE.data["account"] = {
                    USD_ACCOUNT: {
                        "id": USD_ACCOUNT,
                        "user": 1,
                        "instrument": USD,
                        "title": "USD",
                        "type": "checking",
                        "balance": 100,
                        "inBalance": True,
                        "archive": False,
                    }
                }

                error = ApiRequestError(endpoint="/instrument-rates/", status_code=status_code)
                with patch(
                    "zenmoney.instrument_rates.transport._api_post",
                    AsyncMock(side_effect=error),
                ), self.assertRaises(ApiRequestError) as caught:
                    self._run(today="2026-05-10")

                self.assertEqual(caught.exception.status_code, status_code)

    def test_empty_rate_response_does_not_reuse_stale_rate_cache(self):
        cache.CACHE.data["account"] = {
            USD_ACCOUNT: {
                "id": USD_ACCOUNT,
                "user": 1,
                "instrument": USD,
                "title": "USD",
                "type": "checking",
                "balance": 100,
                "inBalance": True,
                "archive": False,
            }
        }

        async def full_historical_response(_path, payload):
            rows = []
            for predicate in payload["predicates"]:
                current = datetime.date.fromisoformat(predicate["fromDate"])
                end = datetime.date.fromisoformat(predicate["toDate"])
                while current <= end:
                    rows.append({
                        "baseInstrument": int(predicate["baseInstrument"]),
                        "quoteInstrument": int(predicate["quoteInstrument"]),
                        "date": current.isoformat(),
                        "rate": 90,
                    })
                    current += datetime.timedelta(days=1)
            return rows

        with patch(
            "zenmoney.instrument_rates.transport._api_post",
            side_effect=full_historical_response,
        ):
            historical_result = self._run(today="2026-05-10")

        with patch(
            "zenmoney.instrument_rates.transport._api_post",
            AsyncMock(return_value=[]),
        ):
            result = self._run(today="2026-05-10")

        self.assertEqual(historical_result["summary"]["opening_balance"]["total"], 9000)
        self.assertEqual(result["summary"]["opening_balance"]["total"], 7000)
        self.assertEqual(result["summary"]["rate_source"]["historical_rates"], "historical_with_current_fallback")
        self.assertEqual(result["metadata"]["instrument_rates"]["request_status"], "partial")
        self.assertEqual(result["metadata"]["instrument_rates"]["historical_points"], 0)
        self.assertEqual(result["metadata"]["instrument_rates"]["rows_received"], 0)
        self.assertIn("warning", result["metadata"]["instrument_rates"])

    def test_partial_rate_response_reports_received_rows_and_uses_current_rate_for_missing_dates(self):
        cache.CACHE.data["account"] = {
            USD_ACCOUNT: {
                "id": USD_ACCOUNT,
                "user": 1,
                "instrument": USD,
                "title": "USD",
                "type": "checking",
                "balance": 100,
                "inBalance": True,
                "archive": False,
            }
        }
        rows = [{
            "baseInstrument": USD,
            "quoteInstrument": RUB,
            "date": "2026-05-10",
            "rate": 90,
        }]

        with patch(
            "zenmoney.instrument_rates.transport._api_post",
            AsyncMock(return_value=rows),
        ):
            result = self._run(today="2026-05-10")

        self.assertEqual(result["metadata"]["instrument_rates"]["rows_received"], 1)
        self.assertEqual(result["metadata"]["instrument_rates"]["request_status"], "partial")
        self.assertLess(
            result["metadata"]["instrument_rates"]["historical_points"],
            result["metadata"]["instrument_rates"]["requested_points"],
        )
        self.assertEqual(result["metadata"]["instrument_rates"]["fallback"], "current_instrument_rate_when_missing")

    def test_single_currency_plans_skip_historical_rate_request(self):
        api_post = AsyncMock()
        with patch("zenmoney.instrument_rates.transport._api_post", api_post):
            result = self._run(today="2026-05-10")

        api_post.assert_not_awaited()
        self.assertEqual(result["summary"]["rate_source"]["historical_rates"], "not_needed")
        self.assertEqual(result["metadata"]["instrument_rates"], {
            "policy": "historical_then_current",
            "request_status": "not_required",
            "requested_pairs": 0,
            "requested_dates": 0,
            "requested_points": 0,
            "historical_points": 0,
            "rows_received": 0,
            "fallback": "none",
        })

    def test_excluded_opening_is_not_reintroduced_by_exchange_difference(self):
        cache.CACHE.data["account"] = {
            USD_ACCOUNT: {
                "id": USD_ACCOUNT,
                "user": 1,
                "instrument": USD,
                "title": "USD",
                "type": "checking",
                "balance": 100,
                "inBalance": True,
                "archive": False,
            }
        }
        cache.CACHE.data["transaction"] = {
            "april-income": {
                "id": "april-income",
                "date": "2026-04-10",
                "income": 10,
                "outcome": 0,
                "incomeAccount": USD_ACCOUNT,
                "outcomeAccount": USD_ACCOUNT,
                "incomeInstrument": USD,
                "outcomeInstrument": USD,
                "tag": [],
            }
        }

        async def fake_api_post(_path, payload):
            rows = []
            for predicate in payload["predicates"]:
                start = datetime.date.fromisoformat(predicate["fromDate"])
                end = datetime.date.fromisoformat(predicate["toDate"])
                current = start
                while current <= end:
                    rows.append({
                        "baseInstrument": int(predicate["baseInstrument"]),
                        "quoteInstrument": int(predicate["quoteInstrument"]),
                        "date": current.isoformat(),
                        "rate": 90 if current.isoformat() >= "2026-04-30" else 80,
                    })
                    current += datetime.timedelta(days=1)
            return rows

        cfg = _config()
        cfg["budget_mode"] = "income_vs_expense"
        with patch("zenmoney.instrument_rates.transport._api_post", side_effect=fake_api_post):
            result = self._run(today="2026-05-10", cfg=cfg, args={"period_offset": -1})

        self.assertEqual(result["summary"]["opening_balance"]["total"], 0)
        self.assertEqual(result["summary"]["income"]["for_balance"], 800)
        self.assertEqual(result["summary"]["exchange_difference"]["fact"], 100)
        self.assertEqual(result["summary"]["balance"], 900)

    def test_three_level_category_tree_keeps_grandchild_budget(self):
        cache.CACHE.data["budget"] = {
            cache.Cache._budget_key(_budget(GRANDCHILD, "2026-07", 300)): _budget(
                GRANDCHILD,
                "2026-07",
                300,
            )
        }

        result = self._run(today="2026-07-15")

        parent = result["expenses"][0]
        child = parent["children"][0]
        grandchild = child["children"][0]
        self.assertEqual(parent["category_id"], PARENT)
        self.assertEqual(child["category_id"], CHILD)
        self.assertEqual(grandchild["category_id"], GRANDCHILD)
        self.assertEqual(result["summary"]["expense"]["for_balance"], 300)

    def test_future_opening_uses_previous_period_budget_not_requested_period_budget(self):
        cfg = _config()
        cfg["plan_settings_override"] = ["includeOpeningBalance"]
        cache.CACHE.data["budget"] = {
            cache.Cache._budget_key(_budget(PARENT, "2026-07", 100)): _budget(
                PARENT,
                "2026-07",
                100,
            ),
            cache.Cache._budget_key(_budget(PARENT, "2026-08", 900)): _budget(
                PARENT,
                "2026-08",
                900,
            ),
        }

        result = self._run(
            today="2026-07-15",
            cfg=cfg,
            args={"period_offset": 1},
        )

        self.assertEqual(result["summary"]["opening_balance"]["source"], "previous_day_summary")
        self.assertEqual(result["summary"]["opening_balance"]["total"], 900)

    def test_past_period_exchange_uses_period_end_closing_snapshot(self):
        cache.CACHE.data["account"] = {
            USD_ACCOUNT: {
                "id": USD_ACCOUNT,
                "user": 1,
                "instrument": USD,
                "title": "USD",
                "type": "checking",
                "balance": 100,
                "inBalance": True,
                "archive": False,
            }
        }
        cache.CACHE.data["transaction"] = {
            "after-period-income": {
                "id": "after-period-income",
                "date": "2026-05-05",
                "income": 50,
                "outcome": 0,
                "incomeAccount": USD_ACCOUNT,
                "outcomeAccount": USD_ACCOUNT,
                "incomeInstrument": USD,
                "outcomeInstrument": USD,
                "tag": [],
            }
        }

        async def fake_api_post(_path, payload):
            rows = []
            for predicate in payload["predicates"]:
                start = datetime.date.fromisoformat(predicate["fromDate"])
                end = datetime.date.fromisoformat(predicate["toDate"])
                current = start
                while current <= end:
                    rows.append({
                        "baseInstrument": int(predicate["baseInstrument"]),
                        "quoteInstrument": int(predicate["quoteInstrument"]),
                        "date": current.isoformat(),
                        "rate": 90 if current.isoformat() >= "2026-04-30" else 80,
                    })
                    current += datetime.timedelta(days=1)
            return rows

        with patch("zenmoney.instrument_rates.transport._api_post", side_effect=fake_api_post):
            result = self._run(today="2026-05-10", args={"period_offset": -1})

        components = result["summary"]["exchange_difference"]["components"]
        self.assertEqual(components["targetBalance"], 4500)
        self.assertEqual(result["summary"]["opening_balance"]["total"], 4000)

    def test_refunds_mode_drives_fact_with_refund_and_reserve(self):
        cache.CACHE.data["tag"][PARENT].update(
            {"showIncome": False, "showOutcome": True}
        )
        cache.CACHE.data["budget"] = {
            cache.Cache._budget_key(_budget(PARENT, "2026-07", 200)): _budget(
                PARENT,
                "2026-07",
                200,
            )
        }
        cache.CACHE.data["account"][USD_ACCOUNT] = {
            **cache.CACHE.data["account"][ACCOUNT],
            "id": USD_ACCOUNT,
            "title": "Other RUB",
        }
        cache.CACHE.data["transaction"] = {
            "expense": {
                "id": "expense",
                "date": "2026-07-05",
                "income": 0,
                "outcome": 100,
                "incomeAccount": ACCOUNT,
                "outcomeAccount": ACCOUNT,
                "incomeInstrument": RUB,
                "outcomeInstrument": RUB,
                "tag": [PARENT],
            },
            "refund": {
                "id": "refund",
                "date": "2026-07-06",
                "income": 30,
                "outcome": 0,
                "incomeAccount": ACCOUNT,
                "outcomeAccount": ACCOUNT,
                "incomeInstrument": RUB,
                "outcomeInstrument": RUB,
                "tag": [PARENT],
            },
            "cross-account-income": {
                "id": "cross-account-income",
                "date": "2026-07-07",
                "income": 20,
                "outcome": 0,
                "incomeAccount": USD_ACCOUNT,
                "outcomeAccount": ACCOUNT,
                "incomeInstrument": RUB,
                "outcomeInstrument": RUB,
                "tag": [PARENT],
            },
        }
        cfg = _config()
        cfg.update(
            {
                "budget_mode": "income_vs_expense",
                "difference_calculation_mode": "REFUNDS",
            }
        )

        result = self._run(today="2026-07-15", cfg=cfg)

        expense = result["expenses"][0]
        self.assertEqual(expense["actual"], 100)
        self.assertEqual(expense["actual_with_refunds"], 70)
        self.assertEqual(expense["plan"], 200)
        self.assertEqual(expense["remaining"], 100)
        self.assertEqual(expense["overspend"], 0)
        self.assertEqual(expense["reserve_remaining"], 130)
        self.assertEqual(result["summary"]["expense"]["actual_with_refunds"], 70)
        self.assertEqual(result["summary"]["expense"]["category_difference_policy"], "REFUNDS")
        self.assertEqual(result["income"][0]["actual_with_refunds"], 20)


if __name__ == "__main__":
    unittest.main()
