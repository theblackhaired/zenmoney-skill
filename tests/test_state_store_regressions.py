import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from zenmoney import budget_tools, cache, config, dispatch, tools, transport, validation

class StateStoreRegressionTests(unittest.TestCase):

    def setUp(self):
        cache.CACHE = cache.Cache()

    def test_atomic_json_write_uses_replace_and_preserves_existing_file_on_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'state.json'
            path.write_text('{"serverTimestamp": 10}', encoding='utf-8')
            with patch.object(config.os, 'replace', side_effect=RuntimeError('replace failed')) as mocked_replace:
                with self.assertRaisesRegex(RuntimeError, 'replace failed'):
                    config.write_json_state_atomic(path, {'serverTimestamp': 11})
            mocked_replace.assert_called_once()
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'serverTimestamp': 10})

    def test_file_lock_rejects_second_writer_while_held(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / 'state.json.lock'
            with config._FileLock(lock_path):
                with self.assertRaises(TimeoutError):
                    with config._FileLock(lock_path, timeout=0.01, poll_interval=0.001):
                        pass
            self.assertTrue(lock_path.exists())

    def test_non_object_state_json_is_rejected_and_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'config.json'
            config_path.write_text('[]', encoding='utf-8')
            with self.assertRaises(config.CorruptStateError) as raised:
                config.read_json_state(config_path)
            self.assertEqual(raised.exception.to_payload()['code'], 'CORRUPT_STATE')
            self.assertEqual(config_path.read_text(encoding='utf-8'), '[]')

    def test_budget_analysis_does_not_hide_corrupt_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'config.json'
            config_path.write_text('{"budget_mode":', encoding='utf-8')
            with patch.object(budget_tools, '_cfg_path', config_path), patch.object(validation, '_billing_start_day', return_value=1):
                with self.assertRaises(config.CorruptStateError):
                    asyncio.run(budget_tools.tool_analyze_budget_detailed({'period': 'billing_period'}))

    def test_force_fetch_replaces_only_requested_entity_stores_before_deletions(self):
        cache.CACHE.apply_diff({'serverTimestamp': 1, 'account': [{'id': 'account-delete', 'title': 'Delete me'}, {'id': 'account-keep', 'title': 'Keep me'}], 'transaction': [{'id': 'tx-stale'}, {'id': 'tx-keep', 'outcome': 1}], 'budget': [{'user': 1, 'tag': 'old-tag', 'date': '2026-07-01', 'outcome': 10}], 'tag': [{'id': 'old-tag', 'title': 'Old'}]})
        self.assertIn('old-tag', cache.CACHE.tags_by_id())
        transport._apply_received_diff({'serverTimestamp': 2, 'transaction': [{'id': 'tx-keep', 'outcome': 2}], 'budget': [{'user': 1, 'tag': None, 'date': '2026-07-01', 'outcome': 100}, {'user': 1, 'tag': 'new-tag', 'date': '2026-07-01', 'outcome': 200}], 'tag': [{'id': 'new-tag', 'title': 'New'}], 'deletion': [{'object': 'account', 'id': 'account-delete'}]}, force_fetch=['transaction', 'budget', 'tag'])
        saved = {'serverTimestamp': cache.CACHE.server_timestamp, 'transaction': cache.CACHE.transactions()}
        self.assertEqual(saved['serverTimestamp'], 2)
        self.assertEqual(saved['transaction'], [{'id': 'tx-keep', 'outcome': 2}])
        self.assertEqual(sorted(cache.CACHE.data['budget']), ['1:new-tag:2026-07-01', '1:null:2026-07-01'])
        self.assertNotIn('old-tag', cache.CACHE.tags_by_id())
        self.assertIn('new-tag', cache.CACHE.tags_by_id())
        self.assertIsNone(cache.CACHE.get_account('account-delete'))
        self.assertIsNotNone(cache.CACHE.get_account('account-keep'))

    def test_cache_apply_diff_rejects_backward_server_timestamp(self):
        loaded = cache.Cache()
        loaded.server_timestamp = 10
        with self.assertRaises(config.StateStoreError):
            loaded.apply_diff({'serverTimestamp': 9})
        self.assertEqual(loaded.server_timestamp, 10)

    def test_cache_dependent_validation_retries_after_prefetch_sync(self):

        async def fake_sync():
            cache.CACHE.data['tag'] = {'food': {'id': 'food', 'title': 'Food', 'parent': None}}
            cache.CACHE.server_timestamp = 1
            return {}

        async def fake_handler(args):
            return json.dumps({'category_id': args['category_id']})
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'config.json'
            with patch.object(config, '_cfg_path', config_path), patch.object(dispatch, '_sync', AsyncMock(side_effect=fake_sync)), patch.object(dispatch, '_close_client', AsyncMock(return_value=None)):
                result = asyncio.run(dispatch.run_tool('get_reminders', {'category': 'Food'}, {'get_reminders': fake_handler}, lambda: None))
        self.assertEqual(json.loads(result), {'category_id': 'food'})

    def test_analytics_selected_entities_are_revalidated_after_prefetch_sync(self):
        entity_id = '11111111-1111-1111-1111-111111111111'
        cases = {'account': {'store': 'account', 'entity': {'id': entity_id, 'title': 'Account', 'inBalance': True}, 'arguments': {'account_scope': 'selected', 'account_ids': [entity_id]}}, 'category': {'store': 'tag', 'entity': {'id': entity_id, 'title': 'Category', 'parent': None}, 'arguments': {'category_scope': 'selected', 'category_ids': [entity_id]}}, 'merchant': {'store': 'merchant', 'entity': {'id': entity_id, 'title': 'Merchant'}, 'arguments': {'merchant_scope': 'selected', 'merchant_ids': [entity_id]}}}
        for entity_type, case in cases.items():
            with self.subTest(entity_type=entity_type), tempfile.TemporaryDirectory() as temp_dir:
                cache.CACHE.apply_diff({'serverTimestamp': 1, case['store']: [case['entity']]})
                cache.CACHE = cache.Cache()

                async def fake_sync():
                    cache.CACHE.data[case['store']] = {}
                    return {}
                arguments = {'start_date': '2026-07-01', 'end_date': '2026-07-31', 'report': 'outcome', **case['arguments']}
                with patch.object(config, '_cfg_path', Path(temp_dir) / 'config.json'), patch.object(dispatch, '_sync', AsyncMock(side_effect=fake_sync)), patch.object(dispatch, '_close_client', AsyncMock(return_value=None)):
                    result = asyncio.run(dispatch.run_tool('get_analytics', arguments, {'get_analytics': AsyncMock(return_value='{"status":"ok"}')}, lambda: None))
                parsed = json.loads(result)
                self.assertEqual(parsed['code'], 'ENTITY_NOT_FOUND')
                self.assertEqual(parsed['details']['entity_type'], entity_type)

    def test_pure_syntax_validation_error_does_not_sync(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(config, '_cfg_path', Path(temp_dir) / 'config.json'), patch.object(dispatch, '_sync', AsyncMock(return_value=None)) as mocked_sync, patch.object(dispatch, '_close_client', AsyncMock(return_value=None)):
                result = asyncio.run(dispatch.run_tool('get_accounts', {'include_archived': 'false'}, {'get_accounts': AsyncMock(return_value='[]')}, lambda: None))
        parsed = json.loads(result)
        self.assertEqual(parsed['code'], 'INVALID_BOOL')
        mocked_sync.assert_not_awaited()

    def test_setup_budget_mode_is_cache_only_and_writes_config_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'config.json'
            config_path.write_text(json.dumps({'budget_modes': {'stale': {'count_all_movements': True}}}), encoding='utf-8')
            with patch.object(config, '_cfg_path', config_path), patch.object(dispatch, '_sync', AsyncMock(return_value=None)) as mocked_sync, patch.object(dispatch, '_close_client', AsyncMock(return_value=None)):
                result = asyncio.run(dispatch.run_tool('setup_budget_mode', {'mode': 'income_vs_expense'}, {'setup_budget_mode': budget_tools.tool_setup_budget_mode}, lambda: None))
            parsed = json.loads(result)
            saved = json.loads(config_path.read_text(encoding='utf-8'))
        self.assertTrue(parsed['success'])
        self.assertEqual(parsed['plan_balance_mode'], 'EXCLUDE_OPENING_BALANCE')
        self.assertIsNone(parsed['plan_settings'])
        self.assertEqual(parsed['settings_source'], 'unavailable_no_synced_user')
        self.assertEqual(saved['budget_mode'], 'income_vs_expense')
        self.assertNotIn('budget_mode_configured', saved)
        self.assertNotIn('budget_modes', saved)
        mocked_sync.assert_not_awaited()

    def test_account_meta_migration_uses_config_state_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            refs = root / 'references'
            refs.mkdir()
            config_path = root / 'config.json'
            refs.joinpath('account_meta.json').write_text(json.dumps({'acct-1': {'description': 'Main'}}), encoding='utf-8')
            config_path.write_text(json.dumps({'token': 'secret'}), encoding='utf-8')
            with patch.object(config, 'ROOT', root), patch.object(config, '_cfg_path', config_path), patch.object(config, 'write_json_state_atomic', wraps=config.write_json_state_atomic) as write_atomic:
                tools._migrate_account_meta()
            saved = json.loads(config_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['token'], 'secret')
        self.assertEqual(saved['accounts_meta']['acct-1']['description'], 'Main')
        write_atomic.assert_called_once()

    def test_account_meta_migration_fails_closed_on_corrupt_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            refs = root / 'references'
            refs.mkdir()
            config_path = root / 'config.json'
            original = '{"token":'
            config_path.write_text(original, encoding='utf-8')
            refs.joinpath('account_meta.json').write_text(json.dumps({'acct-1': {'description': 'Main'}}), encoding='utf-8')
            with patch.object(config, 'ROOT', root), patch.object(config, '_cfg_path', config_path):
                with self.assertRaises(config.CorruptStateError):
                    tools._migrate_account_meta()
            self.assertEqual(config_path.read_text(encoding='utf-8'), original)
if __name__ == '__main__':
    unittest.main()
