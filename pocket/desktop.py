"""Durable adapter for Hermes Desktop's /api/ws JSON-RPC contract.

Admission is journaled before any upstream mutation. No automatic prompt
retry, session resume, approval answer or credential collection occurs here.
"""
import hashlib
import json
import sqlite3
import threading
import uuid
from .store import Conflict, NotFound, TERMINAL


class DesktopAdapter:
    def __init__(self, rpc, database):
        self.rpc = rpc
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(database), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS desktop_runs (
            run_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL, runtime_id TEXT, stored_id TEXT,
            state TEXT NOT NULL, result TEXT, last_seq INTEGER NOT NULL DEFAULT 0,
            epoch TEXT)''')
        self.db.commit()

    def close(self):
        self.db.close()

    def check(self):
        result = self.rpc.call('ping', {})
        if result.get('pong') is not True:
            raise ValueError('Desktop liveness probe failed')

    def _row(self, run_id):
        row = self.db.execute('SELECT * FROM desktop_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise NotFound('Desktop run not found')
        return dict(row)

    def _state(self, run_id, state, result=None):
        row = self._row(run_id)
        if row['state'] in TERMINAL:
            return
        if result is not None and (not isinstance(result, str) or len(result.encode('utf-8')) > 65536):
            state, result = 'failed', 'Response exceeds the Pocket text limit.'
        with self.db:
            self.db.execute('UPDATE desktop_runs SET state=?, result=COALESCE(?,result) WHERE run_id=?',
                            (state, result, run_id))

    def create(self, text, key, history=None):
        payload = json.dumps({'text': text, 'history': history or []}, ensure_ascii=False,
                             sort_keys=True, separators=(',', ':'))
        with self.lock:
            with self.db:
                old = self.db.execute('SELECT * FROM desktop_runs WHERE request_key=?', (key,)).fetchone()
                if old:
                    if old['payload'] != payload:
                        raise Conflict('request key already used')
                    return old['run_id']
                run_id = 'desktop_' + uuid.uuid4().hex
                self.db.execute('INSERT INTO desktop_runs(run_id,request_key,payload,state) VALUES(?,?,?,?)',
                                (run_id, key, payload, 'unknown'))
            try:
                session = self.rpc.call('session.create', {
                    'source': 'pocket', 'title': 'Hermes Pocket',
                    'messages': history or [], 'close_on_disconnect': False,
                    'idempotency_key': 'pocket_' + hashlib.sha256(key.encode()).hexdigest()})
                runtime, stored = session['session_id'], session['stored_session_id']
                if not all(isinstance(x, str) and 0 < len(x) <= 256 for x in (runtime, stored)):
                    raise ValueError('invalid Desktop identity')
                with self.db:
                    self.db.execute('UPDATE desktop_runs SET runtime_id=?,stored_id=? WHERE run_id=?',
                                    (runtime, stored, run_id))
                ack = self.rpc.call('prompt.submit', {'session_id': runtime, 'text': text})
                if ack.get('status') in ('streaming', 'steered'):
                    self._state(run_id, 'running')
                elif ack.get('status') == 'queued':
                    self._state(run_id, 'queued_upstream')
            except Exception:
                # May have submitted successfully. Retain ID and uncertainty.
                self._state(run_id, 'unknown')
            return run_id

    def _event(self, params):
        if not isinstance(params, dict):
            return
        sid = params.get('session_id')
        rows = list(self.db.execute('SELECT run_id,state FROM desktop_runs WHERE runtime_id=?', (sid,)))
        payload = params.get('payload', {})
        if not isinstance(payload, dict):
            return
        for row in rows:
            kind = params.get('type')
            if kind == 'message.complete':
                state = {'complete': 'completed', 'error': 'failed', 'interrupted': 'cancelled'}.get(payload.get('status'))
                if state:
                    result = payload.get('text', '')
                    self._state(row['run_id'], state, result if isinstance(result, str) else None)
            elif kind in ('message.start', 'message.delta') and row['state'] not in ('stopping', 'awaiting_approval'):
                self._state(row['run_id'], 'running')

    def _drain(self):
        for frame in self.rpc.drain():
            if not isinstance(frame, dict):
                continue
            params = frame.get('params', {})
            if frame.get('method') == 'event':
                self._event(params)
            elif 'id' in frame and frame.get('method') and isinstance(params, dict):
                # Server requests require the owner's trusted Hermes UI.
                rows = list(self.db.execute('SELECT run_id FROM desktop_runs WHERE runtime_id=?',
                                            (params.get('session_id'),)))
                for row in rows:
                    self._state(row['run_id'], 'awaiting_approval')

    def read(self, run_id):
        with self.lock:
            self._drain()
            row = self._row(run_id)
            if row['state'] not in TERMINAL and row['runtime_id']:
                try:
                    replay = self.rpc.call('session.events.since', {
                        'session_id': row['runtime_id'], 'last_seen': row['last_seq']})
                    epoch = replay.get('epoch')
                    if replay.get('truncated') or (row['epoch'] and epoch != row['epoch']):
                        self._state(run_id, 'unknown')
                    else:
                        for event in replay.get('events', []):
                            self._event(event)
                        if replay.get('open_requests'):
                            self._state(run_id, 'awaiting_approval')
                    seq = replay.get('latest_seq', row['last_seq'])
                    if type(seq) is int and seq >= 0:
                        with self.db:
                            self.db.execute('UPDATE desktop_runs SET last_seq=?,epoch=? WHERE run_id=?',
                                            (seq, epoch, run_id))
                except Exception:
                    self._state(run_id, 'unknown')
            row = self._row(run_id)
            return {'state': row['state'], 'result': row['result']}

    def stop(self, run_id):
        with self.lock:
            row = self._row(run_id)
            if row['state'] in TERMINAL:
                return
            if not row['runtime_id']:
                self._state(run_id, 'unknown')
                return
            try:
                self.rpc.call('session.interrupt', {'session_id': row['runtime_id']})
                self._state(run_id, 'stopping')
            except Exception:
                self._state(run_id, 'unknown')

    def handoff(self, run_id):
        with self.lock:
            row = self._row(run_id)
            return {'interface': 'hermes_desktop', 'stored_session_id': row['stored_id'],
                    'action': 'Open this session in the trusted Hermes Desktop UI.'}
