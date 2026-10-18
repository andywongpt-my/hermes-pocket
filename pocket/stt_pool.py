"""One resident process, one active STT request and one bounded waiting slot."""
from collections import deque
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .stt_protocol import read_frame, encode_frame, MAX_REQUEST, MAX_RESPONSE
from .voice_errors import VoiceError


def worker_environment(directory):
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR')
           if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               TMPDIR=directory, TEMP=directory, TMP=directory)
    return env


class ResidentSTTPool:
    def __init__(self, python, settings, *, worker_path=None, startup_timeout=60,
                 queue_timeout=15, job_timeout=150):
        if (not Path(python).is_absolute() or not Path(python).is_file()
                or not isinstance(settings, dict) or set(settings) != {
                    'helper_path', 'helper_sha256', 'model_path', 'language'}
                or any(not isinstance(v, str) or len(v) > 4096 for v in settings.values())):
            raise ValueError('explicit local STT runtime required')
        for value, limit in ((startup_timeout, 60), (queue_timeout, 15), (job_timeout, 150)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= limit:
                raise ValueError('bounded STT timeouts required')
        self.python, self.settings = str(python), dict(settings)
        self.worker = str(worker_path or Path(__file__).with_name('stt_worker.py'))
        self.startup_timeout, self.queue_timeout, self.job_timeout = startup_timeout, queue_timeout, job_timeout
        self._condition = threading.Condition(threading.RLock())
        self._io_lock = threading.Lock()
        self._proc = None; self._events = None; self._directory = None
        self._active = None; self._pending = None; self._closed = False
        self._ready = False; self._generation = 0; self._restarts = deque()
        self._restart_count = 0; self._startup_us = 0; self._model_load_us = 0

    def _check(self, cancel, deadline, timeout_reason):
        if self._closed:
            raise VoiceError('stt_shutdown')
        if cancel is not None and cancel.is_set():
            raise VoiceError('stt_cancelled')
        if time.monotonic() >= deadline:
            raise VoiceError(timeout_reason)

    def _stop(self, proc):
        if proc is None:
            return
        with self._condition:
            if self._proc is proc:
                self._ready = False
        try:
            if proc.poll() is None:
                if os.name == 'posix':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            proc.wait(timeout=5)
        except ProcessLookupError:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            raise VoiceError('stt_worker_unreaped') from None
        with self._condition:
            if self._proc is proc:
                self._proc = None
                directory, self._directory = self._directory, None
            else:
                directory = None
        for stream in (proc.stdin, proc.stdout):
            try: stream.close()
            except (OSError, ValueError): pass
        if directory is not None:
            directory.cleanup()

    def _receive(self, events, cancel, deadline, timeout_reason):
        while True:
            self._check(cancel, deadline, timeout_reason)
            try:
                kind, value = events.get(timeout=min(.025, max(.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            self._check(cancel, deadline, timeout_reason)
            if kind != 'frame':
                raise VoiceError('stt_protocol_error' if kind == 'protocol' else 'stt_worker_crash')
            return value

    def _send(self, proc, events, value):
        try:
            raw = encode_frame(value, MAX_REQUEST)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise VoiceError('stt_protocol_error') from None
        def write():
            try:
                remaining = memoryview(raw)
                while remaining:
                    count = os.write(proc.stdin.fileno(), remaining[:65536])
                    if count <= 0:
                        raise OSError()
                    remaining = remaining[count:]
            except (OSError, ValueError):
                try: events.put_nowait(('crash', None))
                except queue.Full: pass
        thread = threading.Thread(target=write, daemon=True, name='pocket-stt-feed')
        thread.start()
        return thread

    def _start(self, cancel, deadline):
        self._check(cancel, deadline, 'stt_timeout')
        with self._condition:
            proc = self._proc
            if proc is not None and self._ready and proc.poll() is None:
                return
        if proc is not None:
            self._stop(proc)
        with self._condition:
            now = time.monotonic()
            while self._restarts and self._restarts[0] <= now - 60:
                self._restarts.popleft()
            if self._generation:
                if len(self._restarts) >= 2:
                    raise VoiceError('stt_restart_backoff')
                self._restarts.append(now); self._restart_count += 1
            self._generation += 1
            generation = self._generation
        started = time.monotonic_ns()
        startup_deadline = time.monotonic() + self.startup_timeout
        until = min(deadline, startup_deadline)
        timeout_reason = 'stt_timeout' if deadline < startup_deadline else 'stt_startup_timeout'
        directory = tempfile.TemporaryDirectory(prefix='pocket-resident-stt-')
        proc = None
        try:
            with self._condition:
                self._check(cancel, deadline, 'stt_timeout')
                proc = subprocess.Popen([self.python, '-B', self.worker, '--parent-pid', str(os.getpid())],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    env=worker_environment(directory.name), bufsize=0,
                    **({'start_new_session': True} if os.name == 'posix' else {}))
                self._proc, self._directory = proc, directory
                events = self._events = queue.Queue(maxsize=2)
            def read():
                try:
                    while True:
                        value = read_frame(proc.stdout, MAX_RESPONSE)
                        if value is None:
                            events.put_nowait(('crash', None)); return
                        events.put_nowait(('frame', value))
                except queue.Full:
                    return
                except (EOFError, OSError, ValueError, UnicodeError, RecursionError):
                    try: events.put_nowait(('protocol', None))
                    except queue.Full: pass
            threading.Thread(target=read, daemon=True, name='pocket-stt-read').start()
            writer = self._send(proc, events, {'kind': 'init', 'generation': generation, 'settings': self.settings})
            ready = self._receive(events, cancel, until, timeout_reason)
            if (not isinstance(ready, dict) or set(ready) != {'kind', 'generation', 'model_load_us'}
                    or ready['kind'] != 'ready' or type(ready['generation']) is not int
                    or ready['generation'] != generation or type(ready['model_load_us']) is not int
                    or not 0 <= ready['model_load_us'] <= 60000000):
                raise VoiceError('stt_protocol_error')
            writer.join(.1)
            with self._condition:
                self._check(cancel, deadline, 'stt_timeout')
                self._ready = True
                self._startup_us = (time.monotonic_ns() - started) // 1000
                self._model_load_us = ready['model_load_us']
        except Exception as error:
            if proc is not None:
                self._stop(proc)
            else:
                directory.cleanup()
            if isinstance(error, VoiceError):
                raise
            raise VoiceError('stt_startup_failed') from None

    def warm(self):
        with self._io_lock:
            self._start(None, float('inf'))
        return self.runtime_metadata()

    def transcribe_job(self, body, *, cancel=None):
        admitted = time.monotonic()
        deadline, queue_deadline = admitted + self.job_timeout, admitted + self.queue_timeout
        token = object()
        timings = {}
        try:
            with self._condition:
                self._check(cancel, deadline, 'stt_timeout')
                if self._active is not None:
                    if self._pending is not None:
                        raise VoiceError('stt_queue_full')
                    self._pending = token
                    try:
                        while self._active is not None:
                            self._check(cancel, deadline, 'stt_timeout')
                            self._check(cancel, queue_deadline, 'stt_queue_timeout')
                            self._condition.wait(.025)
                        self._check(cancel, min(deadline, queue_deadline), 'stt_queue_timeout')
                    finally:
                        self._pending = None
                self._active = token
            timings['queue'] = int((time.monotonic() - admitted) * 1000000)
            with self._io_lock:
                started = time.monotonic_ns()
                try:
                    self._start(cancel, deadline)
                finally:
                    timings['startup'] = (time.monotonic_ns() - started) // 1000
                with self._condition:
                    proc, events, generation = self._proc, self._events, self._generation
                request_id = uuid.uuid4().hex
                try:
                    writer = self._send(proc, events, {'kind': 'transcribe', 'generation': generation,
                        'request_id': request_id, 'body': body})
                    value = self._receive(events, cancel, deadline, 'stt_timeout')
                    base = {'kind', 'generation', 'request_id', 'ok', 'inference_us'}
                    if (not isinstance(value, dict) or type(value.get('ok')) is not bool
                            or set(value) != base | ({'transcript'} if value.get('ok') else {'reason'})
                            or value['kind'] != 'result' or type(value['generation']) is not int
                            or value['generation'] != generation or value['request_id'] != request_id
                            or type(value['inference_us']) is not int or not 0 <= value['inference_us'] <= 150000000):
                        raise VoiceError('stt_protocol_error')
                    timings['inference'] = value['inference_us']
                    self._check(cancel, deadline, 'stt_timeout')
                    writer.join(.1)
                    if not value['ok']:
                        if value['reason'] not in ('stt_no_confident_speech', 'stt_worker_failed', 'stt_invalid_audio'):
                            raise VoiceError('stt_protocol_error')
                        raise VoiceError(value['reason'])
                    text = value['transcript']
                    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 4096:
                        raise VoiceError('stt_protocol_error')
                    timings['stt_total'] = int((time.monotonic() - admitted) * 1000000)
                    return {'transcript': text.strip(), 'timings_us': timings}
                except VoiceError as error:
                    if error.reason not in ('stt_no_confident_speech', 'stt_worker_failed', 'stt_invalid_audio'):
                        self._stop(proc)
                    raise
        except VoiceError as error:
            timings.setdefault('queue', int((time.monotonic() - admitted) * 1000000))
            timings['stt_total'] = int((time.monotonic() - admitted) * 1000000)
            error.timings_us = timings
            raise
        finally:
            with self._condition:
                if self._active is token:
                    self._active = None
                self._condition.notify_all()

    def runtime_metadata(self):
        with self._condition:
            proc = self._proc
            alive = proc is not None and proc.poll() is None
            result = {'resident': True, 'ready': bool(alive and self._ready),
                'generation': self._generation, 'active': int(self._active is not None),
                'pending': int(self._pending is not None), 'worker_pid': proc.pid if alive else 0,
                'model_load_us': self._model_load_us, 'startup_us': self._startup_us,
                'restart_count': self._restart_count}
            if alive and sys.platform == 'linux':
                try:
                    pages = int((Path('/proc') / str(proc.pid) / 'statm').read_text().split()[1])
                    result['worker_rss_bytes'] = pages * os.sysconf('SC_PAGE_SIZE')
                except (OSError, ValueError, IndexError):
                    pass
            return result

    def close(self):
        with self._condition:
            self._closed = True
            proc = self._proc
            self._condition.notify_all()
        self._stop(proc)
