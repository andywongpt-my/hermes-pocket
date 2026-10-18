"""Durable fixed voice reasons/timings and bounded integer-only journal rows."""
import hashlib
import itertools
import json
import os
import queue
import sys
import threading
import time

from .voice_errors import REASONS, STAGES, safe_timings


def journal_fields(device, kind, job, stage, reason, elapsed_us):
    tag = hashlib.sha256(json.dumps([device, kind, job], separators=(',', ':')).encode()).digest()
    return [time.time_ns() // 1000, int.from_bytes(tag[:8], 'big') & (2**63 - 1),
            1 if kind == 'audio' else 2, STAGES.index(stage), REASONS.index(reason), elapsed_us]


class NumericVoiceLog:
    def __init__(self):
        self._queue = queue.Queue(maxsize=64)
        self._stopped = threading.Event()
        self._serial = itertools.count(1)
        try:
            try: source = sys.stderr.fileno()
            except (AttributeError, OSError, ValueError): source = 2
            self._fd = os.dup(source)
        except OSError:
            self._stopped.set(); return
        threading.Thread(target=self._write, daemon=True, name='pocket-voice-timing').start()

    def emit(self, fields):
        if self._stopped.is_set() or len(fields) != 6 or any(type(v) is not int for v in fields):
            return
        try: self._queue.put_nowait((2, next(self._serial), *fields))
        except queue.Full: pass

    def _write(self):
        try:
            while not self._stopped.is_set():
                try: row = self._queue.get(timeout=.05)
                except queue.Empty: continue
                try:
                    remaining = (json.dumps(row, separators=(',', ':')) + '\n').encode('ascii')
                    while remaining:
                        count = os.write(self._fd, remaining)
                        if count <= 0: break
                        remaining = remaining[count:]
                except Exception:
                    pass
        finally:
            try: os.close(self._fd)
            except OSError: pass

    def close(self):
        self._stopped.set()  # A blocked journal must never hold up shutdown.


class VoiceObservations:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.executescript('''
              CREATE TABLE IF NOT EXISTS voice_details (
                event INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT NOT NULL, kind TEXT NOT NULL, job TEXT NOT NULL,
                reason TEXT NOT NULL, stage TEXT NOT NULL, utc_us INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS voice_details_job ON voice_details(device,kind,job,event);
              CREATE TABLE IF NOT EXISTS voice_timings (
                device TEXT NOT NULL, kind TEXT NOT NULL, job TEXT NOT NULL,
                stage TEXT NOT NULL, elapsed_us INTEGER NOT NULL, utc_us INTEGER NOT NULL,
                PRIMARY KEY(device,kind,job,stage));
            ''')
        self.journal = NumericVoiceLog()

    def record(self, device, kind, job, reason, stage, timings_us=None):
        reason = reason if reason in REASONS else 'unknown'
        stage = stage if stage in STAGES else 'unknown'
        timings = safe_timings(timings_us)
        utc = time.time_ns() // 1000
        try:
            with self.store.lock:
                # SQLite connection contexts are not nested transactions. Never
                # commit or roll back a caller's critical work for optional data.
                # Real callers record after committing state; unexpected nesting
                # keeps only the best-effort numeric journal below.
                if not self.store.db.in_transaction:
                    with self.store.db:
                        self.store.db.execute('INSERT INTO voice_details(device,kind,job,reason,stage,utc_us) VALUES(?,?,?,?,?,?)',
                                              (device, kind, job, reason, stage, utc))
                        for name, elapsed in timings.items():
                            self.store.db.execute('INSERT OR REPLACE INTO voice_timings VALUES(?,?,?,?,?,?)',
                                                  (device, kind, job, name, elapsed, utc))
        except Exception:
            pass  # Observability failure never changes admission or leaks a payload.
        try:
            self.journal.emit(journal_fields(device, kind, job, stage, reason, timings.get(stage, 0)))
        except Exception:
            pass

    def status(self, device, kind, job):
        try:
            with self.store.lock:
                detail = self.store.db.execute('SELECT reason,stage FROM voice_details WHERE device=? AND kind=? AND job=? ORDER BY event DESC LIMIT 1',
                                               (device, kind, job)).fetchone()
                rows = self.store.db.execute('SELECT stage,elapsed_us FROM voice_timings WHERE device=? AND kind=? AND job=?',
                                             (device, kind, job)).fetchall()
            reason, stage = tuple(detail) if detail else ('unknown', 'unknown')
            return {'reason': reason if reason in REASONS else 'unknown',
                    'stage': stage if stage in STAGES else 'unknown',
                    'timings_us': safe_timings(dict(rows))}
        except Exception:
            return {'reason': 'unknown', 'stage': 'unknown', 'timings_us': {}}

    def close(self):
        self.journal.close()
