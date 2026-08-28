import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.currency_conversion import current_rate_converter
from zenmoney.errors import ToolError


class CurrencyConversionContractTests(unittest.TestCase):
    def setUp(self):
        self.convert = current_rate_converter([
            {"id": 1, "rate": 1},
            {"id": 2, "rate": 80},
            {"id": 3, "rate": 100},
        ])

    def test_converts_with_synced_current_rates(self):
        self.assertEqual(
            self.convert(Decimal("40"), 2, 3, "2026-08-20"),
            Decimal("32"),
        )

    def test_same_instrument_is_identity_without_rate_lookup(self):
        convert = current_rate_converter([{"id": 2, "rate": 0}])
        self.assertEqual(
            convert(Decimal("12.34"), 2, 2, "2026-08-20"),
            Decimal("12.34"),
        )

    def test_unknown_instrument_is_explicit_error(self):
        with self.assertRaises(ToolError) as caught:
            self.convert(Decimal("1"), 999, 1, "2026-08-20")
        self.assertEqual(caught.exception.code, "INVALID_INSTRUMENT_RATE")

    def test_invalid_current_rate_is_explicit_error(self):
        for bad_rate in (None, "not-a-number", -1, 0, "NaN"):
            with self.subTest(rate=bad_rate):
                convert = current_rate_converter([
                    {"id": 1, "rate": 1},
                    {"id": 2, "rate": bad_rate},
                ])
                with self.assertRaises(ToolError) as caught:
                    convert(Decimal("1"), 2, 1, "2026-08-20")
                self.assertEqual(caught.exception.code, "INVALID_INSTRUMENT_RATE")


if __name__ == "__main__":
    unittest.main()
