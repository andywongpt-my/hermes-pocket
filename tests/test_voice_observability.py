"""Synthetic cancellation, durable safe observations and TTS stage attribution."""
import base64
import http.client
import importlib.util
import json
import threading
import time
import unittest
from unittest.mock import patch

from pocket.audio import AudioError
from pocket.chat import ChatService
from pocket.linux_voice import EncodedSpeech, decode_speech_pcm
from pocket.server import make_server
from pocket.service import Service, FakeHermes
from pocket.store import Store, NotFound
from pocket.voice_jobs import VoiceJobs


class ControlledSpeech:
    stt_enabled = True
    def __init__(self):
        self.started = threading.Event(); self.release = threading.Event()
        self.syntheses = []; self.cancel = None; self.closed = False
    def transcribe_job(self, pcm, *, cancel=None):
        self.cancel = cancel; self.started.set(); self.release.wait(2)
        return {'transcript': 'synthetic transcript', 'timings_us': {'queue': 2, 'inference': 3}}
    def transcribe(self, pcm):
        self.started.set(); self.release.wait(2); return 'synthetic transcript'
    def speak(self, text):
        self.syntheses.append(text); return b'\0\0' * 160
    def close(self):
        self.closed = True; self.release.set()


class VoiceObservationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:'); self.backend = FakeHermes()
        self.chat = ChatService(Service(self.store, self.backend))
        self.speech = ControlledSpeech()
        self.voice = VoiceJobs(self.chat, self.speech, decode=lambda x: x)
        self.serial = 0
    def tearDown(self):
        self.speech.release.set(); self.voice.close(); self.store.close()
    def begin(self, rid='one'):
        self.voice.begin('device', 'linux', rid, 'chat')
        self.voice.chunk('device', rid, 0, 'AAA=')
    def wait(self, read, state):
        until = time.monotonic() + 2
        while time.monotonic() < until:
            value = read()
            if value['state'] == state: return value
            time.sleep(.005)
        self.fail('synthetic job did not reach ' + state)
    def complete(self, text='synthetic reply'):
        self.serial += 1
        task = self.chat.submit('device', 'chat', {'version': 1, 'request_id': 'text' + str(self.serial), 'text': 'synthetic'})
        self.backend.complete(task['run_id'], text)
        return task['request_id']

    def test_abort_active_transcription_acknowledges_cancel_and_never_submits_late_result(self):
        self.begin(); self.voice.finish('device', 'one'); self.assertTrue(self.speech.started.wait(1))
        value = self.voice.abort('device', 'one')
        self.assertEqual(value.get('reason'), 'stt_cancelled')
        self.assertTrue(self.speech.cancel.is_set())
        self.speech.release.set()
        self.voice.pool.shutdown(wait=True)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
        self.assertEqual(self.voice.status('device', 'one')['reason'], 'stt_cancelled')

    def test_legacy_transcriber_cannot_admit_after_acknowledged_cancellation(self):
        self.speech.transcribe_job = None
        self.begin(); self.voice.finish('device', 'one'); self.assertTrue(self.speech.started.wait(1))
        self.assertEqual(self.voice.abort('device', 'one').get('reason'), 'stt_cancelled')
        self.speech.release.set(); self.voice.pool.shutdown(wait=True)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)

    def test_abort_after_submission_intent_is_honestly_too_late_without_holding_voice_lock(self):
        self.speech.release.set(); started = threading.Event(); release = threading.Event()
        original = self.chat.submit
        def submit(*a, **kw):
            started.set(); release.wait(2); return original(*a, **kw)
        with patch.object(self.chat, 'submit', submit):
            self.begin(); self.voice.finish('device', 'one'); self.assertTrue(started.wait(1))
            try:
                before = time.monotonic(); result = self.voice.abort('device', 'one')
                self.assertLess(time.monotonic() - before, .2)
                self.assertTrue(result.get('cancellation_too_late'))
            finally: release.set()
            self.wait(lambda: self.voice.status('device', 'one'), 'submitted')

    def test_capture_abort_invalid_expiry_and_restart_are_distinct(self):
        self.begin(); self.assertEqual(self.voice.abort('device', 'one').get('reason'), 'capture_aborted')
        self.begin('bad')
        with self.assertRaises(AudioError): self.voice.chunk('device', 'bad', 9, 'AAA=')
        self.assertEqual(self.voice.status('device', 'bad').get('reason'), 'capture_invalid')
        self.begin('expired')
        capture, _ = self.voice.captures['device', 'expired']
        self.voice.captures['device', 'expired'] = (capture, 0)
        self.assertEqual(self.voice.status('device', 'expired').get('reason'), 'capture_expired')
        self.begin('restart'); self.voice.close()
        self.voice = VoiceJobs(self.chat, self.speech, decode=lambda x: x)
        self.assertEqual(self.voice.status('device', 'restart').get('reason'), 'capture_restart_loss')

    def test_status_timings_and_detail_tables_are_additive_scoped_and_payload_free(self):
        self.speech.release.set(); self.begin(); self.voice.finish('device', 'one')
        value = self.wait(lambda: self.voice.status('device', 'one'), 'submitted')
        self.assertEqual(value.get('reason'), 'ok')
        self.assertEqual(value.get('timings_us', {}).get('inference'), 3)
        self.assertTrue(all(type(v) is int for v in value['timings_us'].values()))
        with self.assertRaises(NotFound): self.voice.status('other', 'one')
        columns = [r[1] for r in self.store.db.execute('PRAGMA table_info(audio_jobs)')]
        self.assertEqual(columns, ['device', 'request', 'chat', 'state', 'text', 'task'])
        for table in ('voice_details', 'voice_timings'):
            rows = self.store.db.execute('SELECT * FROM ' + table).fetchall()
            self.assertTrue(rows)
            self.assertNotIn('synthetic transcript', str([tuple(r) for r in rows]))

    def test_full_text_up_to_4096_bytes_is_passed_without_truncation(self):
        text = '语' * 1300
        task = self.complete(text); self.voice.speak('device', task)
        self.wait(lambda: self.voice.speech_status('device', task), 'ready')
        self.assertEqual(self.speech.syntheses, [text])
        self.assertEqual(self.store.get('device', task)['result'], text)

    def test_excessive_text_is_visibly_rejected_before_synthesis(self):
        text = '语' * 1400
        task = self.complete(text); value = self.voice.speak('device', task)
        self.assertEqual(value.get('reason'), 'tts_text_too_long')
        self.assertEqual(value['state'], 'unavailable')
        self.assertEqual(self.speech.syntheses, [])
        self.assertEqual(self.store.get('device', task)['result'], text)

    def test_synthesis_failure_decode_failure_and_decode_timeout_have_distinct_reasons(self):
        from pocket.voice_errors import VoiceError
        for reason, stage in [('tts_synthesis_failed', 'synthesis'), ('tts_synthesis_timeout', 'synthesis'),
                              ('tts_decode_failed', 'decode'), ('tts_decode_timeout', 'decode')]:
            task = self.complete(reason)
            failure = VoiceError(reason)
            target = self.speech if stage == 'synthesis' else self.voice
            attribute = 'speak' if stage == 'synthesis' else 'decode'
            with patch.object(target, attribute, side_effect=failure):
                self.voice.speak('device', task)
                value = self.wait(lambda: self.voice.speech_status('device', task), 'unavailable')
            self.assertEqual(value.get('reason'), reason)
            self.assertIn(stage, value.get('timings_us', {}))

    def test_playback_at_30_seconds_is_rejected_and_expiry_is_not_a_synthesis_error(self):
        task = self.complete()
        self.voice.decode = lambda x: b'\0\0' * 480000
        self.voice.speak('device', task)
        value = self.wait(lambda: self.voice.speech_status('device', task), 'unavailable')
        self.assertEqual(value.get('reason'), 'tts_playback_limit')

    def test_ready_playback_expiry_retains_explicit_reason(self):
        task = self.complete(); self.voice.speak('device', task)
        self.wait(lambda: self.voice.speech_status('device', task), 'ready')
        pcm, _ = self.voice.playback['device', task]; self.voice.playback['device', task] = (pcm, 0)
        self.assertEqual(self.voice.speech_status('device', task).get('reason'), 'tts_expired')
        self.voice.speak('device', task)
        self.assertEqual(len(self.speech.syntheses), 1)

    def test_close_owns_speech_runtime_and_prevents_late_admission(self):
        self.begin(); self.voice.finish('device', 'one'); self.assertTrue(self.speech.started.wait(1))
        self.voice.close()
        self.assertTrue(self.speech.closed)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)

    def test_stt_failures_preserve_exact_queue_startup_and_worker_stage_reasons(self):
        from pocket.voice_errors import VoiceError
        for index, (reason, stage) in enumerate([
                ('stt_queue_full', 'queue'), ('stt_queue_timeout', 'queue'),
                ('stt_startup_timeout', 'startup'), ('stt_restart_backoff', 'startup'),
                ('stt_worker_crash', 'inference'), ('stt_protocol_error', 'inference'),
                ('stt_no_confident_speech', 'inference')]):
            rid = 'failed' + str(index)
            with patch.object(self.speech, 'transcribe_job', side_effect=VoiceError(reason, timings_us={stage: 9})):
                self.begin(rid); self.voice.finish('device', rid)
                value = self.wait(lambda: self.voice.status('device', rid), 'unknown')
            self.assertEqual(value.get('reason'), reason)
            self.assertEqual(value.get('stage'), stage)
            self.assertEqual(value.get('timings_us', {}).get(stage), 9)


class DecoderReasonTests(unittest.TestCase):
    def test_decoder_timeout_and_limit_are_visible(self):
        import subprocess
        speech = EncodedSpeech(b'synthetic', 'audio/mpeg', 'fishaudio')
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('fixture', 20)):
            with self.assertRaises(AudioError) as caught: decode_speech_pcm(speech)
            self.assertEqual(getattr(caught.exception, 'reason', None), 'tts_decode_timeout')
        with patch('subprocess.run', return_value=type('Output', (), {'stdout': b'\0' * 960000})()):
            with self.assertRaises(AudioError) as caught: decode_speech_pcm(speech)
            self.assertEqual(getattr(caught.exception, 'reason', None), 'tts_playback_limit')


class JournalTests(unittest.TestCase):
    def test_journal_is_integer_only_private_and_bounded_when_writer_stalls(self):
        self.assertIsNotNone(importlib.util.find_spec('pocket.voice_observations'), 'observations not implemented')
        from pocket.voice_observations import NumericVoiceLog, journal_fields
        ready = threading.Event(); release = threading.Event(); rows = []
        def write(fd, row):
            ready.set(); release.wait(1); rows.append(json.loads(row)); return len(row)
        with patch('os.write', write):
            log = NumericVoiceLog()
            try:
                fields = journal_fields('private-device', 'audio', 'private-request', 'inference', 'ok', 4)
                log.emit(fields); self.assertTrue(ready.wait(1))
                started = time.monotonic()
                for _ in range(1000): log.emit(fields)
                self.assertLess(time.monotonic() - started, .2)
                self.assertLessEqual(log._queue.qsize(), 64)
                log.emit([True] * len(fields))
            finally: log.close(); release.set()
        self.assertTrue(all(type(v) is int for v in fields))
        self.assertNotIn('private', json.dumps(fields))


if __name__ == '__main__': unittest.main()
