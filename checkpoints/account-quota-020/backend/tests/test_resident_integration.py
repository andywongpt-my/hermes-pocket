"""Offline ownership, optional adapter and authenticated metadata contracts."""
import base64
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

from pocket.audio import AudioError
from pocket.hermes_speech import HermesSpeechTransport
from pocket.linux_voice import LinuxSpeechAdapter
from pocket.run_bridge import speech_adapter, build
from pocket.server import make_server
from pocket.service import Service, FakeHermes
from pocket.store import Store
from tests.test_hermes_speech import FIXTURE_WORKER


class AdapterTests(unittest.TestCase):
    def test_optional_job_method_preserves_pcm_and_safe_timings(self):
        transport = Mock(); cancel = threading.Event()
        transport.transcribe_job.return_value = {'transcript': 'synthetic', 'timings_us': {'queue': 4}}
        adapter = LinuxSpeechAdapter(transport, stt_enabled=True)
        self.assertTrue(callable(getattr(adapter, 'transcribe_job', None)), 'optional timed job interface missing')
        result = adapter.transcribe_job(b'\0\0' * 160, cancel=cancel)
        body = transport.transcribe_job.call_args.args[0]
        with wave.open(io.BytesIO(base64.b64decode(body['data_url'].split(',')[1])), 'rb') as stream:
            self.assertEqual(stream.readframes(160), b'\0\0' * 160)
        self.assertIs(transport.transcribe_job.call_args.kwargs['cancel'], cancel)
        self.assertEqual(result, {'transcript': 'synthetic', 'timings_us': {'queue': 4}})

    def test_adapter_legacy_callable_and_close_remain_supported(self):
        class Transport:
            def __call__(self, path, body): return {'ok': True, 'transcript': 'synthetic'}
        adapter = LinuxSpeechAdapter(Transport(), stt_enabled=True)
        self.assertEqual(adapter.transcribe(b'\0\0'), 'synthetic')
        self.assertTrue(callable(getattr(adapter, 'close', None)), 'speech close ownership missing')
        adapter.close()

    def test_configuration_warms_before_return_and_closes_on_failure(self):
        config = dict(kind='hermes-command', python='/synthetic/python', hermes_root='/synthetic/root',
                      hermes_home='/synthetic/home', pins_file='/synthetic/pins', stt_enabled=True)
        with patch('pocket.run_bridge.HermesSpeechTransport') as factory:
            factory.return_value.check.side_effect = AudioError('synthetic')
            with self.assertRaises(AudioError): speech_adapter(config, 'linux')
            factory.return_value.close.assert_called_once_with()


class TransportResidentTests(unittest.TestCase):
    def test_resident_initialization_checks_once_and_routes_only_stt_to_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pins = root / 'pins.json'; pins.write_text('{}')
            worker = root / 'fixture.py'; worker.write_text(FIXTURE_WORKER)
            import inspect
            self.assertIn('resident', inspect.signature(HermesSpeechTransport).parameters,
                          'resident transport option missing')
            pool = Mock(); pool.runtime_metadata.return_value = {'resident': True, 'ready': True}
            pool.transcribe_job.return_value = {'transcript': 'synthetic', 'timings_us': {'inference': 5}}
            with patch('pocket.hermes_speech.ResidentSTTPool', return_value=pool), \
                    patch.object(HermesSpeechTransport, '_resolve_stt_settings', return_value={}) as resolver:
                transport = HermesSpeechTransport(sys.executable, root, root, pins,
                    worker_path=worker, resident=True)
                try:
                    transport.check()
                    self.assertEqual(transport('/api/audio/transcribe', {})['transcript'], 'synthetic')
                    self.assertEqual(transport('/api/audio/speak', {'text': 'synthetic'})['provider'], 'fishaudio')
                    resolver.assert_called_once(); pool.warm.assert_called_once()
                    self.assertEqual(pool.transcribe_job.call_count, 1)
                    self.assertTrue(transport.runtime_metadata()['stt']['ready'])
                finally: transport.close()
                pool.close.assert_called_once()


class RuntimeHTTPTests(unittest.TestCase):
    def test_status_metadata_is_authenticated_allowlisted_and_does_not_warm(self):
        class Speech:
            stt_enabled = True
            def __init__(self): self.reads = 0
            def runtime_metadata(self):
                self.reads += 1
                return {'stt': {'resident': True, 'ready': False, 'generation': 3,
                    'active': 0, 'pending': 0, 'worker_pid': 0, 'startup_us': 5,
                    'model_load_us': 4, 'restart_count': 2, 'private_path': '/private'}}
            def warm(self): raise AssertionError('metadata GET must not warm')
        speech = Speech(); store = Store(':memory:')
        server = make_server({'linux': Service(store, FakeHermes())}, {'synthetic': 'device'},
                             speech={'linux': speech})
        thread = threading.Thread(target=server.serve_forever); thread.start()
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        try:
            client.request('GET', '/targets/linux/status')
            response = client.getresponse(); response.read(); self.assertEqual(response.status, 401)
            self.assertEqual(speech.reads, 0)
            for _ in range(2):
                client.request('GET', '/targets/linux/status', headers={'Authorization': 'Bearer synthetic'})
                response = client.getresponse(); data = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertIn('voice_runtime', data, 'additive runtime status missing')
                self.assertNotIn('private', json.dumps(data))
                self.assertEqual(data['voice_runtime']['stt']['generation'], 3)
            self.assertEqual(speech.reads, 2)
        finally:
            client.close(); server.shutdown(); thread.join(); server.server_close(); store.close()


if __name__ == '__main__': unittest.main()
