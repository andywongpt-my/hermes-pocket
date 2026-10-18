"""Bounded speech worker transport. Provider credentials remain inside Hermes."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

from .audio import AudioError
from .transport import unique
from .stt_pool import ResidentSTTPool
from .voice_errors import VoiceError

MAX_REQUEST = 1400000
MAX_RESPONSE = 4 * 1024 * 1024 + 1024


def _stop_worker(proc):
    if proc.poll() is not None:
        return
    try:
        if sys.platform == 'linux':
            # The subreaper owns all provider descendants, including independent
            # sessions. Ask it to drain/reap them; never kill that supervisor.
            proc.terminate()
        else:
            proc.kill()
        proc.wait(timeout=5)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        # A kernel-blocked child may take longer to die. Keep its supervisor
        # alive to retain containment and finish reaping instead of orphaning it.
        pass


def _exchange(proc, payload, timeout, *, max_response=None, cancel=None):
    """Bound every read while feeding stdin concurrently under one deadline."""
    events = queue.Queue(maxsize=2)
    stopped = threading.Event()
    deadline = time.monotonic() + timeout

    def feed():
        try:
            remaining = memoryview(payload)
            while remaining and not stopped.is_set():
                written = os.write(proc.stdin.fileno(), remaining[:65536])
                remaining = remaining[written:]
        except (OSError, ValueError):
            pass
        finally:
            proc.stdin.close()

    def read():
        try:
            while not stopped.is_set():
                chunk = os.read(proc.stdout.fileno(), 65536)
                while not stopped.is_set():
                    try:
                        events.put(chunk, timeout=.05)
                        break
                    except queue.Full:
                        pass
                if not chunk:
                    return
        except (OSError, ValueError):
            # The parent turns a missing completion into the same bounded error.
            pass

    threads = [threading.Thread(target=feed, daemon=True),
               threading.Thread(target=read, daemon=True)]
    for thread in threads:
        thread.start()
    output = bytearray()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise VoiceError('tts_shutdown')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            try:
                chunk = events.get(timeout=min(.05, remaining) if cancel is not None else remaining)
            except queue.Empty:
                if cancel is not None:
                    continue
                raise subprocess.TimeoutExpired(proc.args, timeout) from None
            if not chunk:
                break
            if len(output) + len(chunk) > (MAX_RESPONSE if max_response is None else max_response):
                raise AudioError('Hermes speech output too large')
            output.extend(chunk)
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                raise VoiceError('tts_shutdown')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            try: proc.wait(timeout=min(.05, remaining) if cancel is not None else remaining)
            except subprocess.TimeoutExpired:
                if cancel is None: raise
        return bytes(output)
    finally:
        stopped.set()
        _stop_worker(proc)
        # The Linux supervisor can remain alive while draining an uninterruptible
        # provider child. Keep this invocation active until it confirms reap;
        # cancellation HTTP can honestly remain 202 meanwhile. Never reset STT.
        if cancel is not None:
            while proc.poll() is None:
                try: proc.wait(timeout=.05)
                except subprocess.TimeoutExpired: pass
        for thread in threads:
            thread.join(timeout=.5)
        proc.stdin.close()
        proc.stdout.close()


class HermesSpeechTransport:
    def __init__(self, python, hermes_root, hermes_home, pins_file, *, timeout=150,
                 worker_path=None, resident=False):
        paths = [Path(x) for x in (python, hermes_root, hermes_home, pins_file)]
        if any(not p.is_absolute() for p in paths):
            raise ValueError('explicit absolute Hermes runtime paths required')
        if not paths[0].is_file() or not paths[1].is_dir() or not paths[2].is_dir() or not paths[3].is_file():
            raise ValueError('existing Hermes runtime and speech pins required')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 180:
            raise ValueError('bounded speech timeout required')
        self.python, self.root, self.home, self.pins = map(str, paths)
        self.worker = str(Path(worker_path) if worker_path else Path(__file__).with_name('hermes_speech_worker.py'))
        self.timeout = timeout
        if type(resident) is not bool:
            raise ValueError('explicit resident STT choice required')
        self._resident = resident
        self._stt_pool = None
        self._bootstrap_proc = None
        self._warm_lock = threading.Lock()
        self._closed = False

    def _resolve_stt_settings(self, timeout):
        with tempfile.TemporaryDirectory(prefix='pocket-stt-bootstrap-') as directory:
            env = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'PATH',
                'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR') if k in os.environ}
            env.update(HERMES_HOME=self.home, PYTHONDONTWRITEBYTECODE='1',
                PYTHONNOUSERSITE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                TMPDIR=directory, TEMP=directory, TMP=directory)
            args = [self.python, '-B', str(Path(__file__).with_name('stt_bootstrap.py')),
                    '--hermes-root', self.root, '--pins', self.pins]
            if sys.platform == 'linux':
                args = [self.python, '-B', str(Path(__file__).with_name('speech_supervisor.py')),
                        '--timeout', str(timeout), '--', *args]
            proc = None
            try:
                if self._closed:
                    raise VoiceError('stt_shutdown')
                proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env=env, bufsize=0,
                    **({'start_new_session': True} if os.name == 'posix' else {}))
                self._bootstrap_proc = proc
                raw = _exchange(proc, b'', timeout, max_response=16384)
                if proc.returncode == 124:
                    raise VoiceError('stt_startup_timeout')
                if proc.returncode:
                    raise VoiceError('stt_startup_failed')
                value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value, dict) or set(value) != {'ok', 'settings'} or value['ok'] is not True:
                    raise VoiceError('stt_startup_failed')
                settings = value['settings']
                if not isinstance(settings, dict) or set(settings) != {
                        'helper_path', 'helper_sha256', 'model_path', 'language'}:
                    raise VoiceError('stt_protocol_error')
                return settings
            except subprocess.TimeoutExpired:
                raise VoiceError('stt_startup_timeout') from None
            except VoiceError:
                raise
            except Exception:
                raise VoiceError('stt_startup_failed') from None
            finally:
                if proc is not None:
                    _stop_worker(proc)
                self._bootstrap_proc = None

    def warm(self):
        if not self._resident:
            return self.runtime_metadata()
        with self._warm_lock:
            if self._closed:
                raise VoiceError('stt_shutdown')
            if self._stt_pool is None:
                started = time.monotonic()
                settings = self._resolve_stt_settings(60)
                remaining = 60 - (time.monotonic() - started)
                if remaining <= 0:
                    raise VoiceError('stt_startup_timeout')
                pool = ResidentSTTPool(self.python, settings, startup_timeout=remaining)
                self._stt_pool = pool
                if self._closed:
                    pool.close()
                    raise VoiceError('stt_shutdown')
            self._stt_pool.warm()
        return self.runtime_metadata()

    def runtime_metadata(self):
        pool = self._stt_pool
        return {'stt': pool.runtime_metadata() if pool is not None else {
            'resident': self._resident, 'ready': False, 'generation': 0,
            'active': 0, 'pending': 0, 'worker_pid': 0, 'model_load_us': 0,
            'startup_us': 0, 'restart_count': 0}}

    def transcribe_job(self, body, *, cancel=None):
        if self._closed:
            raise VoiceError('stt_shutdown')
        if self._resident:
            if self._stt_pool is None:
                self.warm()
            return self._stt_pool.transcribe_job(body, cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise VoiceError('stt_cancelled')
        value = self._invoke({'operation': 'transcribe', 'body': body})
        return {'transcript': value.get('transcript'), 'timings_us': {}}

    def close(self):
        if self._closed:
            return
        self._closed = True
        proc = self._bootstrap_proc
        if proc is not None:
            _stop_worker(proc)
        if self._stt_pool is not None:
            self._stt_pool.close()

    def _invoke(self, request, *, cancel=None, timeout=None):
        invocation_timeout = self.timeout if timeout is None else min(self.timeout, timeout)
        if isinstance(invocation_timeout, bool) or not isinstance(invocation_timeout, (int, float)) or not 0 < invocation_timeout <= 180:
            raise VoiceError('tts_synthesis_timeout')
        if cancel is not None and cancel.is_set():
            raise VoiceError('tts_shutdown')
        payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > MAX_REQUEST:
            raise AudioError('speech request too large')
        with tempfile.TemporaryDirectory(prefix='pocket-speech-') as directory:
            # No Pocket pairing/gateway/provider credentials cross into the worker.
            # Hermes resolves its existing provider credential internally, as usual.
            env = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'PATH',
                'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR') if k in os.environ}
            env.update(HERMES_HOME=self.home, PYTHONDONTWRITEBYTECODE='1',
                PYTHONNOUSERSITE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                TMPDIR=directory, TEMP=directory, TMP=directory)
            args = [self.python, '-B', self.worker, '--hermes-root', self.root,
                '--pins', self.pins, '--work-dir', directory]
            if sys.platform == 'linux':
                supervisor = str(Path(__file__).with_name('speech_supervisor.py'))
                args = [self.python, '-B', supervisor, '--timeout', str(invocation_timeout), '--', *args]
            proc = None
            try:
                options = {'start_new_session': True} if os.name == 'posix' else {}
                proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env=env, bufsize=0, **options)
                stdout = _exchange(proc, payload, invocation_timeout + (1 if sys.platform == 'linux' and cancel is None else 0), cancel=cancel)
                if proc.returncode == 124:
                    raise AudioError('Hermes speech timed out')
                if proc.returncode:
                    raise AudioError('Hermes speech unavailable')
                value = json.loads(stdout.decode('utf-8'), object_pairs_hook=unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value, dict) or value.get('ok') is not True:
                    raise AudioError('Hermes speech unavailable')
                return value
            except subprocess.TimeoutExpired:
                _stop_worker(proc)
                raise AudioError('Hermes speech timed out') from None
            except AudioError:
                raise
            except (OSError, ValueError, UnicodeError, RecursionError):
                if proc is not None:
                    _stop_worker(proc)
                raise AudioError('Hermes speech unavailable') from None

    def check(self):
        value = self._invoke({'operation': 'check'})
        if (value.get('ready') is not True or value.get('stt_provider') != 'pocket_local'
                or value.get('tts_provider') != 'fishaudio'):
            raise AudioError('approved Hermes speech providers unavailable')
        if self._resident:
            self.warm()
        return value

    def speak_job(self, body, *, cancel, timeout):
        if self._closed: raise VoiceError('tts_shutdown')
        if not isinstance(body, dict) or set(body) != {'text'} or not isinstance(body['text'], str) or not body['text'].strip() or len(body['text'].encode()) > 4096:
            raise VoiceError('tts_text_too_long')
        try:
            return self._invoke({'operation': 'speak', 'body': body}, cancel=cancel, timeout=min(120, timeout))
        except AudioError as error:
            if isinstance(error, VoiceError): raise
            raise VoiceError('tts_synthesis_timeout' if str(error) == 'Hermes speech timed out'
                             else 'tts_synthesis_failed') from None

    def __call__(self, path, body):
        routes = {'/api/audio/transcribe': 'transcribe', '/api/audio/speak': 'speak'}
        if path not in routes:
            raise AudioError('unsupported speech route')
        if path == '/api/audio/transcribe' and self._resident:
            value = self.transcribe_job(body)
            return {'ok': True, **value}
        if path == '/api/audio/speak' and isinstance(body, dict) and isinstance(body.get('text'), str) and len(body['text'].encode()) > 4096:
            raise VoiceError('tts_text_too_long')
        try:
            return self._invoke({'operation': routes[path], 'body': body})
        except AudioError as error:
            if path == '/api/audio/speak':
                raise VoiceError('tts_synthesis_timeout' if str(error) == 'Hermes speech timed out'
                                 else 'tts_synthesis_failed') from None
            raise
