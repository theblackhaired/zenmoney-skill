import asyncio
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import instrument_rates
from zenmoney.errors import ToolError


class InstrumentRatesContractTests(unittest.TestCase):
    def setUp(self):
        self.cache = instrument_rates.InstrumentRateCache()
        self.main = {"id": 1, "rate": 1}
        self.usd = {"id": 2, "rate": 80}
        self.eur = {"id": 3, "rate": 100}

    def test_api_request_uses_confirmed_wrapper_and_predicate_shape(self):
        api_post = AsyncMock(return_value=[{
            "baseInstrument": 2,
            "quoteInstrument": 1,
            "date": "2026-08-20",
            "rate": 81.5,
        }])

        with patch.object(instrument_rates.transport, "_api_post", api_post):
            rows = asyncio.run(instrument_rates.fetch_instrument_rates(
                [{
                    "baseInstrument": "2",
                    "quoteInstrument": "1",
                    "fromDate": "2026-08-20",
                    "toDate": "2026-08-21",
                }],
                cache=self.cache,
            ))

        api_post.assert_awaited_once_with(
            "/instrument-rates/",
            {"predicates": [{
                "baseInstrument": "2",
                "quoteInstrument": "1",
                "fromDate": "2026-08-20",
                "toDate": "2026-08-21",
            }]},
        )
        self.assertEqual(rows[0]["rate"], Decimal("81.5"))
        self.assertEqual(self.cache.get(2, 1, "2026-08-20"), Decimal("81.5"))

    def test_optional_dates_are_omitted_not_serialized_as_null(self):
        predicate = instrument_rates.instrument_rate_predicate(2, 1)
        self.assertEqual(predicate, {"baseInstrument": "2", "quoteInstrument": "1"})

    def test_date_conversion_uses_source_main_over_target_main(self):
        self.cache.add_rows([
            {"baseInstrument": 2, "quoteInstrument": 1, "date": "2026-08-20", "rate": 80},
            {"baseInstrument": 3, "quoteInstrument": 1, "date": "2026-08-20", "rate": 100},
        ])

        result = instrument_rates.convert_on_date(
            Decimal("40"),
            source=self.usd,
            target=self.eur,
            main=self.main,
            on_date="2026-08-20",
            cache=self.cache,
        )

        # 40 USD * 80 RUB/USD / 100 RUB/EUR = 32 EUR.
        self.assertEqual(result, Decimal("32"))

    def test_main_instrument_historical_leg_is_identity(self):
        self.cache.add_rows([
            {"baseInstrument": 3, "quoteInstrument": 1, "date": "2026-08-20", "rate": 100},
        ])
        factor = instrument_rates.conversion_rate_on_date(
            source=self.main,
            target=self.eur,
            main=self.main,
            on_date="2026-08-20",
            cache=self.cache,
        )
        self.assertEqual(factor, Decimal("0.01"))

    def test_missing_target_historical_leg_falls_back_only_for_target_leg(self):
        self.cache.add_rows([
            {"baseInstrument": 2, "quoteInstrument": 1, "date": "2026-08-20", "rate": 40},
        ])

        factor = instrument_rates.conversion_rate_on_date(
            source=self.usd,
            target=self.eur,
            main=self.main,
            on_date="2026-08-20",
            cache=self.cache,
        )

        self.assertEqual(factor, Decimal("0.4"))

    def test_missing_source_historical_leg_falls_back_only_for_source_leg(self):
        self.cache.add_rows([
            {"baseInstrument": 3, "quoteInstrument": 1, "date": "2026-08-20", "rate": 50},
        ])
        factor = instrument_rates.conversion_rate_on_date(
            source=self.usd,
            target=self.eur,
            main=self.main,
            on_date="2026-08-20",
            cache=self.cache,
        )
        self.assertEqual(factor, Decimal("1.6"))

    def test_exchange_converter_bridges_ids_to_instrument_rows(self):
        self.cache.add_rows([
            {"baseInstrument": 2, "quoteInstrument": 1, "date": "2026-08-20", "rate": 80},
            {"baseInstrument": 3, "quoteInstrument": 1, "date": "2026-08-20", "rate": 100},
        ])
        convert = instrument_rates.exchange_converter(
            instruments=[self.main, self.usd, self.eur],
            main_instrument_id=1,
            cache=self.cache,
        )

        self.assertEqual(convert(Decimal("40"), 2, 3, "2026-08-20"), Decimal("32"))

    def test_exchange_converter_rejects_unknown_instrument_id(self):
        convert = instrument_rates.exchange_converter(
            instruments=[self.main, self.usd, self.eur],
            main_instrument_id=1,
            cache=self.cache,
        )

        with self.assertRaises(ToolError) as caught:
            convert(Decimal("1"), 999, 1, "2026-08-20")
        self.assertEqual(caught.exception.code, "INVALID_INSTRUMENT_RATE")

    def test_same_instrument_is_identity_without_rate_lookup(self):
        malformed = {"id": 2, "rate": 0}
        result = instrument_rates.convert_on_date(
            Decimal("12.34"),
            source=malformed,
            target=malformed,
            main=self.main,
            on_date="2026-08-20",
            cache=self.cache,
        )
        self.assertEqual(result, Decimal("12.34"))

    def test_cache_key_does_not_collide_for_concatenated_ids(self):
        self.cache.add_rows([
            {"baseInstrument": 1, "quoteInstrument": 23, "date": "2026-08-20", "rate": 4},
            {"baseInstrument": 12, "quoteInstrument": 3, "date": "2026-08-20", "rate": 5},
        ])
        self.assertEqual(self.cache.get(1, 23, "2026-08-20"), Decimal("4"))
        self.assertEqual(self.cache.get(12, 3, "2026-08-20"), Decimal("5"))

    def test_duplicate_response_key_is_resolved_by_response_order(self):
        self.cache.add_rows([
            {"baseInstrument": 2, "quoteInstrument": 1, "date": "2026-08-20", "rate": 80},
            {"baseInstrument": 2, "quoteInstrument": 1, "date": "2026-08-20", "rate": 81},
        ])
        self.assertEqual(self.cache.get(2, 1, "2026-08-20"), Decimal("81"))

    def test_malformed_or_zero_response_rate_is_explicit_error(self):
        for bad_rate in (None, "not-a-number", -1, 0, "NaN"):
            with self.subTest(rate=bad_rate):
                with self.assertRaises(ToolError) as caught:
                    self.cache.add_rows([{
                        "baseInstrument": 2,
                        "quoteInstrument": 1,
                        "date": "2026-08-20",
                        "rate": bad_rate,
                    }])
                self.assertEqual(caught.exception.code, "INVALID_INSTRUMENT_RATE")

    def test_zero_current_rate_is_explicit_error_on_fallback(self):
        with self.assertRaises(ToolError) as caught:
            instrument_rates.conversion_rate_on_date(
                source={"id": 2, "rate": 0},
                target=self.eur,
                main=self.main,
                on_date="2026-08-20",
                cache=self.cache,
            )
        self.assertEqual(caught.exception.code, "INVALID_INSTRUMENT_RATE")

    def test_non_ascii_digit_instrument_id_is_explicit_error(self):
        with self.assertRaises(ToolError) as caught:
            instrument_rates.instrument_rate_predicate("²", 1)
        self.assertEqual(caught.exception.code, "INVALID_INSTRUMENT_RATE")

    def test_malformed_response_wrapper_is_explicit_error(self):
        with patch.object(
            instrument_rates.transport,
            "_api_post",
            AsyncMock(return_value={"items": []}),
        ):
            with self.assertRaises(ToolError) as caught:
                asyncio.run(instrument_rates.fetch_instrument_rates(
                    [instrument_rates.instrument_rate_predicate(2, 1)],
                    cache=self.cache,
                ))
        self.assertEqual(caught.exception.code, "INVALID_INSTRUMENT_RATE")

    def test_empty_predicates_do_not_call_api(self):
        api_post = AsyncMock()
        with patch.object(instrument_rates.transport, "_api_post", api_post):
            rows = asyncio.run(instrument_rates.fetch_instrument_rates([], cache=self.cache))
        self.assertEqual(rows, [])
        api_post.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
