import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import budget_tools, cache, config
from zenmoney.errors import InvalidArgumentError


TAG_ID = "33333333-3333-3333-3333-333333333333"
MONTH_DATE = "2026-07-01"


def _budget(user_id: int, outcome: float) -> dict:
    return {
        "user": user_id,
        "tag": TAG_ID,
        "date": MONTH_DATE,
        "income": 0,
        "incomeLock": False,
        "outcome": outcome,
        "outcomeLock": False,
    }


class BudgetUserIsolationTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()
        cache.CACHE.data["user"] = {
            "1": {"id": 1},
            "2": {"id": 2},
        }
        cache.CACHE.data["tag"] = {
            TAG_ID: {"id": TAG_ID, "title": "Food", "parent": None},
        }

    def _config_path(self, temp_dir: str, payload: dict) -> Path:
        path = Path(temp_dir) / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_cache_key_retains_user_tag_and_date(self):
        cache.CACHE.apply_diff({
            "budget": [
                _budget(1, 100),
                _budget(2, 200),
            ],
        })

        self.assertEqual(
            sorted(cache.CACHE.data["budget"]),
            [
                f"1:{TAG_ID}:{MONTH_DATE}",
                f"2:{TAG_ID}:{MONTH_DATE}",
            ],
        )
        self.assertEqual(len(cache.CACHE.budgets()), 2)

    def test_cache_apply_rejects_budget_without_user_before_mutating_cursor(self):
        invalid_diff = {
            "serverTimestamp": 2,
            "budget": [{
                "tag": TAG_ID,
                "date": MONTH_DATE,
                "outcome": 100,
            }],
        }
        apply_cases = [
            ("incremental", lambda target: target.apply_diff(invalid_diff)),
            (
                "force_fetch",
                lambda target: target.apply_force_fetch_diff(invalid_diff, ["budget"]),
            ),
        ]

        for label, apply in apply_cases:
            with self.subTest(apply=label):
                target = cache.Cache()
                target.server_timestamp = 1
                with self.assertRaisesRegex(ValueError, "missing required field: user"):
                    apply(target)

                self.assertEqual(target.server_timestamp, 1)
                self.assertEqual(target.data["budget"], {})

    def test_cache_load_rejects_budget_without_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(
                json.dumps({
                    "serverTimestamp": 1,
                    "budget": [{
                        "tag": TAG_ID,
                        "date": MONTH_DATE,
                        "outcome": 100,
                    }],
                }),
                encoding="utf-8",
            )

            loaded = cache.Cache()
            with patch.object(config, "CACHE_PATH", cache_path), \
                 self.assertRaisesRegex(ValueError, "missing required field: user"):
                loaded.load()

        self.assertEqual(loaded.data["budget"], {})

    def test_get_budgets_returns_only_configured_plan_user(self):
        for budget in (_budget(1, 100), _budget(2, 200)):
            cache.CACHE.data["budget"][cache.Cache._budget_key(budget)] = budget

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config_path(temp_dir, {"plan_user_id": 2})
            with patch.object(budget_tools, "_cfg_path", config_path):
                result = json.loads(asyncio.run(budget_tools.tool_get_budgets({"month": "2026-07"})))

        self.assertEqual([budget["outcome"] for budget in result], [200])

    def test_get_budgets_rejects_ambiguous_user_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config_path(temp_dir, {})
            with patch.object(budget_tools, "_cfg_path", config_path):
                with self.assertRaisesRegex(InvalidArgumentError, "multiple users"):
                    asyncio.run(budget_tools.tool_get_budgets({"month": "2026-07"}))

    def test_get_budgets_rejects_missing_synced_user(self):
        cache.CACHE.data["user"] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config_path(temp_dir, {})
            with patch.object(budget_tools, "_cfg_path", config_path):
                with self.assertRaisesRegex(InvalidArgumentError, "preferences are unavailable"):
                    asyncio.run(budget_tools.tool_get_budgets({"month": "2026-07"}))

    def test_budget_writes_reject_ambiguous_user_selection_before_lookup_or_write(self):
        calls = [
            (budget_tools.tool_create_budget, {"outcome": 100}),
            (budget_tools.tool_update_budget, {"outcome": 100}),
            (budget_tools.tool_delete_budget, {}),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config_path(temp_dir, {})
            for handler, extra_args in calls:
                with self.subTest(handler=handler.__name__), \
                     patch.object(budget_tools, "_cfg_path", config_path), \
                     patch.object(budget_tools, "_write_diff") as mocked_write:
                    with self.assertRaisesRegex(InvalidArgumentError, "multiple users"):
                        asyncio.run(handler({
                            "month": "2026-07",
                            "category": "Food",
                            **extra_args,
                        }))
                    mocked_write.assert_not_called()

    def test_create_budget_writes_configured_plan_user_and_confirms_scoped_key(self):
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            created = diff["budget"][0]
            cache.CACHE.data["budget"][cache.Cache._budget_key(created)] = created
            return {"budget": [created]}

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config_path(temp_dir, {"plan_user_id": 2})
            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(budget_tools, "_write_diff", side_effect=fake_write_diff):
                asyncio.run(budget_tools.tool_create_budget({
                    "month": "2026-07",
                    "category": "Food",
                    "outcome": 300,
                }))

        self.assertEqual(captured["diff"]["budget"][0]["user"], 2)
        self.assertIn(f"2:{TAG_ID}:{MONTH_DATE}", cache.CACHE.data["budget"])

    def test_update_budget_changes_only_configured_plan_user(self):
        first = _budget(1, 100)
        second = _budget(2, 200)
        for budget in (first, second):
            cache.CACHE.data["budget"][cache.Cache._budget_key(budget)] = budget
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config_path(temp_dir, {"plan_user_id": 2})
            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(budget_tools, "_write_diff", side_effect=fake_write_diff):
                asyncio.run(budget_tools.tool_update_budget({
                    "month": "2026-07",
                    "category": "Food",
                    "outcome": 250,
                }))

        updated = captured["diff"]["budget"][0]
        self.assertEqual(updated["user"], 2)
        self.assertEqual(updated["outcome"], 250)
        self.assertEqual(cache.CACHE.data["budget"][cache.Cache._budget_key(first)]["outcome"], 100)

    def test_delete_budget_zeroes_only_configured_plan_user(self):
        first = _budget(1, 100)
        second = _budget(2, 200)
        for budget in (first, second):
            cache.CACHE.data["budget"][cache.Cache._budget_key(budget)] = budget
        captured = {}

        async def fake_write_diff(diff):
            captured["diff"] = diff
            return {}

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config_path(temp_dir, {"plan_user_id": 2})
            with patch.object(budget_tools, "_cfg_path", config_path), \
                 patch.object(budget_tools, "_write_diff", side_effect=fake_write_diff):
                asyncio.run(budget_tools.tool_delete_budget({
                    "month": "2026-07",
                    "category": "Food",
                }))

        deleted = captured["diff"]["budget"][0]
        self.assertEqual(deleted["user"], 2)
        self.assertEqual(deleted["outcome"], 0)
        self.assertEqual(cache.CACHE.data["budget"][cache.Cache._budget_key(first)]["outcome"], 100)


if __name__ == "__main__":
    unittest.main()
