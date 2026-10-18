"""Runtime metadata contracts using synthetic files only; no provider access."""
import importlib
import importlib.util
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from pocket.store import Store


COUNTERS = ('api_calls', 'input_tokens', 'output_tokens', 'cache_read_tokens',
            'cache_write_tokens', 'reasoning_tokens')


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'pocket.sqlite')
        self.addCleanup(self.store.close)
        self.state = self.root / 'state.sqlite'
        with closing(sqlite3.connect(self.state)) as db:
            db.executescript('''
              CREATE TABLE session_model_usage (
                session_id TEXT, billing_provider TEXT, task TEXT,
                api_call_count, input_tokens, output_tokens, cache_read_tokens,
                cache_write_tokens, reasoning_tokens, last_seen,
                billing_base_url TEXT, cost_usd, private_content TEXT);
              CREATE TABLE messages (content TEXT);
              INSERT INTO messages VALUES ('FORBIDDEN_MESSAGE_CANARY');
            ''')
        self.now = 1000

    def owned(self, device, run_id, request_id=None, store=None):
        store = store or self.store
        request_id = request_id or run_id or 'null-run'
        with store.db:
            store.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',
                (device, request_id, 'FORBIDDEN_PAYLOAD_CANARY', 'running', run_id,
                 'FORBIDDEN_RESULT_CANARY', 1))

    def observation(self, run_id, provider='ollama-cloud', task=None, values=None,
                    last_seen=900):
        values = (1, 10, 3, 5, 0, 0) if values is None else values
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (run_id, provider, task, *values, last_seen,
                 'FORBIDDEN_ENDPOINT_CANARY', 999, 'FORBIDDEN_CONTENT_CANARY'))

    def source(self, store=None, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('pocket.runtime_usage'),
                             'optional bounded runtime collector is missing')
        module = importlib.import_module('pocket.runtime_usage')
        source = module.HermesRuntimeUsage(store or self.store, self.state,
                                          clock=lambda: self.now, **kwargs)
        self.addCleanup(source.close)
        return source


class RuntimeUsageTests(RuntimeFixture):
    def test_aggregates_each_explicit_provider_with_main_and_auxiliary_separate(self):
        self.owned('owner', 'one'); self.owned('owner', 'two')
        self.observation('one', 'claude-subscription-directsdk-experimental', values=(2, 20, 6, 10, 0, 1))
        self.observation('one', task='title', values=(1, 4, 2, 0, 0, 0))
        self.observation('two', task='', values=(3, 30, 9, 15, 0, 2), last_seen=950)
        rows = self.source().report('owner')
        self.assertEqual(set(rows), {'codex', 'claude', 'ollama_cloud'})
        local = rows['ollama_cloud']
        self.assertEqual((local['scope'], local['period'], local['source']),
                         ('pocket_runs', 'all_recorded', 'Hermes runtime'))
        self.assertEqual(local['main'], dict(runs=1, api_calls=3, input_tokens=30,
            output_tokens=9, cache_read_tokens=15, cache_write_tokens=0, reasoning_tokens=2))
        self.assertEqual(local['auxiliary']['input_tokens'], 4)
        self.assertEqual(rows['claude']['main']['api_calls'], 2)
        self.assertEqual((local['availability'], local['fetched_at'], local['last_seen']),
                         ('available', 1000, 950))
        self.assertEqual(rows['codex']['availability'], 'unavailable')
        self.assertTrue(all(v is None for v in rows['codex']['main'].values()))
        self.assertTrue(all(v is None for v in rows['claude']['auxiliary'].values()))

    def test_distinct_run_ids_are_scoped_to_device_and_target(self):
        self.owned('owner', 'one'); self.owned('owner', 'one', 'duplicate')
        self.owned('other', 'two'); self.owned('owner', None)
        self.observation('one'); self.observation('two', values=(99, 99, 99, 99, 99, 99))
        self.observation('unrelated', values=(88, 88, 88, 88, 88, 88))
        source = self.source()
        self.assertEqual(source.report('owner')['ollama_cloud']['main']['api_calls'], 1)
        self.assertEqual(source.report('other')['ollama_cloud']['main']['api_calls'], 99)
        second = Store(self.root / 'other-target.sqlite'); self.addCleanup(second.close)
        self.owned('owner', 'different', store=second)
        self.assertEqual(self.source(second).report('owner')['ollama_cloud']['availability'], 'unavailable')

    def test_missing_and_invalid_counters_stay_null_but_observed_zero_survives(self):
        self.owned('owner', 'one')
        self.observation('one', values=(0, None, -1, '123', 0, 1.5), last_seen='bad')
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['availability'], 'partial')
        self.assertEqual(row['main']['api_calls'], 0)
        self.assertEqual(row['main']['cache_write_tokens'], 0)
        for counter in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'reasoning_tokens'):
            self.assertIsNone(row['main'][counter], counter)
        self.assertIsNone(row['last_seen'])

    def test_coverage_exposes_missing_runs_and_unmapped_metadata_without_alias_guessing(self):
        for run in ('mapped', 'missing', 'generic', 'mixed'): self.owned('owner', run)
        self.observation('mapped', 'openai-codex')
        self.observation('generic', 'openai'); self.observation('generic', 'ollama')
        self.observation('mixed', 'ollama-cloud'); self.observation('mixed', 'unknown')
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['availability'], 'partial')
        self.assertEqual(row['coverage'], dict(owned_runs=4, observed_runs=3,
            missing_runs=1, unattributed_runs=2, unmapped_observations=3,
            provider_observed_runs=1))
        self.assertEqual(row['main']['api_calls'], 1)

    def test_empty_observations_are_unknown_and_source_files_remain_untouched(self):
        self.owned('owner', 'one')
        before = {path: path.read_bytes() for path in (self.state, self.root / 'pocket.sqlite')}
        rows = self.source().report('owner')
        for row in rows.values():
            self.assertEqual(row['availability'], 'unavailable')
            self.assertTrue(all(v is None for v in row['main'].values()))
            self.assertEqual(row['coverage']['missing_runs'], 1)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertFalse(any(self.root.glob('*-journal')))

    def test_forbidden_columns_tables_writes_and_extra_attach_are_denied(self):
        self.owned('owner', 'one'); self.observation('one')
        denied = []
        original = sqlite3.connect
        class ProbeConnection(sqlite3.Connection):
            def set_authorizer(connection, callback):
                super().set_authorizer(callback)
                if callback is None: return
                for sql in ('SELECT payload FROM main.tasks', 'SELECT result FROM main.tasks',
                    'SELECT billing_base_url FROM hermes.session_model_usage',
                    'SELECT cost_usd FROM hermes.session_model_usage',
                    'SELECT content FROM hermes.messages',
                    'DELETE FROM main.tasks', "ATTACH DATABASE ':memory:' AS extra",
                    'PRAGMA query_only=OFF', 'PRAGMA writable_schema=ON'):
                    try: connection.execute(sql).fetchone()
                    except sqlite3.DatabaseError: denied.append(sql)
                    else: raise AssertionError('forbidden operation was allowed: ' + sql)
        def connect(*args, **kwargs):
            kwargs['factory'] = ProbeConnection
            self.assertTrue(kwargs.get('uri')); self.assertIn('mode=ro', args[0])
            return original(*args, **kwargs)
        with patch('sqlite3.connect', connect):
            rows = self.source().report('owner')
        self.assertEqual(rows['ollama_cloud']['main']['api_calls'], 1)
        self.assertEqual(len(set(denied)), 9)
        encoded = json.dumps(rows)
        self.assertNotIn('FORBIDDEN', encoded)
        self.assertNotIn('999', encoded)
        self.assertLessEqual(len(encoded.encode()), 8000)

    def test_cache_preserves_true_fetch_time_refreshes_at_fifteen_seconds_and_copies(self):
        self.owned('owner', 'one'); self.observation('one')
        source = self.source()
        first = source.report('owner')
        first['ollama_cloud']['main']['api_calls'] = 77
        self.observation('one', task='title')
        self.now = 1014
        with patch('sqlite3.connect', side_effect=AssertionError('cache hit opened a connection')):
            hit = source.report('owner')
        self.assertEqual(hit['ollama_cloud']['main']['api_calls'], 1)
        self.assertIsNone(hit['ollama_cloud']['auxiliary']['api_calls'])
        self.assertEqual(hit['ollama_cloud']['fetched_at'], 1000)
        self.now = 1015
        self.assertEqual(source.report('owner')['ollama_cloud']['auxiliary']['api_calls'], 1)
        self.assertEqual(source.report('owner')['ollama_cloud']['fetched_at'], 1015)

    def test_read_failure_preserves_last_good_as_stale_and_recovery_refreshes(self):
        self.owned('owner', 'one'); self.observation('one')
        source = self.source(); source.report('owner')
        self.now = 1015
        moved = self.state.with_suffix('.moved'); self.state.rename(moved)
        row = source.report('owner')['ollama_cloud']
        self.assertEqual((row['availability'], row['main']['api_calls'], row['fetched_at']),
                         ('stale', 1, 1000))
        self.assertEqual(row['reason'], 'read_failed')
        self.assertNotIn(str(self.state), json.dumps(row))
        moved.rename(self.state); self.now = 1030
        self.assertEqual(source.report('owner')['ollama_cloud']['availability'], 'available')

    def test_missing_source_or_table_is_safe_failure_without_creating_database(self):
        self.state.unlink()
        source = self.source()
        for row in source.report('owner').values():
            self.assertEqual(row['availability'], 'failed')
            self.assertEqual(row['reason'], 'read_failed')
            self.assertIsNone(row['fetched_at'])
            self.assertIsNone(row['main']['api_calls'])
        self.assertFalse(self.state.exists())
        with closing(sqlite3.connect(self.state)) as db: db.execute('CREATE TABLE unrelated (x)')
        self.now = 1015
        self.assertEqual(source.report('owner')['codex']['availability'], 'failed')

    def test_cache_evicts_the_least_recent_device_at_eight_snapshots(self):
        source = self.source()
        for index in range(8): source.report('device-' + str(index))
        source.report('device-0'); source.report('device-8')
        with patch('sqlite3.connect', side_effect=AssertionError('expected retained cache')):
            self.assertEqual(source.report('device-0')['codex']['fetched_at'], 1000)
        with patch('sqlite3.connect', side_effect=sqlite3.OperationalError('PRIVATE_PATH')):
            self.assertEqual(source.report('device-1')['codex']['availability'], 'failed')

    def test_busy_database_fails_within_bounded_deadline_and_closes_connection(self):
        self.owned('owner', 'one'); self.observation('one')
        source = self.source(timeout=0.1)
        with closing(sqlite3.connect(self.state)) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            started = time.monotonic()
            row = source.report('owner')['ollama_cloud']
            elapsed = time.monotonic() - started
            self.assertEqual(row['availability'], 'failed')
            self.assertLess(elapsed, 0.5)
        self.now = 1015
        self.assertEqual(source.report('owner')['ollama_cloud']['availability'], 'available')
        self.state.rename(self.state.with_suffix('.closed'))

    def test_query_deadline_aborts_instead_of_returning_a_truncated_aggregate(self):
        self.owned('owner', 'one')
        with closing(sqlite3.connect(self.state)) as db, db:
            db.executemany('INSERT INTO session_model_usage (session_id,billing_provider) VALUES (?,?)',
                           (('one', 'ollama-cloud') for _ in range(30000)))
        source = self.source(timeout=0.002)
        started = time.monotonic()
        row = source.report('owner')['ollama_cloud']
        self.assertEqual(row['availability'], 'failed')
        self.assertIsNone(row['main']['runs'])
        self.assertLess(time.monotonic() - started, 0.5)

    def test_oversized_or_mixed_valid_counters_never_overflow_or_undercount(self):
        self.owned('owner', 'one')
        self.observation('one', values=(1, 2**53 - 1, 2**53, 1, 0, 0))
        self.observation('one', values=(1, 1, 0, None, 0, 0))
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['main']['api_calls'], 2)
        self.assertEqual(row['main']['cache_write_tokens'], 0)
        for key in ('input_tokens', 'output_tokens', 'cache_read_tokens'):
            self.assertIsNone(row['main'][key])
        self.assertEqual(row['availability'], 'partial')

    def test_close_is_idempotent_clears_snapshots_and_prevents_further_reads(self):
        source = self.source(); source.report('owner'); source.close(); source.close()
        with patch('sqlite3.connect', side_effect=AssertionError('closed source opened database')):
            row = source.report('owner')['codex']
        self.assertEqual(row['availability'], 'failed')
        self.assertEqual(row['reason'], 'closed')

    def test_configuration_rejects_nonabsolute_or_remote_paths_and_unsafe_budgets(self):
        module_source = self.source()
        module = importlib.import_module('pocket.runtime_usage')
        for path in ('relative.sqlite', ':memory:', 'file:/private?mode=rw', '//host/share/state', ''):
            with self.subTest(path=path), self.assertRaises(ValueError):
                module.HermesRuntimeUsage(self.store, path)
        for timeout in (0, -1, 0.501, True, float('nan')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.source(timeout=timeout)
        self.assertTrue(module_source.matches_store(self.store))

    def test_schema_views_cannot_expand_the_allowed_read_boundary(self):
        self.owned('owner', 'one')
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('ALTER TABLE session_model_usage RENAME TO private_metadata')
            db.execute('CREATE VIEW session_model_usage AS SELECT * FROM private_metadata')
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['availability'], 'failed')
        self.assertNotIn('private_metadata', json.dumps(row))

    def test_fractional_epoch_is_bounded_to_integer_seconds_and_empty_task_is_main(self):
        self.owned('owner', 'one')
        self.observation('one', task='', last_seen=900.875)
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['last_seen'], 900)
        self.assertEqual(row['main']['runs'], 1)
        self.assertIsNone(row['auxiliary']['runs'])

    def test_all_recorded_period_has_no_silent_row_limit_or_double_counted_provider_runs(self):
        self.owned('owner', 'one')
        with closing(sqlite3.connect(self.state)) as db, db:
            db.executemany('''INSERT INTO session_model_usage
                (session_id,billing_provider,task,api_call_count,input_tokens,output_tokens,
                 cache_read_tokens,cache_write_tokens,reasoning_tokens,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                [('one', 'ollama-cloud', None, 1, 1, 1, 1, 0, 0, 900)] * 100)
        self.observation('one', task='title')
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['main']['api_calls'], 100)
        self.assertEqual(row['main']['runs'], 1)
        self.assertEqual(row['auxiliary']['runs'], 1)
        self.assertEqual(row['coverage']['provider_observed_runs'], 1)


class RuntimeSchemaBoundaryTests(RuntimeFixture):
    FIELDS = ('session_id', 'billing_provider', 'task', 'api_call_count',
              'input_tokens', 'output_tokens', 'cache_read_tokens',
              'cache_write_tokens', 'reasoning_tokens', 'last_seen')

    def metadata(self, name, *, generated=None, mode='VIRTUAL', collations=None, rows=None):
        self.state = self.root / (name + '.sqlite')
        collations = collations or {}
        definitions = []
        for field in self.FIELDS:
            if field == generated:
                definitions.append(field + ' GENERATED ALWAYS AS (private_content) ' + mode)
            else:
                definitions.append(field + (' TEXT COLLATE ' + collations[field]
                    if field in collations else
                    (' TEXT' if field in ('session_id', 'billing_provider', 'task') else '')))
        with closing(sqlite3.connect(self.state)) as db, db:
            db.create_collation('SCOPE_EQ', lambda left, right: 0)
            db.execute('CREATE TABLE session_model_usage (' + ','.join(definitions) +
                       ',private_content)')
            if rows is None:
                values = dict(zip(self.FIELDS, ('one', 'ollama-cloud', None,
                                               1, 10, 3, 5, 0, 0, 900)))
                values['private_content'] = values.pop(generated) if generated else 'PRIVATE_CANARY'
                if generated == 'api_call_count': values['private_content'] = 777
                if generated == 'task': values['private_content'] = 'title'
                fields = tuple(values)
                db.execute('INSERT INTO session_model_usage (' + ','.join(fields) +
                           ') VALUES (' + ','.join('?' for _ in fields) + ')', tuple(values.values()))
            else:
                db.executemany('INSERT INTO session_model_usage (' + ','.join(self.FIELDS) +
                    ') VALUES (' + ','.join('?' for _ in self.FIELDS) + ')', rows)

    def scope(self, *, generated=None, mode='VIRTUAL', collations=None, rows=None):
        collations = collations or {}
        definitions = []
        for field in ('device', 'run_id'):
            if field == generated:
                definitions.append(field + ' TEXT GENERATED ALWAYS AS (private_content) ' + mode)
            else:
                definitions.append(field + ' TEXT COLLATE ' + collations.get(field, 'BINARY'))
        with self.store.db:
            self.store.db.create_collation('SCOPE_EQ', lambda left, right: 0)
            self.store.db.execute('DROP TABLE tasks')
            self.store.db.execute('CREATE TABLE tasks (' + ','.join(definitions) + ',private_content)')
            if generated:
                other = 'run_id' if generated == 'device' else 'device'
                self.store.db.execute('INSERT INTO tasks (' + other + ',private_content) VALUES (?,?)',
                    ('one' if other == 'run_id' else 'owner', 'owner' if generated == 'device' else 'one'))
            else:
                self.store.db.executemany('INSERT INTO tasks (device,run_id) VALUES (?,?)',
                                         rows or [('owner', 'one')])

    def test_generated_metadata_fields_fail_closed_for_virtual_and_stored_columns(self):
        self.owned('owner', 'one')
        for mode in ('VIRTUAL', 'STORED'):
            for field in self.FIELDS:
                with self.subTest(mode=mode, field=field):
                    self.metadata('generated-' + mode + '-' + field, generated=field, mode=mode)
                    rows = self.source().report('owner')
                    for row in rows.values():
                        self.assertEqual(row['availability'], 'failed')
                        self.assertIsNone(row['main']['api_calls'])
                    self.assertNotIn('777', json.dumps(rows))

    def test_generated_device_and_run_scope_fields_fail_closed(self):
        for mode in ('VIRTUAL', 'STORED'):
            for field in ('device', 'run_id'):
                with self.subTest(mode=mode, field=field):
                    self.scope(generated=field, mode=mode)
                    self.metadata('scope-' + mode + '-' + field)
                    row = self.source().report('owner')['ollama_cloud']
                    self.assertEqual(row['availability'], 'failed')
                    self.assertIsNone(row['main']['api_calls'])

    def test_provider_alias_comparisons_ignore_nocase_and_rtrim_column_collations(self):
        self.owned('owner', 'one')
        for collation in ('NOCASE', 'RTRIM'):
            for index, alias in enumerate(('openai-codex',
                    'claude-subscription-directsdk-experimental', 'ollama-cloud')):
                with self.subTest(collation=collation, alias=alias):
                    spelling = alias.upper() if collation == 'NOCASE' else alias + ' '
                    self.metadata('alias-' + collation + '-' + str(index),
                        collations={'billing_provider': collation},
                        rows=[('one', spelling, None, 1, 10, 3, 5, 0, 0, 900)])
                    rows = self.source().report('owner')
                    self.assertTrue(all(row['availability'] == 'unavailable' for row in rows.values()))
                    self.assertEqual(rows['ollama_cloud']['coverage']['unmapped_observations'], 1)

    def test_task_whitespace_does_not_become_main_under_rtrim(self):
        self.owned('owner', 'one')
        self.metadata('task-rtrim', collations={'task': 'RTRIM'},
            rows=[('one', 'ollama-cloud', ' ', 1, 10, 3, 5, 0, 0, 900)])
        row = self.source().report('owner')['ollama_cloud']
        self.assertIsNone(row['main']['api_calls'])
        self.assertEqual(row['auxiliary']['api_calls'], 1)

    def test_device_equality_never_expands_to_case_or_space_variants(self):
        for collation, other in (('NOCASE', 'OWNER'), ('RTRIM', 'owner ')):
            with self.subTest(collation=collation):
                self.scope(collations={'device': collation}, rows=[(other, 'one')])
                self.metadata('device-' + collation)
                row = self.source().report('owner')['ollama_cloud']
                self.assertEqual(row['availability'], 'unavailable')
                self.assertEqual(row['coverage']['owned_runs'], 0)

    def test_run_join_never_matches_case_or_space_variants(self):
        for collation, run in (('NOCASE', 'ONE'), ('RTRIM', 'one ')):
            with self.subTest(collation=collation):
                self.scope(collations={'run_id': collation}, rows=[('owner', run)])
                self.metadata('join-' + collation, collations={'session_id': collation})
                row = self.source().report('owner')['ollama_cloud']
                self.assertEqual(row['availability'], 'unavailable')
                self.assertEqual(row['coverage']['missing_runs'], 1)

    def test_run_and_observation_distinct_counts_preserve_binary_identity(self):
        for collation, second in (('NOCASE', 'ONE'), ('RTRIM', 'one ')):
            with self.subTest(collation=collation):
                self.scope(collations={'run_id': collation},
                           rows=[('owner', 'one'), ('owner', second)])
                self.metadata('distinct-' + collation, collations={'session_id': collation},
                    rows=[('one', 'ollama-cloud', None, 1, 10, 3, 5, 0, 0, 900),
                          (second, 'ollama-cloud', None, 2, 10, 3, 5, 0, 0, 900)])
                row = self.source().report('owner')['ollama_cloud']
                self.assertEqual(row['availability'], 'available')
                self.assertEqual(row['main']['api_calls'], 3)
                self.assertEqual(row['main']['runs'], 2)
                self.assertEqual(row['coverage']['owned_runs'], 2)
                self.assertEqual(row['coverage']['observed_runs'], 2)
                self.assertEqual(row['coverage']['provider_observed_runs'], 2)

    def test_exact_queries_do_not_require_or_invoke_source_custom_collations(self):
        self.scope(collations={'device': 'SCOPE_EQ', 'run_id': 'SCOPE_EQ'})
        self.metadata('custom-collation', collations=dict.fromkeys(
            ('session_id', 'billing_provider', 'task'), 'SCOPE_EQ'))
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['availability'], 'available')
        self.assertEqual(row['main']['api_calls'], 1)

    def test_literal_metadata_view_is_rejected_as_a_nonordinary_table(self):
        self.owned('owner', 'one')
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('DROP TABLE session_model_usage')
            db.execute('''CREATE VIEW session_model_usage AS SELECT 'one' AS session_id,
                'ollama-cloud' AS billing_provider,NULL AS task,777 AS api_call_count,
                1 AS input_tokens,1 AS output_tokens,1 AS cache_read_tokens,
                1 AS cache_write_tokens,1 AS reasoning_tokens,900 AS last_seen''')
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['availability'], 'failed')
        self.assertIsNone(row['main']['api_calls'])

    def test_numeric_metadata_identity_never_matches_text_with_different_bytes(self):
        self.owned('owner', '01')
        self.metadata('numeric-session')
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('DROP TABLE session_model_usage')
            db.execute('''CREATE TABLE session_model_usage (session_id INTEGER,
                billing_provider TEXT,task TEXT,api_call_count,input_tokens,
                output_tokens,cache_read_tokens,cache_write_tokens,reasoning_tokens,last_seen)''')
            db.execute('INSERT INTO session_model_usage VALUES (1,?,?,?,?,?,?,?,?,?)',
                       ('ollama-cloud', None, 777, 1, 1, 1, 1, 1, 900))
        row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(row['availability'], 'failed')
        self.assertIsNone(row['main']['api_calls'])

    def test_numeric_device_and_run_storage_do_not_gain_text_identity(self):
        self.metadata('numeric-scope')
        for field in ('device', 'run_id'):
            with self.subTest(field=field):
                with self.store.db:
                    self.store.db.execute('DROP TABLE tasks')
                    self.store.db.execute('CREATE TABLE tasks (device ' +
                        ('INTEGER' if field == 'device' else 'TEXT') + ',run_id ' +
                        ('INTEGER' if field == 'run_id' else 'TEXT') + ')')
                    self.store.db.execute('INSERT INTO tasks VALUES (?,?)',
                        (1, 'one') if field == 'device' else ('owner', 1))
                device = '01' if field == 'device' else 'owner'
                row = self.source().report(device)['ollama_cloud']
                self.assertEqual(row['availability'], 'failed')
                self.assertIsNone(row['main']['api_calls'])

    def test_nonregular_state_is_rejected_before_any_sqlite_open(self):
        self.state = self.root
        source = self.source()
        opened = []
        original = sqlite3.connect
        def connect(*args, **kwargs):
            opened.append(args[0])
            return original(*args, **kwargs)
        with patch('sqlite3.connect', connect):
            row = source.report('owner')['ollama_cloud']
        self.assertEqual(row['availability'], 'failed')
        self.assertEqual(opened, [])

    def test_schema_validation_and_data_reads_hold_the_same_read_transaction(self):
        self.owned('owner', 'one'); self.observation('one')
        state = self.state
        attempted = []
        original = sqlite3.connect
        class RaceConnection(sqlite3.Connection):
            def set_authorizer(connection, callback):
                super().set_authorizer(callback)
                if callback is None or callback.__name__ != '_authorize': return
                with closing(original(state, timeout=0.005)) as writer:
                    try:
                        writer.execute('ALTER TABLE session_model_usage DROP COLUMN api_call_count')
                    except sqlite3.OperationalError:
                        attempted.append('locked')
                    else:
                        attempted.append('changed')
        def connect(*args, **kwargs):
            kwargs['factory'] = RaceConnection
            return original(*args, **kwargs)
        with patch('sqlite3.connect', connect):
            row = self.source().report('owner')['ollama_cloud']
        self.assertEqual(attempted, ['locked'])
        self.assertEqual(row['availability'], 'available')
        self.assertEqual(row['main']['api_calls'], 1)

    def test_persisted_objects_cannot_shadow_native_schema_metadata_interfaces(self):
        self.owned('owner', 'one'); self.observation('one')
        native_calls = []
        original = sqlite3.connect
        for database in (self.store.db,):
            with database:
                database.executescript('''
                  CREATE TABLE pragma_table_list (schema TEXT,name TEXT,private_content TEXT,
                    type TEXT GENERATED ALWAYS AS (
                      CASE WHEN CAST(private_content AS INTEGER)=777 THEN 'table' ELSE 'view' END) VIRTUAL);
                  INSERT INTO pragma_table_list (schema,name,private_content) VALUES
                    ('main','tasks','777 PRIVATE_CANARY'),
                    ('hermes','session_model_usage','777 PRIVATE_CANARY');
                  CREATE TABLE pragma_table_xinfo (name TEXT,type TEXT,hidden,private_content TEXT);
                ''')
        with closing(original(self.state)) as db, db:
            db.executescript('''
              CREATE TABLE pragma_table_list (schema TEXT,name TEXT,private_content TEXT,
                type TEXT GENERATED ALWAYS AS (
                  CASE WHEN CAST(private_content AS INTEGER)=777 THEN 'table' ELSE 'view' END) VIRTUAL);
              INSERT INTO pragma_table_list (schema,name,private_content) VALUES
                ('main','tasks','777 PRIVATE_CANARY'),
                ('hermes','session_model_usage','777 PRIVATE_CANARY');
              CREATE TABLE pragma_table_xinfo (name TEXT,type TEXT,hidden,private_content TEXT);
            ''')
        class ProbeConnection(sqlite3.Connection):
            def set_authorizer(connection, callback):
                if callback is None: return super().set_authorizer(None)
                def guarded(action, table, column, database, context):
                    if action == sqlite3.SQLITE_PRAGMA and table in ('table_list', 'table_xinfo'):
                        native_calls.append((table, column, database))
                    return callback(action, table, column, database, context)
                super().set_authorizer(guarded)
        def connect(*args, **kwargs):
            kwargs['factory'] = ProbeConnection
            return original(*args, **kwargs)
        for private in ('777 PRIVATE_CANARY', '888 PRIVATE_CANARY'):
            with self.store.db:
                self.store.db.execute('UPDATE pragma_table_list SET private_content=?', (private,))
            with closing(original(self.state)) as db, db:
                db.execute('UPDATE pragma_table_list SET private_content=?', (private,))
            native_calls.clear()
            with patch('sqlite3.connect', connect):
                row = self.source().report('owner')['ollama_cloud']
            self.assertEqual(row['availability'], 'available')
            self.assertEqual(row['main']['api_calls'], 1)
            # SQLite labels these native virtual-table READs as main even when
            # SQL explicitly uses temp. Actual PRAGMA calls prove the interface;
            # stored generated canaries in both schemas cannot affect results.
            self.assertEqual(native_calls.count(('table_list', None, None)), 2)
            self.assertIn(('table_xinfo', 'tasks', 'main'), native_calls)
            self.assertIn(('table_xinfo', 'session_model_usage', 'hermes'), native_calls)
            self.assertEqual(len(native_calls), 4)

    @unittest.skipUnless(os.name == 'posix', 'POSIX FIFO open semantics')
    def test_fifo_sources_fail_within_outer_deadline_without_sqlite_open_hang(self):
        self.owned('owner', 'one')
        fifo = self.root / 'fifo.sqlite'; os.mkfifo(fifo)
        code = '''
import os,sys
from pocket.runtime_usage import HermesRuntimeUsage
from pocket.store import Store
store=Store(sys.argv[1])
try:
 source=HermesRuntimeUsage(store,sys.argv[2],timeout=0.1)
 try:
  if sys.argv[3]=='pocket':
   os.rename(sys.argv[1],sys.argv[1]+'.moved')
   os.mkfifo(sys.argv[1])
  assert source.report('owner')['ollama_cloud']['availability']=='failed'
 finally: source.close()
finally: store.close()
'''
        for role in ('hermes', 'pocket'):
            with self.subTest(role=role):
                result = subprocess.run([sys.executable, '-B', '-c', code,
                    str(self.root / 'pocket.sqlite'),
                    str(fifo if role == 'hermes' else self.state), role],
                    timeout=2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
