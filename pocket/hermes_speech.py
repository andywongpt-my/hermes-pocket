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


def _exchange(proc, payload, timeout):
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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            try:
                chunk = events.get(timeout=remaining)
            except queue.Empty:
                raise subprocess.TimeoutExpired(proc.args, timeout) from None
            if not chunk:
                break
            if len(output) + len(chunk) > MAX_RESPONSE:
                raise AudioError('Hermes speech output too large')
            output.extend(chunk)
        proc.wait(timeout=max(.001, deadline - time.monotonic()))
        return bytes(output)
    finally:
        stopped.set()
        _stop_worker(proc)
        for thread in threads:
            thread.join(timeout=.5)
        proc.stdin.close()
        proc.stdout.close()


class HermesSpeechTransport:
    def __init__(self, python, hermes_root, hermes_home, pins_file, *, timeout=150,
                 worker_path=None):
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

    def _invoke(self, request):
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
                args = [self.python, '-B', supervisor, '--timeout', str(self.timeout), '--', *args]
            proc = None
            try:
                options = {'start_new_session': True} if os.name == 'posix' else {}
                proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env=env, bufsize=0, **options)
                stdout = _exchange(proc, payload, self.timeout + (1 if sys.platform == 'linux' else 0))
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
        return value

    def __call__(self, path, body):
        routes = {'/api/audio/transcribe': 'transcribe', '/api/audio/speak': 'speak'}
        if path not in routes:
            raise AudioError('unsupported speech route')
        return self._invoke({'operation': routes[path], 'body': body})
