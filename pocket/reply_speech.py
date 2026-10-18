"""Synth-once reply sessions with private disk PCM and shared resource bounds.

Only bounded chunks leave the spool. The journal is an idempotency tombstone;
restarting, cancelling or expiring a job never invokes synthesis again.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid

from .linux_voice import EncodedSpeech, decode_speech_to_file
from .result_pages import ReplyError, completed_result
from .schema import identifier, ProtocolError
from .store import NotFound

MAX_TEXT = 4096
MAX_ENCODED = 3145728
SEGMENT_BYTES = 640000
CHUNK_BYTES = 4096
MAX_PCM = 7680000
OVERSHOOT = 65536
MAX_ACTIVE = 2
MAX_READY = 4
DISK_BUDGET = 67108864
GENERATION_SECONDS = 120
DECODE_SECONDS = 20
IDLE_SECONDS = 180
ABSOLUTE_SECONDS = 900
CLEANUP_SECONDS = 15
_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_PCM_NAME = re.compile(r'[0-9a-f]{32}\.pcm')


class SpeechFailure(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _safe_path(path):
    path = Path(os.path.abspath(path))
    for component in reversed((path, *path.parents)):
        if component.exists() or component.is_symlink():
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise OSError('unsafe reply spool')
    return path


class _PrivateDirectory:
    """A process lease precedes recovery; cleanup never follows a symlink."""
    def __init__(self, path):
        self.path = _safe_path(path)
        self.fd = None
        # Reject symlink/reparse components rather than resolving them and then
        # potentially cleaning an unrelated directory through a changed link.
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.lstat()
        if not stat.S_ISDIR(info.st_mode) or (hasattr(os, 'getuid') and info.st_uid != os.getuid()):
            raise OSError('unsafe reply spool')
        os.chmod(self.path, 0o700)
        self.identity = (info.st_dev, info.st_ino)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.path / 'lease', flags, 0o600)
        try:
            lease = os.fstat(fd)
            if not stat.S_ISREG(lease.st_mode) or lease.st_nlink != 1 or (hasattr(os, 'getuid') and lease.st_uid != os.getuid()):
                raise OSError('unsafe reply lease')
            os.chmod(self.path / 'lease', 0o600)
            if os.name == 'posix':
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                if not lease.st_size: os.write(fd, b'\0')
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise

    def _check(self):
        info = self.path.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self.identity:
            raise OSError('reply spool replaced')

    def create(self, generation):
        self._check()
        fd = os.open(self.path / (generation + '.pcm'),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
        return os.fdopen(fd, 'wb', buffering=0)

    def _file(self, generation):
        self._check()
        path = self.path / (generation + '.pcm')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (hasattr(os, 'getuid') and info.st_uid != os.getuid()):
            raise OSError('unsafe reply file')
        return path, info

    def read(self, generation, offset, size):
        if not 0 < size <= CHUNK_BYTES or offset < 0:
            raise ValueError('invalid bounded read')
        path, info = self._file(generation)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
                raise OSError('reply file changed')
            os.lseek(fd, offset, os.SEEK_SET)
            data = os.read(fd, size)
            if len(data) != size:
                raise OSError('incomplete reply file')
            return data
        finally:
            os.close(fd)

    def remove(self, generation):
        try: path, _ = self._file(generation)
        except FileNotFoundError: return
        path.unlink()

    def recover(self):
        self._check()
        for path in self.path.iterdir():
            if _PCM_NAME.fullmatch(path.name):
                self.remove(path.stem)

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            # Keep the lease inode: unlinking it would allow split ownership.
            if os.name != 'posix':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            os.close(fd)


class _PCMSink:
    def __init__(self, output, cancel):
        self.output, self.cancel, self.bytes = output, cancel, 0

    def write(self, data):
        if self.cancel.is_set(): raise SpeechFailure('tts_cancelled')
        view = memoryview(data)
        while view:
            part = view[:OVERSHOOT]
            if self.bytes + len(part) > MAX_PCM + OVERSHOOT:
                raise SpeechFailure('tts_playback_limit')
            written = self.output.write(part)
            if not written: raise OSError(errno.ENOSPC, 'reply spool write failed')
            self.bytes += written; view = view[written:]
            if self.bytes > MAX_PCM:
                raise SpeechFailure('tts_playback_limit')
        return len(data)

    def flush(self): self.output.flush()


@dataclass
class _Job:
    owner: object
    device: str
    task: str
    request: str
    revision: str
    generation: str
    created: float
    idle: float
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    state: str = 'processing'
    reason: str | None = None
    retire_state: str | None = None
    bytes: int = 0
    segments: list = field(default_factory=list)

    @property
    def key(self): return self.owner.target, self.device, self.task


class ReplySpools:
    """Shared two-active/four-ready/64MiB controller for every server target."""
    def __init__(self, stores, *, root=None, clock=time.monotonic):
        self.lock = threading.RLock()
        self.clock, self.stores, self.directories = clock, dict(stores), {}
        self.jobs = {}
        self.incarnation = uuid.uuid4().hex
        self.closed = False
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=MAX_ACTIVE, thread_name_prefix='pocket-reply')
        try:
            # Acquire every lease before journal recovery or any old file removal.
            for target, store in self.stores.items():
                filename = store.db.execute('PRAGMA database_list').fetchone()[2]
                if root is not None:
                    path = Path(root) / hashlib.sha256(target.encode()).hexdigest()[:24]
                    # The common parent is private too, never a public PCM root.
                    parent = _safe_path(root)
                    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.chmod(parent, 0o700)
                elif filename:
                    path = Path(filename).parent / ('.pocket-reply-v2-' + hashlib.sha256(os.path.abspath(filename).encode()).hexdigest()[:24])
                else:
                    path = Path(tempfile.gettempdir()) / ('pocket-reply-v2-' + self.incarnation + '-' + target)
                self.directories[target] = _PrivateDirectory(path)
            for target, store in self.stores.items():
                with store.lock, store.db:
                    store.db.executescript('''
                      CREATE TABLE IF NOT EXISTS speech_jobs (
                        device TEXT NOT NULL, task TEXT NOT NULL, state TEXT NOT NULL,
                        PRIMARY KEY(device,task));
                      CREATE TABLE IF NOT EXISTS reply_speech_v2 (
                        device TEXT NOT NULL, task TEXT NOT NULL, request_id TEXT NOT NULL,
                        result_revision TEXT NOT NULL, generation TEXT NOT NULL,
                        state TEXT NOT NULL, reason TEXT,
                        PRIMARY KEY(device,task));
                    ''')
            # Cleanup must succeed under every lease before a legacy active
            # marker can become terminal. Failed cleanup leaves honest markers
            # for the controller's existing SQLite release-idle gate.
            for directory in self.directories.values(): directory.recover()
            for store in self.stores.values():
                with store.lock, store.db:
                    store.db.execute("UPDATE speech_jobs SET state='unavailable' WHERE (device,task) IN (SELECT device,task FROM reply_speech_v2 WHERE state IN ('processing','ready','cancelling'))")
                    store.db.execute("UPDATE reply_speech_v2 SET state='unavailable',reason='tts_restart_loss' WHERE state IN ('processing','ready','cancelling')")
        except BaseException:
            for directory in self.directories.values(): directory.close()
            self.pool.shutdown(wait=True)
            raise
        self.cleaner = threading.Thread(target=self._clean, daemon=True, name='pocket-reply-cleanup')
        self.cleaner.start()

    def _clean(self):
        while not self.stop.wait(CLEANUP_SECONDS):
            try: self.expire()
            except Exception: pass  # No raw content, paths or exceptions in logs.

    def _persist(self, job):
        store = job.owner.store
        # A processing marker covers provider/decoder work AND cleanup/reaping.
        marker = 'ready' if job.state == 'ready' else (
            'processing' if not job.done.is_set() or job.state == 'cancelling' else 'unavailable')
        with store.lock, store.db:
            store.db.execute('UPDATE reply_speech_v2 SET state=?,reason=? WHERE device=? AND task=?',
                             (job.state, job.reason, job.device, job.task))
            store.db.execute('UPDATE speech_jobs SET state=? WHERE device=? AND task=?',
                             (marker, job.device, job.task))

    def _finish_retirement(self, job):
        if not job.done.is_set(): return False
        try: self.directories[job.owner.target].remove(job.generation)
        except OSError:
            job.state = 'cancelling'
            self._persist(job)
            return False
        job.state = job.retire_state or 'unavailable'
        job.bytes = 0; job.segments = []
        self._persist(job)
        self.jobs.pop(job.key, None)
        return True

    def _retire(self, job, state, reason):
        if job.retire_state is None:
            job.retire_state, job.reason = state, reason
        job.cancel.set()
        job.state = 'cancelling'
        self._persist(job)  # Invalidate first, before waiting for any worker.
        return self._finish_retirement(job)

    def expire(self):
        with self.lock:
            now = self.clock()
            for job in list(self.jobs.values()):
                if job.retire_state is not None:
                    self._finish_retirement(job)
                elif now >= job.created + ABSOLUTE_SECONDS or now >= job.idle:
                    self._retire(job, 'unavailable', 'tts_expired')
                elif job.state == 'processing' and now >= job.created + GENERATION_SECONDS:
                    self._retire(job, 'unavailable', 'tts_generation_timeout')

    def _capacity(self, target):
        ready = sum(job.state == 'ready' for job in self.jobs.values())
        active = sum(not job.done.is_set() for job in self.jobs.values())
        reserved = sum(job.bytes if job.state == 'ready' else MAX_PCM + OVERSHOOT
                       for job in self.jobs.values())
        if active >= MAX_ACTIVE: return 'tts_active_capacity'
        if ready >= MAX_READY: return 'tts_ready_capacity'
        if reserved + MAX_PCM + OVERSHOOT > DISK_BUDGET: return 'tts_disk_budget'
        try:
            if shutil.disk_usage(self.directories[target].path).free < MAX_PCM + OVERSHOOT:
                return 'tts_disk_budget'
        except OSError: return 'tts_disk_full'
        return None

    def drain(self):
        """Stop V2 work and remove PCM while retaining exclusive store leases.

        The HTTP server also owns legacy voice workers. It must finish their
        shutdown before releasing these same startup/recovery leases.
        """
        with self.lock:
            if not self.closed:
                self.closed = True
                self.stop.set()
                for job in list(self.jobs.values()): self._retire(job, 'unavailable', 'tts_shutdown')
        self.pool.shutdown(wait=True)
        self.cleaner.join(timeout=1)
        with self.lock:
            for job in list(self.jobs.values()):
                if not self._finish_retirement(job):
                    raise OSError('reply cleanup incomplete')

    def close(self):
        self.drain()
        with self.lock:
            for directory in self.directories.values(): directory.close()


class ReplySpeech:
    def __init__(self, target, store, speech, spools, *, decode=decode_speech_to_file):
        self.target, self.store, self.speech, self.spools, self.decode = target, store, speech, spools, decode

    def _row(self, device, task):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM reply_speech_v2 WHERE device=? AND task=?', (device, task)).fetchone()
        if row is None: raise NotFound('speech not requested')
        return dict(row)

    def _metadata(self, row):
        job = self.spools.jobs.get((self.target, row['device'], row['task']))
        ready = job is not None and job.state == 'ready'
        segments = [dict(segment) for segment in job.segments] if ready else []
        value = {'version': 2, 'task_id': row['task'], 'request_id': row['request_id'],
                 'result_revision': row['result_revision'], 'generation': row['generation'],
                 'state': row['state'], 'format': 'pcm_s16le', 'sample_rate': 16000,
                 'channels': 1, 'sample_bits': 16, 'chunk_bytes': CHUNK_BYTES,
                 'bytes': job.bytes if ready else 0,
                 'chunks': sum(segment['chunks'] for segment in segments),
                 'segment_count': len(segments), 'segments': segments}
        if row['reason'] is not None: value['reason'] = row['reason']
        return value

    def _generation(self, device, task, generation):
        self.store.get(device, task)
        if not isinstance(generation, str) or not _HEX32.fullmatch(generation):
            raise ReplyError(400, 'invalid_request')
        row = self._row(device, task)
        if row['generation'] != generation:
            raise ReplyError(409, 'speech_generation_changed')
        return row

    def create(self, device, task, request, revision=None):
        _, text, current = completed_result(self.target, device, self.store, task)
        try: identifier(request)
        except (ProtocolError, TypeError): raise ReplyError(400, 'invalid_request') from None
        if revision is not None and (not isinstance(revision, str) or not _HEX64.fullmatch(revision)):
            raise ReplyError(400, 'invalid_request')
        if revision is not None and revision != current:
            raise ReplyError(409, 'result_revision_changed')
        with self.spools.lock:
            self.spools.expire()
            try: old = self._row(device, task)
            except NotFound: old = None
            if old is not None:
                if old['request_id'] != request:
                    raise ReplyError(409, 'speech_request_conflict')
                if old['result_revision'] != current:
                    raise ReplyError(409, 'result_revision_changed')
                return self._metadata(old)
            reason = 'tts_shutdown' if self.spools.closed else None
            if not self.speech: reason = reason or 'tts_unavailable'
            if not callable(getattr(self.speech, 'speak_job', None)):
                reason = reason or 'tts_cancellation_unavailable'
            if not text.strip(): reason = reason or 'tts_empty_text'
            if len(text.encode('utf-8')) > MAX_TEXT: reason = reason or 'tts_text_too_long'
            reason = reason or self.spools._capacity(self.target)
            binding = [self.spools.incarnation, uuid.uuid4().hex, self.target, device, task, current, request]
            generation = hashlib.sha256(json.dumps(binding, separators=(',', ':')).encode()).hexdigest()[:32]
            with self.store.lock, self.store.db:
                legacy = self.store.db.execute('SELECT state FROM speech_jobs WHERE device=? AND task=?', (device, task)).fetchone()
                if legacy is not None: reason = reason or 'tts_already_requested'
                state = 'unavailable' if reason else 'processing'
                self.store.db.execute('INSERT INTO reply_speech_v2 VALUES(?,?,?,?,?,?,?)',
                    (device, task, request, current, generation, state, reason))
                if legacy is None:
                    self.store.db.execute('INSERT INTO speech_jobs VALUES(?,?,?)', (device, task, state))
            if reason: return self._metadata(self._row(device, task))
            now = self.spools.clock()
            job = _Job(self, device, task, request, current, generation, now, now + IDLE_SECONDS)
            self.spools.jobs[job.key] = job
            try: self.spools.pool.submit(self._run, job, text)
            except RuntimeError:
                job.done.set()
                self.spools._retire(job, 'unavailable', 'tts_shutdown')
            return self._metadata(self._row(device, task))

    def _check(self, job):
        if job.cancel.is_set(): raise SpeechFailure(job.reason or 'tts_cancelled')
        if self.spools.clock() >= job.created + GENERATION_SECONDS:
            raise SpeechFailure('tts_generation_timeout')

    def _run(self, job, text):
        reason = None
        phase = 'synthesis'
        try:
            self._check(job)
            remaining = job.created + GENERATION_SECONDS - self.spools.clock()
            encoded = self.speech.speak_job(text, cancel=job.cancel, timeout=remaining)
            self._check(job)
            if not isinstance(encoded, EncodedSpeech) or not isinstance(encoded.data, bytes) or not encoded.data:
                raise SpeechFailure('tts_synthesis_failed')
            if len(encoded.data) > MAX_ENCODED: raise SpeechFailure('tts_encoded_limit')
            phase = 'decode'
            with self.spools.directories[self.target].create(job.generation) as output:
                sink = _PCMSink(output, job.cancel)
                self.decode(encoded, sink, cancel=job.cancel,
                    timeout=min(DECODE_SECONDS, job.created + GENERATION_SECONDS - self.spools.clock()))
                self._check(job)
                size = os.fstat(output.fileno()).st_size
                if size > MAX_PCM: raise SpeechFailure('tts_playback_limit')
                if not size or size % 2 or size != sink.bytes: raise SpeechFailure('tts_decode_failed')
            del encoded
            # Store writers must not slip a new sequence/result between this
            # final snapshot and the ready journal commit. Keep the same lock
            # through _persist (its nested Store RLock acquisition is reentrant).
            with self.spools.lock, self.store.lock:
                self._check(job)
                _, _, current = completed_result(self.target, job.device, self.store, job.task)
                if current != job.revision: raise SpeechFailure('tts_result_revision_changed')
                if sum(value.state == 'ready' for value in self.spools.jobs.values()) >= MAX_READY:
                    raise SpeechFailure('tts_ready_capacity')
                job.bytes = size
                job.segments = [{'index': offset // SEGMENT_BYTES,
                    'bytes': min(SEGMENT_BYTES, size - offset),
                    'chunks': (min(SEGMENT_BYTES, size - offset) + CHUNK_BYTES - 1) // CHUNK_BYTES,
                    'first_frame': offset // 2, 'frames': min(SEGMENT_BYTES, size - offset) // 2}
                    for offset in range(0, size, SEGMENT_BYTES)]
                job.state = 'ready'; job.reason = None; job.done.set()
                self.spools._persist(job)
        except Exception as error:
            if isinstance(error, SpeechFailure): reason = error.reason
            elif isinstance(error, TimeoutError): reason = 'tts_decode_timeout' if phase == 'decode' else 'tts_generation_timeout'
            elif isinstance(error, OSError): reason = 'tts_disk_full' if error.errno in (errno.ENOSPC, errno.EDQUOT) else 'tts_decode_failed'
            else:
                known = getattr(error, 'reason', None)
                reason = known if known in ('tts_decode_timeout', 'tts_decode_failed', 'tts_playback_limit',
                    'tts_synthesis_timeout', 'tts_synthesis_failed') else ('tts_decode_failed' if phase == 'decode' else 'tts_synthesis_failed')
        finally:
            with self.spools.lock:
                if reason is not None or job.cancel.is_set():
                    # Work and decoder/provider reap have finished before the
                    # terminal marker can become visible to release idle gates.
                    job.done.set()
                    self.spools._retire(job, job.retire_state or 'unavailable', job.reason or reason or 'tts_cancelled')

    def status(self, device, task, generation):
        with self.spools.lock:
            self.store.get(device, task)
            self.spools.expire()
            return self._metadata(self._generation(device, task, generation))

    def cancel(self, device, task, generation):
        with self.spools.lock:
            row = self._generation(device, task, generation)
            self.spools.expire()
            job = self.spools.jobs.get((self.target, device, task))
            if job is not None:
                self.spools._retire(job, 'cancelled', 'tts_cancelled')
            row = self._row(device, task)
            # No job means already retired: do not change an expiry/restart
            # tombstone into a claim that an old generation was newly cancelled.
            return (202 if row['state'] == 'cancelling' else 200), self._metadata(row)

    def chunk(self, device, task, generation, segment, sequence):
        with self.spools.lock:
            self.store.get(device, task)
            self.spools.expire()
            row = self._generation(device, task, generation)
            if type(segment) is not int or type(sequence) is not int or segment < 0 or sequence < 0:
                raise ReplyError(400, 'invalid_request')
            job = self.spools.jobs.get((self.target, device, task))
            if row['state'] != 'ready' or job is None:
                raise ReplyError(410, 'speech_unavailable')
            if segment >= len(job.segments) or sequence >= job.segments[segment]['chunks']:
                raise ReplyError(400, 'invalid_request')
            entry = job.segments[segment]
            count = min(CHUNK_BYTES, entry['bytes'] - sequence * CHUNK_BYTES)
            try: pcm = self.spools.directories[self.target].read(generation, segment * SEGMENT_BYTES + sequence * CHUNK_BYTES, count)
            except OSError:
                self.spools._retire(job, 'unavailable', 'tts_spool_unavailable')
                raise ReplyError(410, 'speech_unavailable') from None
            job.idle = min(self.spools.clock() + IDLE_SECONDS, job.created + ABSOLUTE_SECONDS)
            segment_eof = sequence + 1 == entry['chunks']
            return {'version': 2, 'generation': generation, 'segment': segment, 'sequence': sequence,
                    'bytes': len(pcm), 'data': base64.b64encode(pcm).decode('ascii'),
                    'segment_eof': segment_eof, 'reply_eof': segment_eof and segment + 1 == len(job.segments)}


def reply_capabilities(available):
    return {'version': 1, 'text_pages': {'version': 1, 'page_bytes': 768, 'max_result_utf8_bytes': 65536},
            'speech_segments': {'version': 2, 'available': bool(available), 'segment_seconds': 20,
                'max_reply_seconds': 240, 'max_segments': 12, 'chunk_bytes': 4096,
                'sample_rate': 16000, 'channels': 1, 'sample_bits': 16, 'max_tts_utf8_bytes': 4096,
                'max_encoded_bytes': 3145728, 'generation_timeout_seconds': 120,
                'decode_timeout_seconds': 20, 'idle_ttl_seconds': 180, 'absolute_ttl_seconds': 900}}
