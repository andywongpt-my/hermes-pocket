import base64
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
import wave
from unittest.mock import patch

from pocket.audio import AudioError
from pocket.hermes_speech import HermesSpeechTransport
from pocket.hermes_speech_worker import handle_request, validate_provider_pins
from pocket.linux_voice import LinuxSpeechAdapter
from pocket.run_bridge import speech_adapter


def wav_bytes(rate=16000, channels=1):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b'\0\0' * 160)
    return stream.getvalue()


class FakeHermes:
    def __init__(self):
        self.calls = []

    def check(self):
        return {'stt_provider': 'pocket_local', 'tts_provider': 'fishaudio'}

    def transcribe(self, path):
        self.calls.append(('stt', Path(path).read_bytes()))
        return 'synthetic fixture only'

    def speak(self, text, path):
        self.calls.append(('tts', text))
        Path(path).write_bytes(b'ID3synthetic-fixture')


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.hermes = FakeHermes()

    def call(self, operation, body=None):
        request = {'operation': operation}
        if body is not None:
            request['body'] = body
        return handle_request(request, self.hermes, self.root)

    def test_check_does_not_invoke_a_speech_provider(self):
        self.assertTrue(self.call('check')['ready'])
        self.assertEqual(self.hermes.calls, [])

    def test_stt_validates_wav_and_deletes_local_audio(self):
        raw = wav_bytes()
        result = self.call('transcribe', {'mime_type': 'audio/wav', 'data_url':
            'data:audio/wav;base64,' + base64.b64encode(raw).decode()})
        self.assertEqual(result['transcript'], 'synthetic fixture only')
        self.assertEqual(self.hermes.calls, [('stt', raw)])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_stt_rejects_wrong_rate_channels_and_truncated_frames(self):
        for raw in (wav_bytes(44100), wav_bytes(channels=2), wav_bytes()[:-8]):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                self.call('transcribe', {'mime_type': 'audio/wav', 'data_url':
                    'data:audio/wav;base64,' + base64.b64encode(raw).decode()})
        self.assertEqual(self.hermes.calls, [])

    def test_tts_uses_fixed_provider_and_deletes_output(self):
        result = self.call('speak', {'text': 'synthetic reply'})
        self.assertEqual(result['provider'], 'fishaudio')
        self.assertEqual(result['mime_type'], 'audio/mpeg')
        self.assertEqual(self.hermes.calls, [('tts', 'synthetic reply')])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_request_cannot_override_provider_command_or_file_paths(self):
        requests = [
            {'operation': 'speak', 'body': {'text': 'x', 'provider': 'other'}},
            {'operation': 'speak', 'body': {'text': 'x'}, 'command': 'anything'},
            {'operation': 'transcribe', 'body': {'input_path': '/etc/passwd'}},
            {'operation': 'check', 'body': {}},
            {'operation': 'unknown'},
        ]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                handle_request(request, self.hermes, self.root)
        self.assertEqual(self.hermes.calls, [])

    def test_tts_rejects_empty_and_oversize_text_without_provider_calls(self):
        for text in ('', ' ' * 20, 'x' * 4097):
            with self.assertRaises(ValueError):
                self.call('speak', {'text': text})
        self.assertEqual(self.hermes.calls, [])

    def test_oversized_or_symlink_speech_output_is_rejected(self):
        def oversized(text, path):
            Path(path).write_bytes(b'x' * (3 * 1024 * 1024 + 1))
        self.hermes.speak = oversized
        with self.assertRaises(ValueError):
            self.call('speak', {'text': 'fixture'})
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'Linux symlink semantics')
    def test_tts_output_symlink_is_not_followed(self):
        target = self.root / 'unrelated'
        target.write_bytes(b'unchanged')
        def link(text, path):
            Path(path).symlink_to(target)
        self.hermes.speak = link
        with self.assertRaises(ValueError):
            self.call('speak', {'text': 'fixture'})
        self.assertEqual(target.read_bytes(), b'unchanged')
        self.assertFalse((self.root / 'output.mp3').exists())

    def test_provider_definition_or_source_drift_fails_closed(self):
        import hashlib
        stt, tts = {'command': 'fixed local'}, {'command': 'fixed fish'}
        source = self.root / 'helper.py'
        source.write_text('fixed helper')
        canonical = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        pins = {'version': 1, 'stt_provider': 'pocket_local', 'tts_provider': 'fishaudio',
            'stt_config_sha256': canonical(stt), 'tts_config_sha256': canonical(tts),
            'source_files': {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}}
        validate_provider_pins(pins, stt, tts)
        with self.assertRaises(ValueError):
            validate_provider_pins(pins, {'command': 'changed'}, tts)
        source.write_text('changed helper')
        with self.assertRaises(ValueError):
            validate_provider_pins(pins, stt, tts)


FIXTURE_WORKER = '''import base64,io,json,os,sys,wave
r=json.load(sys.stdin)
if r['operation']=='check':
 print(json.dumps({'ok':True,'ready':True,'stt_provider':'pocket_local','tts_provider':'fishaudio','environment_names':list(os.environ)}))
elif r['operation']=='transcribe':
 print(json.dumps({'ok':True,'transcript':'fixture transcript'}))
else:
 s=io.BytesIO()
 with wave.open(s,'wb') as w:
  w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\\0\\0'*160)
 print(json.dumps({'ok':True,'provider':'fishaudio','mime_type':'audio/wav','data_url':'data:audio/wav;base64,'+base64.b64encode(s.getvalue()).decode()}))
'''


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.worker = self.root / 'fixture.py'
        self.worker.write_text(FIXTURE_WORKER)
        self.pins = self.root / 'pins.json'
        self.pins.write_text('{}')

    def transport(self, **options):
        return HermesSpeechTransport(sys.executable, str(self.root), str(self.root),
            str(self.pins), worker_path=str(self.worker), **options)

    def test_transport_strips_parent_credentials_and_keeps_provider_calls_out_of_check(self):
        with patch.dict(os.environ, {'POCKET_DEVICE_TOKEN': 'fixture-private',
                                    'FISH_AUDIO_API_KEY': 'fixture-fish'}):
            result = self.transport().check()
        self.assertNotIn('POCKET_DEVICE_TOKEN', result['environment_names'])
        self.assertNotIn('FISH_AUDIO_API_KEY', result['environment_names'])
        self.assertNotIn('fixture-private', json.dumps(result))

    def test_existing_audio_adapter_works_through_worker_without_network(self):
        speech = LinuxSpeechAdapter(self.transport(), stt_enabled=True)
        self.assertEqual(speech.transcribe(b'\0\0' * 160), 'fixture transcript')
        result = speech.speak('fixture reply')
        self.assertEqual(result.provider, 'fishaudio')
        self.assertEqual(result.mime_type, 'audio/wav')
        with wave.open(io.BytesIO(result.data), 'rb') as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels()), (16000, 1))

    def test_unsupported_route_rejected_before_worker(self):
        with self.assertRaises(AudioError):
            self.transport()('/api/env', {})

    def test_worker_errors_do_not_leak_text_or_credentials(self):
        self.worker.write_text("import sys;sys.stderr.write('fixture-private reply text');raise SystemExit(1)")
        with self.assertRaises(AudioError) as raised:
            self.transport()('/api/audio/speak', {'text': 'private input'})
        self.assertNotIn('private', str(raised.exception))

    def test_invalid_and_oversize_worker_output_rejected(self):
        for output in ('not json', '{"ok":true,"ok":false}', 'x' * (4*1024*1024+1025)):
            self.worker.write_text('print(' + repr(output) + ')')
            with self.assertRaises(AudioError):
                self.transport().check()

    def test_output_limit_stops_noisy_worker_before_its_timeout(self):
        self.worker.write_text("import sys,time;sys.stdout.write('x'*2048);sys.stdout.flush();time.sleep(60)")
        started = time.monotonic()
        with patch('pocket.hermes_speech.MAX_RESPONSE', 1024):
            with self.assertRaisesRegex(AudioError, 'output too large'):
                self.transport(timeout=2.5).check()
        self.assertLess(time.monotonic() - started, 1.5)

    @unittest.skipUnless(os.name == 'posix', 'Linux process-tree semantics')
    def test_timeout_kills_nested_provider_session(self):
        pidfile = self.root / 'child.pid'
        self.worker.write_text("import subprocess,sys,time\n"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)\n"
            + 'open(' + repr(str(pidfile)) + ",'w').write(str(p.pid))\n"
            + 'time.sleep(60)\n')
        with self.assertRaises(AudioError):
            self.transport(timeout=0.8).check()
        self.assertTrue(pidfile.is_file())
        pid = int(pidfile.read_text())
        status = Path('/proc') / str(pid) / 'status'
        for _ in range(20):
            if not status.exists() or 'State:\tZ' in status.read_text():
                break
            time.sleep(.05)
        else:
            self.fail('provider descendant survived timeout')

    @unittest.skipUnless(sys.platform == 'linux', 'Linux orphan containment')
    def test_exited_worker_cannot_leave_provider_session_alive(self):
        pidfile = self.root / 'orphan.pid'
        child = ('import os,time;open(' + repr(str(pidfile))
            + ",'w').write(str(os.getpid()));time.sleep(60)")
        self.worker.write_text('import os,subprocess,sys,time\n'
            + 'subprocess.Popen([sys.executable,"-c",' + repr(child) + '],start_new_session=True)\n'
            + 'time.sleep(.1)\nos._exit(0)\n')
        try:
            with self.assertRaises(AudioError):
                self.transport(timeout=.8).check()
            self.assertTrue(pidfile.is_file())
            pid = int(pidfile.read_text())
            status = Path('/proc') / str(pid) / 'status'
            for _ in range(20):
                if not status.exists() or 'State:\tZ' in status.read_text():
                    break
                time.sleep(.05)
            else:
                self.fail('provider survived after its worker exited')
        finally:
            if pidfile.is_file():
                try:
                    os.kill(int(pidfile.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(sys.platform == 'linux', 'Linux subreaper semantics')
    def test_timeout_reaps_an_actively_forking_provider_tree(self):
        pidfile = self.root / 'forking.pids'
        provider = ('import os,time\n'
            + 'f=os.open(' + repr(str(pidfile)) + ',os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)\n'
            + 'os.write(f,(str(os.getpid())+"\\n").encode())\n'
            + 'for i in range(20):\n'
            + ' child=os.fork()\n'
            + ' if child==0:\n'
            + '  os.write(f,(str(os.getpid())+"\\n").encode());time.sleep(60);os._exit(0)\n'
            + ' time.sleep(.07)\n'
            + 'time.sleep(60)\n')
        self.worker.write_text('import subprocess,sys,time\n'
            + 'subprocess.Popen([sys.executable,"-c",' + repr(provider) + '],start_new_session=True)\n'
            + 'time.sleep(60)\n')
        try:
            with self.assertRaisesRegex(AudioError, 'timed out'):
                self.transport(timeout=.4).check()
            pids = [int(value) for value in pidfile.read_text().split()]
            self.assertGreaterEqual(len(pids), 2)
            self.assertEqual([pid for pid in pids if (Path('/proc') / str(pid)).exists()], [],
                'supervisor must reap all adopted provider children before returning')
        finally:
            if pidfile.is_file():
                for value in pidfile.read_text().split():
                    try:
                        os.kill(int(value), signal.SIGKILL)
                    except ProcessLookupError:
                        pass


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = {'kind': 'hermes-command', 'python': '/explicit/python',
            'hermes_root': '/explicit/hermes', 'hermes_home': '/explicit/profile',
            'pins_file': '/explicit/pins.json', 'stt_enabled': True}

    def test_hermes_speech_needs_no_dashboard_or_provider_key_reference(self):
        with patch('pocket.run_bridge.HermesSpeechTransport') as factory:
            adapter = speech_adapter(self.config, 'linux')
        factory.return_value.check.assert_called_once_with()
        self.assertTrue(adapter.stt_enabled)

    def test_mixed_auth_fields_and_other_targets_are_rejected(self):
        for config, target in [(self.config, 'windows'),
                ({**self.config, 'token_env': 'POCKET_DASHBOARD_TOKEN'}, 'linux'),
                ({**self.config, 'stt_enabled': 1}, 'linux')]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                speech_adapter(config, target)


if __name__ == '__main__':
    unittest.main()
