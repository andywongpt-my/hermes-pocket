"""Offline resident subprocess and real pinned-helper reuse contracts."""
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from pocket.audio import AudioError


class ResidentPoolTests(unittest.TestCase):
    def pool(self, mode='ok', **kw):
        self.assertIsNotNone(importlib.util.find_spec('pocket.stt_pool'),
                             'resident-STT pool not implemented')
        from pocket.stt_pool import ResidentSTTPool
        settings = {'helper_path': str(Path(__file__).resolve()),
                    'helper_sha256': '0' * 64,
                    'model_path': str(Path(__file__).resolve().parent), 'language': mode}
        pool = ResidentSTTPool(sys.executable, settings,
            worker_path=Path(__file__).parent / 'fixtures' / 'resident_stt.py',
            startup_timeout=.8, queue_timeout=.7, job_timeout=1.5, **kw)
        self.addCleanup(pool.close)
        return pool

    def invoke_async(self, pool, cancel=None):
        result = {}
        def run():
            try:
                result['value'] = pool.transcribe_job({'fixture': True}, cancel=cancel)
            except AudioError as error:
                result['reason'] = error.reason
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(lambda: thread.join(3))
        return thread, result

    def wait_active(self, pool, pending=0):
        until = time.monotonic() + 1
        while time.monotonic() < until:
            meta = pool.runtime_metadata()
            if meta['active'] == 1 and meta['pending'] == pending:
                return
            time.sleep(.005)
        self.fail('synthetic job was not admitted')

    def test_reuses_one_warmed_worker_for_distinct_jobs(self):
        pool = self.pool()
        cold = pool.runtime_metadata()
        self.assertEqual((cold['resident'], cold['ready'], cold['generation']), (True, False, 0))
        pool.warm()
        one = pool.transcribe_job({'fixture': True})
        two = pool.transcribe_job({'fixture': True})
        self.assertEqual(one['transcript'], two['transcript'])
        self.assertEqual(pool.runtime_metadata()['generation'], 1)
        self.assertEqual(pool.runtime_metadata()['model_load_us'], 7)
        self.assertEqual(one['timings_us']['inference'], 11)
        self.assertTrue(all(type(v) is int and v >= 0 for v in one['timings_us'].values()))

    def test_active_plus_one_pending_rejects_third_without_execution(self):
        pool = self.pool('delay')
        pool.warm()
        first, a = self.invoke_async(pool)
        self.wait_active(pool)
        second, b = self.invoke_async(pool)
        self.wait_active(pool, 1)
        with self.assertRaises(AudioError) as raised:
            pool.transcribe_job({'fixture': True})
        self.assertEqual(raised.exception.reason, 'stt_queue_full')
        first.join(2); second.join(2)
        self.assertIn('value', a); self.assertIn('value', b)
        self.assertEqual(pool.runtime_metadata()['pending'], 0)

    def test_queued_cancellation_does_not_kill_active_worker(self):
        pool = self.pool('delay')
        pool.warm(); pid = pool.runtime_metadata()['worker_pid']
        first, a = self.invoke_async(pool); self.wait_active(pool)
        cancel = threading.Event()
        second, b = self.invoke_async(pool, cancel); self.wait_active(pool, 1)
        cancel.set(); second.join(1); first.join(2)
        self.assertEqual(b.get('reason'), 'stt_cancelled')
        self.assertIn('value', a)
        self.assertEqual(pool.runtime_metadata()['worker_pid'], pid)

    def test_active_cancellation_reaps_only_its_generation(self):
        pool = self.pool('hang'); pool.warm()
        cancel = threading.Event()
        thread, result = self.invoke_async(pool, cancel); self.wait_active(pool)
        cancel.set(); thread.join(2)
        self.assertEqual(result.get('reason'), 'stt_cancelled')
        self.assertFalse(pool.runtime_metadata()['ready'])
        self.assertEqual(pool.runtime_metadata()['worker_pid'], 0)

    def test_active_cancel_never_kills_the_distinct_queued_request(self):
        pool = self.pool('delay'); pool.warm()
        original = pool.runtime_metadata()['worker_pid']
        cancel = threading.Event()
        first, a = self.invoke_async(pool, cancel); self.wait_active(pool)
        second, b = self.invoke_async(pool); self.wait_active(pool, 1)
        cancel.set(); first.join(1); second.join(2)
        self.assertEqual(a.get('reason'), 'stt_cancelled')
        self.assertIn('value', b)
        self.assertNotEqual(pool.runtime_metadata()['worker_pid'], original)
        self.assertEqual(pool.runtime_metadata()['generation'], 2)

    def test_failed_queue_preserves_elapsed_stage_timing(self):
        pool = self.pool('delay'); pool.queue_timeout = .04; pool.warm()
        first, result = self.invoke_async(pool); self.wait_active(pool)
        with self.assertRaises(AudioError) as caught:
            pool.transcribe_job({'fixture': True})
        self.assertGreaterEqual(caught.exception.timings_us.get('queue', -1), 30000)
        first.join(1)

    def test_failed_restart_startup_preserves_elapsed_stage_timing(self):
        pool = self.pool('startup_hang'); pool.startup_timeout = .06
        with self.assertRaises(AudioError) as caught:
            pool.transcribe_job({'fixture': True})
        self.assertGreaterEqual(caught.exception.timings_us.get('startup', -1), 50000)

    def test_expired_queued_job_never_runs_after_active_finishes(self):
        pool = self.pool('delay'); pool.queue_timeout = .06; pool.warm()
        first, a = self.invoke_async(pool); self.wait_active(pool)
        second, b = self.invoke_async(pool); second.join(1); first.join(1)
        self.assertEqual(b.get('reason'), 'stt_queue_timeout')
        self.assertIn('value', a)
        self.assertEqual(pool.runtime_metadata()['generation'], 1)

    def test_total_deadline_terminates_hung_job(self):
        pool = self.pool('hang'); pool.job_timeout = .08; pool.warm()
        started = time.monotonic()
        with self.assertRaises(AudioError) as raised:
            pool.transcribe_job({'fixture': True})
        self.assertEqual(raised.exception.reason, 'stt_timeout')
        self.assertLess(time.monotonic() - started, .8)
        self.assertEqual(pool.runtime_metadata()['worker_pid'], 0)

    def test_startup_has_a_bound_and_metadata_never_retries(self):
        pool = self.pool('startup_hang'); pool.startup_timeout = .08
        with self.assertRaises(AudioError) as raised:
            pool.warm()
        self.assertEqual(raised.exception.reason, 'stt_startup_timeout')
        self.assertEqual(pool.runtime_metadata()['worker_pid'], 0)
        self.assertEqual(pool.runtime_metadata()['generation'], 1)
        self.assertEqual(pool.runtime_metadata()['generation'], 1)

    def test_crashed_work_is_not_replayed_and_restarts_have_a_budget(self):
        pool = self.pool('crash'); pool.warm()
        for generation in range(1, 4):
            with self.assertRaises(AudioError) as raised:
                pool.transcribe_job({'fixture': True})
            self.assertEqual(raised.exception.reason, 'stt_worker_crash')
            self.assertEqual(pool.runtime_metadata()['generation'], generation)
        with self.assertRaises(AudioError) as raised:
            pool.transcribe_job({'fixture': True})
        self.assertEqual(raised.exception.reason, 'stt_restart_backoff')
        self.assertEqual(pool.runtime_metadata()['restart_count'], 2)

    def test_stale_generation_id_schema_and_oversized_frames_are_terminal(self):
        for mode in ('generation', 'id', 'schema', 'oversize'):
            with self.subTest(mode=mode):
                pool = self.pool(mode); pool.warm()
                with self.assertRaises(AudioError) as raised:
                    pool.transcribe_job({'fixture': True})
                self.assertEqual(raised.exception.reason, 'stt_protocol_error')
                self.assertNotIn('private', str(raised.exception))
                self.assertEqual(pool.runtime_metadata()['worker_pid'], 0)

    def test_no_confident_speech_keeps_warmed_worker(self):
        pool = self.pool('no_speech'); pool.warm()
        with self.assertRaises(AudioError) as raised:
            pool.transcribe_job({'fixture': True})
        self.assertEqual(raised.exception.reason, 'stt_no_confident_speech')
        self.assertTrue(pool.runtime_metadata()['ready'])

    def test_shutdown_cancels_active_and_pending_and_releases_capacity(self):
        pool = self.pool('hang'); pool.warm()
        first, a = self.invoke_async(pool); self.wait_active(pool)
        second, b = self.invoke_async(pool); self.wait_active(pool, 1)
        pool.close(); first.join(2); second.join(2)
        self.assertEqual((a.get('reason'), b.get('reason')), ('stt_shutdown', 'stt_shutdown'))
        meta = pool.runtime_metadata()
        self.assertEqual((meta['active'], meta['pending'], meta['worker_pid']), (0, 0, 0))
        with self.assertRaises(AudioError) as raised:
            pool.transcribe_job({'fixture': True})
        self.assertEqual(raised.exception.reason, 'stt_shutdown')

    def test_resident_environment_is_explicitly_credential_free(self):
        pool = self.pool()
        from pocket.stt_pool import worker_environment
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'FISH_AUDIO_API_KEY': 'synthetic', 'HERMES_HOME': 'synthetic',
                'POCKET_DEVICE_TOKEN': 'synthetic', 'PYTHONPATH': 'synthetic'}):
            env = worker_environment(directory)
        for name in ('FISH_AUDIO_API_KEY', 'HERMES_HOME', 'POCKET_DEVICE_TOKEN', 'PYTHONPATH', 'HOME'):
            self.assertNotIn(name, env)
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')


class ResidentEngineTests(unittest.TestCase):
    def test_real_helper_reuses_engine_preserving_all_model_and_inference_parameters(self):
        self.assertIsNotNone(importlib.util.find_spec('pocket.stt_worker'),
                             'resident helper engine not implemented')
        from pocket.stt_worker import ResidentEngine
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); model = root / 'model'; model.mkdir()
            (model / 'model.bin').write_bytes(b'synthetic')
            helper = Path(__file__).resolve().parents[1] / 'helpers' / 'pocket_local_stt.py'
            settings = {'helper_path': str(helper), 'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
                        'model_path': str(model), 'language': 'auto'}
            loads = []; calls = []
            class Model:
                def __init__(self, path, **kw): loads.append((path, kw))
                def transcribe(self, path, **kw):
                    calls.append(kw)
                    return iter([SimpleNamespace(text=' synthetic ', no_speech_prob=.1, avg_logprob=-.2),
                                 SimpleNamespace(text='discarded', no_speech_prob=.9, avg_logprob=-2)]), None
            memory = io.BytesIO()
            with wave.open(memory, 'wb') as stream:
                stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed')); stream.writeframes(b'\0\0' * 160)
            engine = ResidentEngine(settings, factory=Model)
            self.assertEqual(engine.transcribe(memory.getvalue()), 'synthetic')
            self.assertEqual(engine.transcribe(memory.getvalue()), 'synthetic')
            self.assertEqual(len(loads), 1)
            self.assertEqual(loads[0][1], dict(device='cpu', compute_type='int8', cpu_threads=2,
                                             num_workers=1, local_files_only=True))
            self.assertEqual(calls, [dict(language=None, beam_size=1, vad_filter=True,
                condition_on_previous_text=False, no_speech_threshold=.6, log_prob_threshold=-1)] * 2)


if __name__ == '__main__':
    unittest.main()
