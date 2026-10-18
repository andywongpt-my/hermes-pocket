"""Best-effort numeric chunk telemetry; journal stalls never hold HTTP workers.

Each line is an integer array: schema, serial, UTC microseconds, capture tag,
chunk sequence, HTTP status, request/response bytes, header-parse/body-read/
Voice-lock-wait/chunk-execution/header-write/body-write/dispatch microseconds.
Serial gaps identify dropped records. No payload or exception strings are kept.
"""
import itertools
import json
import os
import queue
import sys
import threading


class NumericTimingLog:
    def __init__(self):
        self._queue = queue.Queue(maxsize=64)
        self._stopped = threading.Event()
        self._serial = itertools.count(1)
        try:
            try:source=sys.stderr.fileno()
            except (AttributeError,OSError,ValueError):source=2
            self._fd=os.dup(source)
        except OSError:
            self._stopped.set()
            return  # Diagnostics must not prevent startup if stderr is closed.
        self._thread = threading.Thread(target=self._write, daemon=True,
                                        name='pocket-chunk-timing')
        self._thread.start()

    def emit(self, fields):
        if self._stopped.is_set() or len(fields) != 13 or any(type(v) is not int for v in fields):
            return
        row = (1, next(self._serial), *fields)
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            pass

    def _write(self):
        try:
            while not self._stopped.is_set():
                try:
                    row = self._queue.get(timeout=.25)
                except queue.Empty:
                    continue
                try:
                    pending=(json.dumps(row,separators=(',', ':'))+'\n').encode('ascii')
                    while pending:
                        written=os.write(self._fd,pending)
                        if written<=0:break
                        pending=pending[written:]
                except Exception:
                    pass  # Never emit exception messages or retry failed rows.
        finally:
            try:os.close(self._fd)
            except OSError:pass

    def close(self):
        # Raw fd writes never hold the Python buffered stderr lock at shutdown.
        self._stopped.set()  # Daemon output may be blocked; shutdown must not wait.
