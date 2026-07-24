import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import cache, config, transport


class TransportWriteConfirmationTests(unittest.TestCase):
    def setUp(self):
        cache.CACHE = cache.Cache()

    def _run_with_temp_cache(self, changes, responses):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(json.dumps({"serverTimestamp": 0}), encoding="utf-8")

            async def fake_api_post(endpoint, body):
                self.assertEqual(endpoint, "/v8/diff/")
                return responses.pop(0)

            with patch.object(config, "CACHE_PATH", cache_path), \
                 patch.object(transport, "_api_post", AsyncMock(side_effect=fake_api_post)) as api_post:
                result = asyncio.run(transport._write_diff(changes))
                saved = json.loads(cache_path.read_text(encoding="utf-8"))
                calls = [call.args for call in api_post.await_args_list]

        return result, saved, calls

    def test_write_is_confirmed_by_force_fetch_and_saved_to_cache(self):
        tx = {"id": "tx-1", "changed": 10, "deleted": False, "outcome": 100}

        result, saved, calls = self._run_with_temp_cache(
            {"transaction": [tx]},
            [
                {"serverTimestamp": 1, "transaction": [tx]},
                {"serverTimestamp": 2, "transaction": [{**tx, "outcome": 100}]},
            ],
        )

        self.assertEqual(result["serverTimestamp"], 1)
        self.assertEqual(saved["serverTimestamp"], 2)
        self.assertEqual(saved["transaction"][0]["id"], "tx-1")
        self.assertEqual(calls[1][1]["serverTimestamp"], 0)
        self.assertEqual(calls[1][1]["forceFetch"], ["transaction"])

    def test_write_fails_when_server_confirmation_lacks_written_entity(self):
        tx = {"id": "tx-missing", "changed": 10, "deleted": False}

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(json.dumps({"serverTimestamp": 0}), encoding="utf-8")
            with patch.object(config, "CACHE_PATH", cache_path), \
                 patch.object(transport, "_api_post", AsyncMock(side_effect=[
                     {"serverTimestamp": 1},
                     {"serverTimestamp": 2, "transaction": []},
                 ])):
                with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                    asyncio.run(transport._write_diff({"transaction": [tx]}))

    def test_write_fails_when_server_keeps_stale_entity_values(self):
        tx = {"id": "tx-stale", "changed": 10, "deleted": False, "outcome": 100}

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(json.dumps({"serverTimestamp": 0}), encoding="utf-8")
            with patch.object(config, "CACHE_PATH", cache_path), \
                 patch.object(transport, "_api_post", AsyncMock(side_effect=[
                     {"serverTimestamp": 1},
                     {"serverTimestamp": 2, "transaction": [{**tx, "outcome": 50}]},
                 ])):
                with self.assertRaisesRegex(RuntimeError, "mismatched.*outcome"):
                    asyncio.run(transport._write_diff({"transaction": [tx]}))

    def test_write_allows_server_normalization_of_derived_transaction_fields(self):
        tx = {
            "id": "tx-normalized",
            "changed": 10,
            "deleted": False,
            "outcome": 100,
            "payee": "Store",
            "originalPayee": None,
            "opIncome": None,
            "opOutcome": None,
        }
        confirmed = {
            **tx,
            "originalPayee": "Store",
            "opIncome": 0,
            "opOutcome": 0,
        }

        result, saved, _ = self._run_with_temp_cache(
            {"transaction": [tx]},
            [
                {"serverTimestamp": 1, "transaction": [tx]},
                {"serverTimestamp": 2, "transaction": [confirmed]},
            ],
        )

        self.assertEqual(result["serverTimestamp"], 1)
        self.assertEqual(saved["transaction"][0]["originalPayee"], "Store")
        self.assertEqual(saved["transaction"][0]["opOutcome"], 0)

    def test_budget_write_confirms_synthetic_key(self):
        budget = {
            "user": 1,
            "changed": 10,
            "tag": None,
            "date": "2026-07-01",
            "income": 0,
            "outcome": 5000,
        }

        _, saved, calls = self._run_with_temp_cache(
            {"budget": [budget]},
            [
                {"serverTimestamp": 1, "budget": [budget]},
                {"serverTimestamp": 2, "budget": [budget]},
            ],
        )

        self.assertEqual(calls[1][1]["forceFetch"], ["budget"])
        self.assertEqual(calls[1][1]["serverTimestamp"], 0)
        self.assertEqual(saved["budget"][0]["tag"], None)
        self.assertEqual(saved["budget"][0]["date"], "2026-07-01")

    def test_deletion_requires_server_absence_and_removes_from_cache(self):
        existing = {"id": "reminder-1", "changed": 1}
        deletion = {"id": "reminder-1", "object": "reminder", "stamp": 10, "user": 1}

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".cache.json"
            cache_path.write_text(
                json.dumps({"serverTimestamp": 0, "reminder": [existing]}),
                encoding="utf-8",
            )
            with patch.object(config, "CACHE_PATH", cache_path), \
                 patch.object(transport, "_api_post", AsyncMock(side_effect=[
                     {"serverTimestamp": 1},
                     {"serverTimestamp": 2, "reminder": []},
                 ])):
                cache.CACHE.load()
                asyncio.run(transport._write_diff({"deletion": [deletion]}))
                saved = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["reminder"], [])


if __name__ == "__main__":
    unittest.main()
