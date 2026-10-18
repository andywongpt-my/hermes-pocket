"""Optional, bounded Pocket-run counters from explicit local Hermes metadata.

These counters describe recorded activity, never account quota, cost or resets.
No account credentials, endpoint discovery, model requests or background polling.
"""
from collections import OrderedDict
from copy import deepcopy
import math
from pathlib import Path
import sqlite3
import stat
import threading
import time

from .schema import identifier
from .usage import PROVIDERS


MAX_COUNTER = 2**53 - 1
MAX_TIMESTAMP = 253402300799
COUNTERS = ('api_calls', 'input_tokens', 'output_tokens', 'cache_read_tokens',
            'cache_write_tokens', 'reasoning_tokens')
_COLUMNS = ('api_call_count', *COUNTERS[1:])
_SOURCE_FIELDS = {
    ('main', 'tasks'): ('device', 'run_id'),
    ('hermes', 'session_model_usage'): (
        'session_id', 'billing_provider', 'task', 'last_seen', *_COLUMNS)}
_IDENTITY_FIELDS = {('main', 'tasks'): {'device', 'run_id'},
                    ('hermes', 'session_model_usage'): {'session_id'}}
# Only independently confirmed billing-provider aliases; generic openai/ollama
# labels do not establish subscription identity or cloud routing.
_ALIASES = {'openai-codex': 'codex',
            'claude-subscription-directsdk-experimental': 'claude',
            'ollama-cloud': 'ollama_cloud'}
_PROVIDER_CASE = 'CASE u.billing_provider COLLATE BINARY ' + ' '.join(
    "WHEN '" + alias + "' THEN '" + provider + "'"
    for alias, provider in _ALIASES.items()) + " ELSE 'unmapped' END"
_COUNTER_SELECT = ', '.join(
    f"CASE WHEN typeof(u.{column})='integer' AND u.{column} BETWEEN 0 AND {MAX_COUNTER} "
    f'THEN u.{column} ELSE NULL END AS {counter}'
    for column, counter in zip(_COLUMNS, COUNTERS))
_CTE = f'''
  WITH owned AS MATERIALIZED (
    SELECT DISTINCT run_id COLLATE BINARY AS run_id FROM main.tasks
    WHERE device COLLATE BINARY=? AND typeof(device)='text' AND typeof(run_id)='text'
  ), observations AS (
    SELECT u.session_id COLLATE BINARY AS session_id, {_PROVIDER_CASE} AS provider,
      CASE WHEN u.task IS NULL OR u.task COLLATE BINARY=''
           THEN 'main' ELSE 'auxiliary' END AS phase,
      {_COUNTER_SELECT},
      CASE WHEN typeof(u.last_seen) IN ('integer','real')
                AND u.last_seen BETWEEN 0 AND {MAX_TIMESTAMP}
           THEN CAST(u.last_seen AS INTEGER) ELSE NULL END AS last_seen
    FROM hermes.session_model_usage AS u JOIN owned
      ON owned.run_id COLLATE BINARY=u.session_id COLLATE BINARY
    WHERE typeof(u.session_id)='text'
  )
'''
_COVERAGE_SQL = _CTE + '''
  SELECT (SELECT count(run_id) FROM owned) AS owned_runs,
    count(DISTINCT session_id) AS observed_runs,
    count(DISTINCT CASE WHEN provider='unmapped' THEN session_id END) AS unattributed_runs,
    count(CASE WHEN provider='unmapped' THEN session_id END) AS unmapped_observations,
''' + ', '.join(f"count(DISTINCT CASE WHEN provider='{provider}' THEN session_id END) AS {provider}"
                 for provider in PROVIDERS) + ' FROM observations'
_AGGREGATE_SQL = _CTE + '''
  SELECT provider, phase, count(DISTINCT session_id) AS runs,
''' + ', '.join(f'runtime_sum({counter}) AS {counter}' for counter in COUNTERS) + ''',
    max(last_seen) AS last_seen,
    count(CASE WHEN last_seen IS NULL THEN session_id END) AS missing_time
  FROM observations GROUP BY provider, phase
'''


def _bounded(value, maximum=MAX_COUNTER):
    return value if type(value) is int and 0 <= value <= maximum else None


class _Sum:
    """Unknown input or an overflowing total stays unknown, never a partial sum."""
    def __init__(self):
        self.value = 0

    def step(self, value):
        if self.value is not None:
            if value is None or self.value + value > MAX_COUNTER:
                self.value = None
            else:
                self.value += value

    def finalize(self):
        return self.value


def _local_path(value):
    if not isinstance(value, (str, Path)):
        raise ValueError('explicit absolute local database path required')
    text = str(value)
    if not text or len(text) > 4096 or any(ord(c) < 32 for c in text):
        raise ValueError('explicit absolute local database path required')
    path = Path(text)
    if not path.is_absolute() or text.startswith(('//', '\\\\')):
        raise ValueError('explicit absolute local database path required')
    return path.resolve()


def _store_path(store):
    with store.lock:
        rows = store.db.execute('PRAGMA database_list').fetchall()
    filename = next((row[2] for row in rows if row[1] == 'main'), '')
    return _local_path(filename)


def _require_regular(path):
    # SQLite deadlines cannot interrupt an OS open blocked on a FIFO/device.
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('unsupported runtime source')


def _authorize(action, table, column, database, context):
    if action == sqlite3.SQLITE_READ:
        if context not in (None, 'owned', 'observations'):
            return sqlite3.SQLITE_DENY
        return (sqlite3.SQLITE_OK if column in _SOURCE_FIELDS.get((database, table), ())
                else sqlite3.SQLITE_DENY)
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and column in ('count', 'max', 'typeof', 'runtime_sum'):
        return sqlite3.SQLITE_OK
    if (action == sqlite3.SQLITE_PRAGMA and table == 'busy_timeout' and
            isinstance(column, str) and column.isdecimal() and 0 <= int(column) <= 500):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _authorize_schema(action, table, column, database, context):
    """A separate metadata-only phase, with no application table reads."""
    if action == sqlite3.SQLITE_READ:
        allowed = {'pragma_table_list': {'type', 'schema', 'name'},
                   'pragma_table_xinfo': {'name', 'type', 'hidden'}}
        # SQLite labels native eponymous-PRAGMA READ callbacks as main, even
        # though the trusted metadata SQL below explicitly qualifies temp.
        return (sqlite3.SQLITE_OK if database == 'main' and context is None and
                column in allowed.get(table, ()) else sqlite3.SQLITE_DENY)
    if action == sqlite3.SQLITE_PRAGMA:
        if table == 'table_list' and column is None and database is None:
            return sqlite3.SQLITE_OK
        if table == 'table_xinfo' and (database, column) in _SOURCE_FIELDS:
            return sqlite3.SQLITE_OK
    return _authorize(action, table, column, database, context)


def _validate_schema(execute):
    # SQLITE_READ does not reveal generated-column expression dependencies.
    # Validate ordinary fields before any data SELECT, in the same transaction.
    # Fresh connections have no application temp objects: qualifying the native
    # interfaces prevents persisted main/hermes objects from shadowing them.
    # Projections return only fixed table types and allowlisted field metadata;
    # expressions and private columns are not read.
    for (database, table), fields in _SOURCE_FIELDS.items():
        kinds = execute('''SELECT type FROM temp.pragma_table_list
            WHERE schema=? COLLATE BINARY AND name=? COLLATE BINARY''',
            (database, table)).fetchmany(2)
        if len(kinds) != 1 or kinds[0]['type'] != 'table':
            raise ValueError('unsupported runtime schema')
        marks = ','.join('?' for _ in fields)
        rows = execute('''SELECT name,type,hidden FROM temp.pragma_table_xinfo(?,?)
            WHERE name COLLATE BINARY IN (''' + marks + ')',
            (table, database, *fields)).fetchmany(len(fields) + 1)
        if len(rows) != len(fields) or {row['name'] for row in rows} != set(fields):
            raise ValueError('unsupported runtime schema')
        for row in rows:
            if row['hidden'] != 0:
                raise ValueError('unsupported runtime schema')
            if row['name'] in _IDENTITY_FIELDS[(database, table)]:
                declared = row['type'].upper()
                if 'INT' in declared or not any(part in declared for part in ('CHAR', 'CLOB', 'TEXT')):
                    raise ValueError('unsupported runtime schema')


def _blank(availability='unavailable', reason=None):
    row = {'scope': 'pocket_runs', 'period': 'all_recorded', 'source': 'Hermes runtime',
           'availability': availability, 'fetched_at': None, 'last_seen': None,
           'main': dict.fromkeys(('runs', *COUNTERS)),
           'auxiliary': dict.fromkeys(('runs', *COUNTERS)),
           'coverage': dict.fromkeys(('owned_runs', 'observed_runs', 'missing_runs',
               'unattributed_runs', 'unmapped_observations', 'provider_observed_runs'))}
    if reason is not None:
        row['reason'] = reason
    return row


class HermesRuntimeUsage:
    """One target's read-only source, with at most eight device snapshots for 15s.

    Each refresh opens and closes its own SQLite connection. Global coverage is
    device scoped: observed means any matching metadata; unattributed means a
    run has at least one unmapped observation, including mixed-provider runs.
    provider_observed_runs is separately counted across main and auxiliary.
    """
    def __init__(self, store, state_db, *, clock=time.time, timeout=0.125):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                not math.isfinite(timeout) or not 0 < timeout <= 0.5):
            raise ValueError('bounded runtime query timeout required')
        self.pocket_db = _store_path(store)
        self.state_db = _local_path(state_db)
        self.clock = clock
        self.timeout = timeout
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._closed = False

    def matches_store(self, store):
        return self.pocket_db == _store_path(store)

    def close(self):
        with self._lock:
            self._closed = True
            self._cache.clear()

    def report(self, device):
        identifier(device)
        deadline = time.monotonic() + self.timeout
        if not self._lock.acquire(timeout=self.timeout):
            return {provider: _blank('failed', 'read_failed') for provider in PROVIDERS}
        try:
            if self._closed:
                return {provider: _blank('failed', 'closed') for provider in PROVIDERS}
            now = self.clock()
            entry = self._cache.get(device)
            if entry is not None and 0 <= now - entry['checked_at'] < 15:
                self._cache.move_to_end(device)
                return deepcopy(entry['snapshot'])
            try:
                snapshot = self._read(device, deadline)
                last_good = snapshot
            except (sqlite3.Error, OSError, TimeoutError, ValueError, OverflowError):
                last_good = entry['last_good'] if entry else None
                snapshot = deepcopy(last_good) if last_good is not None else {
                    provider: _blank('failed', 'read_failed') for provider in PROVIDERS}
                for row in snapshot.values():
                    row['availability'] = ('stale' if row['availability'] in
                        ('available', 'partial', 'stale') else 'failed')
                    row['reason'] = 'read_failed'
            self._cache[device] = {'checked_at': self.clock(), 'snapshot': snapshot,
                                   'last_good': last_good}
            self._cache.move_to_end(device)
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
            return deepcopy(snapshot)
        finally:
            self._lock.release()

    def _read(self, device, deadline):
        db = None
        def execute(sql, parameters=()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('runtime query deadline')
            db.execute('PRAGMA busy_timeout=' + str(max(0, int(remaining * 1000))))
            return db.execute(sql, parameters)
        try:
            _require_regular(self.pocket_db)
            _require_regular(self.state_db)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('runtime query deadline')
            db = sqlite3.connect(self.pocket_db.as_uri() + '?mode=ro', uri=True,
                                 timeout=remaining, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
            db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 32768)
            db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 1)
            execute('PRAGMA query_only=ON')
            execute('PRAGMA trusted_schema=OFF')
            execute('PRAGMA temp_store=MEMORY')
            _require_regular(self.state_db)
            execute('ATTACH DATABASE ? AS hermes', (self.state_db.as_uri() + '?mode=ro',))
            execute('BEGIN')
            db.create_aggregate('runtime_sum', 1, _Sum)
            db.set_authorizer(_authorize_schema)
            _validate_schema(execute)
            db.set_authorizer(_authorize)
            coverage = execute(_COVERAGE_SQL, (device,)).fetchone()
            aggregates = execute(_AGGREGATE_SQL, (device,)).fetchmany(9)
            if len(aggregates) > 8 or time.monotonic() >= deadline:
                raise TimeoutError('runtime query deadline')
            snapshot = {provider: _blank() for provider in PROVIDERS}
            fetched_at = _bounded(int(self.clock()), MAX_TIMESTAMP)
            for provider, row in snapshot.items():
                row['fetched_at'] = fetched_at
                row['coverage'] = {key: _bounded(coverage[key]) for key in (
                    'owned_runs', 'observed_runs', 'unattributed_runs', 'unmapped_observations')}
                row['coverage']['missing_runs'] = _bounded(
                    coverage['owned_runs'] - coverage['observed_runs'])
                row['coverage']['provider_observed_runs'] = _bounded(coverage[provider])
            for aggregate in aggregates:
                provider = aggregate['provider']
                if provider not in snapshot:
                    continue
                row = snapshot[provider]
                values = {key: _bounded(aggregate[key]) for key in ('runs', *COUNTERS)}
                row[aggregate['phase']] = values
                stamp = _bounded(aggregate['last_seen'], MAX_TIMESTAMP)
                if stamp is not None:
                    row['last_seen'] = max(row['last_seen'] or 0, stamp)
                partial = (any(value is None for value in values.values()) or
                    aggregate['missing_time'] or fetched_at is None or
                    coverage['owned_runs'] != coverage['observed_runs'] or
                    coverage['unmapped_observations'])
                if partial or row['availability'] == 'partial':
                    row['availability'] = 'partial'
                    row['reason'] = 'incomplete_metadata'
                else:
                    row['availability'] = 'available'
            return snapshot
        finally:
            if db is not None:
                db.close()
