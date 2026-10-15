"""Offline adapter contracts and the installed decoder; no external speech calls."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from pocket.hermes_speech_worker import HermesProviders
from pocket.linux_voice import EncodedSpeech, decode_speech_pcm
from tests.test_hermes_speech import wav_bytes


class HermesFacadeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        original_path = sys.path[:]
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        model = self.root / 'model'
        model.mkdir()
        (model / 'model.bin').write_bytes(b'offline-fixture')
        self.stt_command = {'command': 'fixture --model-dir ' + str(model)}
        self.tts_command = {'command': 'fixture fish', 'output_format': 'mp3'}
        # Quote paths with spaces as a configured command normally does.
        import shlex
        self.stt_command['command'] = 'fixture --model-dir ' + shlex.quote(str(model))
        self.config = {'stt': {'enabled': True, 'provider': 'pocket_local'},
                       'tts': {'provider': 'fishaudio'}}
        self.calls = []
        sources = {}
        for name in ('pocket_local_stt.py', 'fish-audio-tts.sh'):
            source = self.root / name
            source.write_bytes(b'fixed synthetic helper')
            sources[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        self.pins = self.root / 'pins.json'
        self.pins.write_text(json.dumps({'version': 1, 'stt_provider': 'pocket_local',
            'tts_provider': 'fishaudio', 'stt_config_sha256': digest(self.stt_command),
            'tts_config_sha256': digest(self.tts_command), 'source_files': sources}))

        def resolve_stt(provider, config):
            if provider != 'pocket_local' or config is not self.config['stt']:
                raise ValueError('wrong STT resolver contract')
            return self.stt_command

        def resolve_tts(provider, config):
            if provider != 'fishaudio' or config is not self.config['tts']:
                raise ValueError('wrong TTS resolver contract')
            return self.tts_command

        def transcribe(filename, provider, command, config):
            if provider != 'pocket_local' or command is not self.stt_command or config is not self.config['stt']:
                raise ValueError('wrong STT execution contract')
            self.calls.append(('stt', Path(filename).read_bytes()))
            return {'success': True, 'provider': provider, 'transcript': 'fixture transcript'}

        def speak(text, filename, provider, command, config):
            if provider != 'fishaudio' or command is not self.tts_command or config is not self.config['tts']:
                raise ValueError('wrong TTS execution contract')
            self.calls.append(('tts', text))
            Path(filename).write_bytes(b'ID3offline-fixture')

        def secret_present(name):
            if name != 'FISH_AUDIO_API_KEY':
                raise ValueError('unexpected credential scope')
            return True

        def module(name, **members):
            value = types.ModuleType(name)
            value.__dict__.update(members)
            return value

        modules = {
            'hermes_cli': module('hermes_cli', __path__=[]),
            'hermes_cli.config': module('hermes_cli.config', load_config=lambda: self.config),
            'hermes_cli.auth': module('hermes_cli.auth', _env_secret=secret_present),
            'tools': module('tools', __path__=[]),
            'tools.transcription_command': module('tools.transcription_command',
                _resolve_command_stt_provider_config=resolve_stt, _transcribe_command_stt=transcribe),
            'tools.tts_command_provider': module('tools.tts_command_provider',
                _resolve_command_provider_config=resolve_tts, _generate_command_tts=speak),
            'tools.tts_text_normalize': module('tools.tts_text_normalize',
                prepare_spoken_text=lambda text, max_chars: ' '.join(text.split())),
        }
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    def test_readiness_resolves_fixed_configuration_without_speech_calls(self):
        hermes = HermesProviders(self.root, self.pins)
        with patch('importlib.util.find_spec', return_value=object()), \
                patch('shutil.which', return_value='/existing/tool'):
            self.assertEqual(hermes.check(), {'stt_provider': 'pocket_local', 'tts_provider': 'fishaudio'})
        self.assertEqual(self.calls, [])

    def test_facade_calls_existing_command_adapter_contracts(self):
        hermes = HermesProviders(self.root, self.pins)
        audio = self.root / 'fixture.wav'
        audio.write_bytes(wav_bytes())
        self.assertEqual(hermes.transcribe(audio), 'fixture transcript')
        output = self.root / 'fixture.mp3'
        hermes.speak('  fixture   reply  ', output)
        self.assertEqual(output.read_bytes(), b'ID3offline-fixture')
        self.assertEqual(self.calls, [('stt', wav_bytes()), ('tts', 'fixture reply')])

    def test_facade_rejects_helper_source_drift_before_speech_calls(self):
        (self.root / 'fish-audio-tts.sh').write_bytes(b'changed helper')
        with self.assertRaisesRegex(ValueError, 'helper changed'):
            HermesProviders(self.root, self.pins)
        self.assertEqual(self.calls, [])


class MP3DecoderTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'owner-installed FFmpeg required')
    def test_synthetic_mp3_decodes_to_bounded_mono_16khz_pcm(self):
        encoded = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-f', 'wav', '-i', 'pipe:0', '-f', 'mp3', 'pipe:1'], input=wav_bytes(),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True).stdout
        self.assertTrue(encoded)
        pcm = decode_speech_pcm(EncodedSpeech(encoded, 'audio/mpeg', 'fishaudio'))
        self.assertTrue(pcm)
        self.assertEqual(len(pcm) % 2, 0)
        self.assertLess(len(pcm), 32000)


if __name__ == '__main__':
    unittest.main()
