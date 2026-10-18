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
from .model_selection import snapshot, validate_snapshot
from .voice_errors import VoiceError, failure_reason, safe_timings, reason_stage
from .voice_observations import VoiceObservations


class VoiceJobs:
    def __init__(self, chat, speech, *, decode, workers=2):
        self.chat, self.speech, self.decode = chat, speech, decode
        self.store = chat.store
        self.lock = threading.RLock()
        self.captures = {}
        self.playback = {}
        self.controls = {}
        self.capture_started = {}
        self.closed = False
        self.observations = VoiceObservations(self.store)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pocket-voice')
        self.busy = threading.BoundedSemaphore(workers)
        with self.lock:
            with self.store.lock, self.store.db:
                self.store.db.executescript('''
                  CREATE TABLE IF NOT EXISTS audio_jobs (
                    device TEXT NOT NULL, request TEXT NOT NULL, chat TEXT NOT NULL,
                    state TEXT NOT NULL, text TEXT, task TEXT,
                    PRIMARY KEY(device,request));
                  CREATE TABLE IF NOT EXISTS speech_jobs (
                    device TEXT NOT NULL, task TEXT NOT NULL, state TEXT NOT NULL,
                    PRIMARY KEY(device,task));
                  CREATE TABLE IF NOT EXISTS audio_model_selections (
                    device TEXT NOT NULL, request TEXT NOT NULL, version INTEGER NOT NULL,
                    choice_id TEXT NOT NULL, provider TEXT, model TEXT,
                    PRIMARY KEY(device,request),
                    FOREIGN KEY(device,request) REFERENCES audio_jobs(device,request));
                ''')
                partial = self.store.db.execute("SELECT device,request,state FROM audio_jobs WHERE state IN ('capturing','processing')").fetchall()
                lost = self.store.db.execute("SELECT device,task FROM speech_jobs WHERE state IN ('processing','ready')").fetchall()
                self.store.db.execute("UPDATE audio_jobs SET state='unknown' WHERE state IN ('capturing','processing')")
                self.store.db.execute("UPDATE speech_jobs SET state='unavailable' WHERE state IN ('processing','ready')")
            # Critical recovery is committed before optional evidence can fail.
            for device, request, state in partial:
                self.observations.record(device, 'audio', request,
                    'capture_restart_loss' if state == 'capturing' else 'stt_restart_loss',
                    'capture' if state == 'capturing' else 'inference')
            for device, task in lost:
                self.observations.record(device, 'speech', task, 'tts_restart_loss', 'playback')

    def close(self):
        with self.lock:
            self.closed = True
            for control in self.controls.values():
                control['cancel'].set()
            for capture, _ in self.captures.values():
                capture.abort()
            self.captures.clear()
            self.capture_started.clear()
        close = getattr(self.speech, 'close', None)
        if callable(close):
            close()
        self.pool.shutdown(wait=True)
        with self.lock:
            self.playback.clear()
        self.observations.close()

    def runtime_metadata(self):
        metadata = getattr(self.speech, 'runtime_metadata', None)
        result = {'resident': False, 'ready': False, 'generation': 0, 'active': 0,
                  'pending': 0, 'worker_pid': 0, 'model_load_us': 0,
                  'startup_us': 0, 'restart_count': 0}
        try:
            value = metadata() if callable(metadata) else {}
            stt = value.get('stt', {}) if isinstance(value, dict) else {}
            if not isinstance(stt, dict):
                return {'stt': result}
            for name in ('resident', 'ready'):
                if type(stt.get(name)) is bool:
                    result[name] = stt[name]
            for name in ('generation', 'active', 'pending', 'worker_pid', 'model_load_us',
                         'startup_us', 'restart_count', 'worker_rss_bytes'):
                if type(stt.get(name)) is int and 0 <= stt[name] < 2**63:
                    result[name] = stt[name]
        except Exception:
            pass
        return {'stt': result}

    def _capture_timing(self, key):
        start = self.capture_started.pop(key, None)
        return {'capture': (time.monotonic_ns() - start) // 1000} if start is not None else {}

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
                self.observations.record(key[0], 'audio', key[1], 'capture_expired', 'capture', self._capture_timing(key))
        for key, (_, expires) in list(self.playback.items()):
            if expires < now:
                del self.playback[key]
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE speech_jobs SET state='unavailable' WHERE device=? AND task=?", key)
                self.observations.record(key[0], 'speech', key[1], 'tts_expired', 'playback')

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
            return {'request_id': request, 'state': row['state'], 'text': row['text'], 'task_id': row['task'],
                    **self.observations.status(device, 'audio', request)}

    def begin(self, device, target, request, chat_id, *, model_choice=None):
        identifier(device); identifier(request); identifier(chat_id)
        version=1 if model_choice is None else 2
        choice='default' if model_choice is None else model_choice
        selection=self.chat.service.validate_model_choice(choice)
        if not self.speech or not self.speech.stt_enabled:
            raise AudioError('speech recognition not configured')
        with self.lock:
            if self.closed:
                raise VoiceError('stt_shutdown')
            self._expire()
            with self.store.lock, self.store.db:
                old = self.store.db.execute('SELECT chat FROM audio_jobs WHERE device=? AND request=?', (device, request)).fetchone()
                if old:
                    if old['chat'] != chat_id:
                        raise Conflict('audio request already used')
                    pinned=self.store.db.execute('SELECT version,choice_id,provider,model FROM audio_model_selections WHERE device=? AND request=?',(device,request)).fetchone()
                    previous=(1,'default',None,None) if pinned is None else tuple(pinned)
                    if previous!=(version,choice,selection['provider'],selection['model']):
                        raise Conflict('audio request already used for another model choice')
                    return self.status(device, request)
                if len(self.captures) >= 4:
                    raise AudioError('capture capacity reached')
                self.store.db.execute("INSERT INTO audio_jobs VALUES(?,?,?,'capturing',NULL,NULL)", (device, request, chat_id))
                self.store.db.execute('INSERT INTO audio_model_selections VALUES(?,?,?,?,?,?)',
                    (device,request,version,choice,selection['provider'],selection['model']))
            self.captures[device, request] = (Capture(target, request), time.monotonic() + 45)
            self.capture_started[device, request] = time.monotonic_ns()
            return self.status(device, request)

    def chunk(self, device, request, sequence, data, *, timing=None):
        waiting=time.perf_counter_ns() if timing is not None else 0
        with self.lock:
            acquired=time.perf_counter_ns() if timing is not None else 0
            if timing is not None:timing[0]=(acquired-waiting)//1000
            try:
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
                    self.observations.record(device, 'audio', request, 'capture_invalid', 'capture',
                                             self._capture_timing((device, request)))
                    raise AudioError('capture aborted') from None
                return {'sequence': sequence, 'accepted_bytes': entry[0].size}
            finally:
                if timing is not None:timing[1]=(time.perf_counter_ns()-acquired)//1000

    def abort(self, device, request):
        identifier(device); identifier(request)
        with self.lock:
            control = self.controls.get((device, request))
            if control is not None and control['committed']:
                # ChatService may hold its database lock while submitting. Do not
                # wait for it or claim cancellation after submission intent.
                control['too_late'] = True
                return {'request_id': request, 'state': 'processing', 'text': None, 'task_id': None,
                        'reason': 'submission_too_late', 'stage': 'submission',
                        'timings_us': safe_timings(control['timings']), 'cancellation_too_late': True}
            entry = self.captures.pop((device, request), None)
            if entry:
                entry[0].abort(); self._update(device, request, 'failed')
                self.observations.record(device, 'audio', request, 'capture_aborted', 'capture',
                                         self._capture_timing((device, request)))
            if control is not None:
                control['cancel'].set()
                self._update(device, request, 'failed')
                self.observations.record(device, 'audio', request, 'stt_cancelled', 'inference', control['timings'])
            result = self.status(device, request)
            if result['state'] == 'submitted':
                result['cancellation_too_late'] = True
            return result

    def finish(self, device, request):
        with self.lock:
            if self.closed:
                raise VoiceError('stt_shutdown')
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
                self.controls[device, request] = {'cancel': threading.Event(), 'committed': False,
                    'too_late': False, 'timings': self._capture_timing((device, request))}
                self.pool.submit(self._transcribe, device, request, pcm)
            except Exception:
                self.busy.release(); self._update(device, request, 'failed')
                self.controls.pop((device, request), None)
                self.observations.record(device, 'audio', request, 'capture_empty', 'capture',
                                         self._capture_timing((device, request)))
                raise
            return self.status(device, request)

    def _transcribe(self, device, request, pcm):
        with self.lock:
            control = self.controls[device, request]
        timings = control['timings']
        stage = 'inference'
        try:
            if control['cancel'].is_set():
                raise VoiceError('stt_shutdown' if self.closed else 'stt_cancelled')
            started = time.monotonic_ns()
            job = getattr(self.speech, 'transcribe_job', None)
            try:
                if callable(job):
                    result = job(pcm, cancel=control['cancel'])
                    text = result['transcript']
                    timings.update(safe_timings(result.get('timings_us')))
                else:
                    text = self.speech.transcribe(pcm)
            finally:
                timings.setdefault('stt_total', (time.monotonic_ns() - started) // 1000)
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 4096:
                raise VoiceError('stt_no_confident_speech')
            with self.store.lock:
                chat_id = self.store.db.execute('SELECT chat FROM audio_jobs WHERE device=? AND request=?', (device, request)).fetchone()[0]
                pinned=self.store.db.execute('SELECT version,choice_id,provider,model FROM audio_model_selections WHERE device=? AND request=?',(device,request)).fetchone()
            version,choice,provider,model=(1,'default',None,None) if pinned is None else tuple(pinned)
            selection=validate_snapshot({'choice_id':choice,'provider':provider,'model':model})
            data={'version':version,'request_id':request,'text':text.strip()}
            if version==2:data['model_choice']=choice
            with self.lock:
                if control['cancel'].is_set() or self.closed:
                    raise VoiceError('stt_shutdown' if self.closed else 'stt_cancelled')
                control['committed'] = True
                stage = 'submission'
                self.observations.record(device, 'audio', request, 'unknown', stage, timings)
            started = time.monotonic_ns()
            try:
                task = self.chat.submit(device, chat_id, data,selection_snapshot=selection)
            finally:
                timings['submission'] = (time.monotonic_ns() - started) // 1000
            with self.lock:
                self._update(device, request, 'submitted', text.strip(), task['request_id'])
                if control['too_late']:
                    self.observations.record(device, 'audio', request, 'submission_too_late', stage, timings)
                self.observations.record(device, 'audio', request, 'ok', stage, timings)
        except Exception as error:
            timings.update(safe_timings(getattr(error, 'timings_us', None)))
            reason = failure_reason(error, 'submission_unknown' if stage == 'submission' else 'stt_worker_failed')
            if not control['committed'] and control['cancel'].is_set():
                reason = 'stt_shutdown' if self.closed else 'stt_cancelled'
            with self.lock:
                self._update(device, request, 'failed' if reason in ('stt_cancelled', 'stt_shutdown') else 'unknown')
                self.observations.record(device, 'audio', request, reason, reason_stage(reason, stage), timings)
        finally:
            with self.lock:
                self.controls.pop((device, request), None)
            del pcm
            self.busy.release()

    def speak(self, device, task_id):
        task = self.chat.service.refresh(device, task_id)
        if task['state'] != 'completed' or not task['result']:
            raise AudioError('no completed response to speak')
        if not self.speech:
            raise AudioError('speech synthesis not configured')
        with self.lock:
            if self.closed:
                raise VoiceError('tts_shutdown')
            self._expire()
            too_long = len(task['result'].encode()) > 4096
            with self.store.lock, self.store.db:
                old = self.store.db.execute('SELECT state FROM speech_jobs WHERE device=? AND task=?', (device, task_id)).fetchone()
                if old:
                    return self.speech_status(device, task_id)
                if too_long:
                    self.store.db.execute("INSERT INTO speech_jobs VALUES(?,?,'unavailable')", (device, task_id))
                else:
                    if not self.busy.acquire(blocking=False):
                        raise AudioError('voice worker busy')
                    try:
                        self.store.db.execute("INSERT INTO speech_jobs VALUES(?,?,'processing')", (device, task_id))
                    except BaseException:
                        self.busy.release()
                        raise
            if too_long:
                self.observations.record(device, 'speech', task_id, 'tts_text_too_long', 'synthesis')
                # The current rejection is known even if its optional observation
                # could not be stored. Later reads honestly report unknown evidence.
                return {**self.speech_status(device, task_id),
                        'reason': 'tts_text_too_long', 'stage': 'synthesis'}
            text = task['result']
            try:
                self.pool.submit(self._synthesize, device, task_id, text)
            except BaseException:
                self.busy.release()
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE speech_jobs SET state='unavailable' WHERE device=? AND task=?", (device, task_id))
                self.observations.record(device, 'speech', task_id, 'tts_synthesis_failed', 'synthesis')
                raise
            return self.speech_status(device, task_id)

    def _synthesize(self, device, task_id, text):
        state = 'unavailable'
        stage = 'synthesis'; reason = 'tts_synthesis_failed'; timings = {}
        try:
            if self.closed:
                raise VoiceError('tts_shutdown')
            started = time.monotonic_ns()
            try:
                encoded = self.speech.speak(text)
            finally:
                timings['synthesis'] = (time.monotonic_ns() - started) // 1000
            stage = 'decode'; reason = 'tts_decode_failed'; started = time.monotonic_ns()
            try:
                pcm = self.decode(encoded)
            finally:
                timings['decode'] = (time.monotonic_ns() - started) // 1000
            if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
                raise VoiceError('tts_decode_failed')
            if len(pcm) >= 960000:
                raise VoiceError('tts_playback_limit')
            stage = 'playback'
            with self.lock:
                if self.closed:
                    raise VoiceError('tts_shutdown')
                if len(self.playback) >= 4:
                    raise VoiceError('tts_playback_capacity')
                self.playback[device, task_id] = (pcm, time.monotonic() + 180)
            state = 'ready'; reason = 'ok'
        except Exception as error:
            reason = failure_reason(error, reason)
            if isinstance(error, TimeoutError):
                reason = 'tts_synthesis_timeout' if stage == 'synthesis' else 'tts_decode_timeout'
        finally:
            with self.lock:
                with self.store.lock, self.store.db:
                    self.store.db.execute('UPDATE speech_jobs SET state=? WHERE device=? AND task=?', (state, device, task_id))
                self.observations.record(device, 'speech', task_id, reason, stage, timings)
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
                    'chunks': (length + 4095) // 4096, 'sample_rate': 16000, 'channels': 1, 'format': 'pcm_s16le',
                    **self.observations.status(device, 'speech', task_id)}

    def playback_chunk(self, device, task_id, sequence):
        with self.lock:
            status = self.speech_status(device, task_id)
            if type(sequence) is not int or sequence < 0 or sequence >= status['chunks'] or status['state'] != 'ready':
                raise AudioError('playback chunk unavailable')
            pcm = self.playback[device, task_id][0][sequence * 4096:(sequence + 1) * 4096]
            return {'sequence': sequence, 'data': base64.b64encode(pcm).decode('ascii')}
