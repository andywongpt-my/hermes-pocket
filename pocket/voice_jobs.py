"""Bounded audio jobs with durable admission and device-scoped playback.

Raw audio stays in memory. Restarted/expired jobs are never synthesized,
transcribed or submitted again automatically. Providers are injected.
"""
import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from .audio import Capture, AudioError
from .schema import identifier
from .store import Conflict, NotFound


class VoiceJobs:
    def __init__(self, chat, speech, *, decode, workers=2):
        self.chat, self.speech, self.decode = chat, speech, decode
        self.store = chat.store
        self.lock = threading.RLock()
        self.captures = {}
        self.playback = {}
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pocket-voice')
        self.busy = threading.BoundedSemaphore(workers)
        with self.store.lock, self.store.db:
            self.store.db.executescript('''
              CREATE TABLE IF NOT EXISTS audio_jobs (
                device TEXT NOT NULL, request TEXT NOT NULL, chat TEXT NOT NULL,
                state TEXT NOT NULL, text TEXT, task TEXT,
                PRIMARY KEY(device,request));
              CREATE TABLE IF NOT EXISTS speech_jobs (
                device TEXT NOT NULL, task TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(device,task));
            ''')
            self.store.db.execute("UPDATE audio_jobs SET state='unknown' WHERE state IN ('capturing','processing')")
            self.store.db.execute("UPDATE speech_jobs SET state='unavailable' WHERE state IN ('processing','ready')")

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            for capture, _ in self.captures.values():
                capture.abort()
            self.captures.clear()
            self.playback.clear()

    def capabilities(self):
        return {'stt': bool(self.speech and self.speech.stt_enabled), 'tts': bool(self.speech),
                'format': 'pcm_s16le', 'sample_rate': 16000, 'channels': 1,
                'max_seconds': 30, 'chunk_bytes': 4096}

    def _expire(self):
        now = time.monotonic()
        for key, (capture, expires) in list(self.captures.items()):
            if expires < now:
                capture.abort()
                del self.captures[key]
                self._update(*key, 'failed')
        for key, (_, expires) in list(self.playback.items()):
            if expires < now:
                del self.playback[key]
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE speech_jobs SET state='unavailable' WHERE device=? AND task=?", key)

    def _update(self, device, request, state, text=None, task=None):
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE audio_jobs SET state=?,text=COALESCE(?,text),task=COALESCE(?,task) WHERE device=? AND request=?',
                                  (state, text, task, device, request))

    def status(self, device, request):
        identifier(device); identifier(request)
        with self.lock:
            self._expire()
            with self.store.lock:
                row = self.store.db.execute('SELECT * FROM audio_jobs WHERE device=? AND request=?', (device, request)).fetchone()
            if row is None:
                raise NotFound('audio job not found')
            return {'request_id': request, 'state': row['state'], 'text': row['text'], 'task_id': row['task']}

    def begin(self, device, target, request, chat_id):
        identifier(device); identifier(request); identifier(chat_id)
        if not self.speech or not self.speech.stt_enabled:
            raise AudioError('speech recognition not configured')
        with self.lock:
            self._expire()
            with self.store.lock, self.store.db:
                old = self.store.db.execute('SELECT chat FROM audio_jobs WHERE device=? AND request=?', (device, request)).fetchone()
                if old:
                    if old['chat'] != chat_id:
                        raise Conflict('audio request already used')
                    return self.status(device, request)
                if len(self.captures) >= 4:
                    raise AudioError('capture capacity reached')
                self.store.db.execute("INSERT INTO audio_jobs VALUES(?,?,?,'capturing',NULL,NULL)", (device, request, chat_id))
            self.captures[device, request] = (Capture(target, request), time.monotonic() + 45)
            return self.status(device, request)

    def chunk(self, device, request, sequence, data):
        with self.lock:
            self._expire()
            entry = self.captures.get((device, request))
            if entry is None:
                raise AudioError('capture unavailable')
            try:
                if not isinstance(data, str) or len(data) > 5464:
                    raise AudioError('invalid chunk')
                entry[0].append(sequence, base64.b64decode(data, validate=True))
            except Exception:
                entry[0].abort(); self.captures.pop((device, request), None)
                self._update(device, request, 'failed')
                raise AudioError('capture aborted') from None
            return {'sequence': sequence, 'accepted_bytes': entry[0].size}

    def abort(self, device, request):
        with self.lock:
            entry = self.captures.pop((device, request), None)
            if entry:
                entry[0].abort(); self._update(device, request, 'failed')
            return self.status(device, request)

    def finish(self, device, request):
        with self.lock:
            job = self.status(device, request)
            if job['state'] != 'capturing':
                return job
            if not self.busy.acquire(blocking=False):
                raise AudioError('voice worker busy')
            entry = self.captures.pop((device, request), None)
            try:
                if entry is None:
                    raise AudioError('capture unavailable')
                pcm = entry[0].finish()
                self._update(device, request, 'processing')
                self.pool.submit(self._transcribe, device, request, pcm)
            except Exception:
                self.busy.release(); self._update(device, request, 'failed')
                raise
            return self.status(device, request)

    def _transcribe(self, device, request, pcm):
        try:
            text = self.speech.transcribe(pcm)
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 4096:
                raise AudioError('empty transcript')
            with self.store.lock:
                chat_id = self.store.db.execute('SELECT chat FROM audio_jobs WHERE device=? AND request=?', (device, request)).fetchone()[0]
            task = self.chat.submit(device, chat_id, {'version': 1, 'request_id': request, 'text': text.strip()})
            self._update(device, request, 'submitted', text.strip(), task['request_id'])
        except Exception:
            self._update(device, request, 'unknown')
        finally:
            del pcm
            self.busy.release()

    def speak(self, device, task_id):
        task = self.chat.service.refresh(device, task_id)
        if task['state'] != 'completed' or not task['result']:
            raise AudioError('no completed response to speak')
        if not self.speech:
            raise AudioError('speech synthesis not configured')
        with self.lock:
            self._expire()
            with self.store.lock, self.store.db:
                old = self.store.db.execute('SELECT state FROM speech_jobs WHERE device=? AND task=?', (device, task_id)).fetchone()
                if old:
                    return self.speech_status(device, task_id)
                if not self.busy.acquire(blocking=False):
                    raise AudioError('voice worker busy')
                try:
                    self.store.db.execute("INSERT INTO speech_jobs VALUES(?,?,'processing')", (device, task_id))
                except BaseException:
                    self.busy.release()
                    raise
            text = task['result']
            # Make long-answer truncation explicit; keep the complete task text.
            spoken = text if len(text.encode()) <= 3500 else text.encode()[:3400].decode('utf-8', errors='ignore') + '\nThe full answer is available in Tasks.'
            try:
                self.pool.submit(self._synthesize, device, task_id, spoken)
            except BaseException:
                self.busy.release()
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE speech_jobs SET state='unavailable' WHERE device=? AND task=?", (device, task_id))
                raise
            return self.speech_status(device, task_id)

    def _synthesize(self, device, task_id, text):
        state = 'unavailable'
        try:
            pcm = self.decode(self.speech.speak(text))
            if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 960000:
                raise AudioError('invalid playback')
            with self.lock:
                if len(self.playback) >= 4:
                    raise AudioError('playback capacity reached')
                self.playback[device, task_id] = (pcm, time.monotonic() + 180)
            state = 'ready'
        except Exception:
            pass
        finally:
            with self.store.lock, self.store.db:
                self.store.db.execute('UPDATE speech_jobs SET state=? WHERE device=? AND task=?', (state, device, task_id))
            self.busy.release()

    def speech_status(self, device, task_id):
        self.chat.service.store.get(device, task_id)  # Owner isolation even for metadata.
        with self.lock:
            self._expire()
            with self.store.lock:
                row = self.store.db.execute('SELECT state FROM speech_jobs WHERE device=? AND task=?', (device, task_id)).fetchone()
            entry = self.playback.get((device, task_id))
            length = len(entry[0]) if entry else 0
            return {'state': row['state'] if row else 'not_requested', 'bytes': length,
                    'chunks': (length + 4095) // 4096, 'sample_rate': 16000, 'channels': 1, 'format': 'pcm_s16le'}

    def playback_chunk(self, device, task_id, sequence):
        with self.lock:
            status = self.speech_status(device, task_id)
            if type(sequence) is not int or sequence < 0 or sequence >= status['chunks'] or status['state'] != 'ready':
                raise AudioError('playback chunk unavailable')
            pcm = self.playback[device, task_id][0][sequence * 4096:(sequence + 1) * 4096]
            return {'sequence': sequence, 'data': base64.b64encode(pcm).decode('ascii')}
