import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney.domain import _fmt_budget
from zenmoney.plans.context import build_context
from zenmoney.plans.render import _category_rows, _period_events


ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
CATEGORY_ID = "22222222-2222-2222-2222-222222222222"


class _SyntheticCache:
    def __init__(self, *, forecast_enabled=Ellipsis, marker=None):
        user = {"id": 1}
        if forecast_enabled is not Ellipsis:
            user["isForecastEnabled"] = forecast_enabled
        self._users = [user]
        self._marker = marker

    def users(self):
        return self._users

    def accounts(self):
        return [{
            "id": ACCOUNT_ID,
            "instrument": 1,
            "inBalance": True,
            "archive": False,
            "type": "checking",
        }]

    def tags(self):
        return [{
            "id": CATEGORY_ID,
            "title": "Food",
            "parent": None,
            "showIncome": True,
            "showOutcome": True,
        }]

    def instruments(self):
        return [{"id": 1, "shortTitle": "RUB", "rate": 1}]

    def transactions(self):
        return []

    def reminders(self):
        if self._marker is None:
            return []
        return [{
            "id": "reminder-1",
            "incomeAccount": ACCOUNT_ID,
            "outcomeAccount": ACCOUNT_ID,
            "income": 0,
            "outcome": 50,
            "tag": [CATEGORY_ID],
        }]

    def reminder_markers(self):
        return [] if self._marker is None else [self._marker]


def _context(*, forecast_enabled=Ellipsis, budgets=None, marker=None):
    return build_context(
        args={
            "budget_mode": "income_vs_expense",
            "resolved_period": {
                "start_date": "2026-08-20",
                "end_date": "2026-09-19",
            },
        },
        cfg={"plan_settings_override": []},
        cache=_SyntheticCache(
            forecast_enabled=forecast_enabled,
            marker=marker,
        ),
        budgets=budgets or [],
        today="2026-08-26",
    )


class PlansForecastContractTests(unittest.TestCase):
    def test_budget_formatter_preserves_both_forecast_side_flags(self):
        formatted = _fmt_budget({
            "tag": None,
            "date": "2026-08-01",
            "income": 100,
            "outcome": 200,
            "isIncomeForecast": True,
            "isOutcomeForecast": False,
        })

        self.assertTrue(formatted["isIncomeForecast"])
        self.assertFalse(formatted["isOutcomeForecast"])

    def test_missing_user_forecast_flag_uses_apk_enabled_default(self):
        self.assertTrue(_context().forecast_enabled)

    def test_disabled_forecast_zeroes_only_each_flagged_budget_side(self):
        cases = (
            (True, False, Decimal(0), Decimal(200)),
            (False, True, Decimal(100), Decimal(0)),
        )
        for income_flag, outcome_flag, expected_income, expected_outcome in cases:
            with self.subTest(
                income_flag=income_flag,
                outcome_flag=outcome_flag,
            ):
                ctx = _context(
                    forecast_enabled=False,
                    budgets=[{
                        "category_id": CATEGORY_ID,
                        "income": 100,
                        "outcome": 200,
                        "incomeLock": False,
                        "outcomeLock": False,
                        "isIncomeForecast": income_flag,
                        "isOutcomeForecast": outcome_flag,
                    }],
                )
                rows, _ = _category_rows(
                    ctx,
                    [],
                    lambda amount, *_: Decimal(str(amount)),
                    {"id": 1},
                )

                self.assertEqual(rows[CATEGORY_ID].income.explicit_budget, expected_income)
                self.assertEqual(rows[CATEGORY_ID].outcome.explicit_budget, expected_outcome)

    def test_enabled_forecast_keeps_both_flagged_budget_sides(self):
        ctx = _context(
            forecast_enabled=True,
            budgets=[{
                "category_id": CATEGORY_ID,
                "income": 100,
                "outcome": 200,
                "isIncomeForecast": True,
                "isOutcomeForecast": True,
            }],
        )
        rows, _ = _category_rows(
            ctx,
            [],
            lambda amount, *_: Decimal(str(amount)),
            {"id": 1},
        )

        self.assertEqual(rows[CATEGORY_ID].income.explicit_budget, Decimal(100))
        self.assertEqual(rows[CATEGORY_ID].outcome.explicit_budget, Decimal(200))

    def test_forecast_marker_is_gated_by_selected_user_flag(self):
        marker = {
            "id": "marker-1",
            "reminder": "reminder-1",
            "date": "2026-08-27",
            "state": "planned",
            "income": 0,
            "outcome": 50,
            "isForecast": True,
        }

        enabled_events = _period_events(
            _context(forecast_enabled=True, marker=marker),
            "2026-08-20",
            "2026-09-19",
        )
        disabled_events = _period_events(
            _context(forecast_enabled=False, marker=marker),
            "2026-08-20",
            "2026-09-19",
        )

        self.assertEqual([event.source_id for event in enabled_events], ["marker-1"])
        self.assertEqual(disabled_events, [])


if __name__ == "__main__":
    unittest.main()
