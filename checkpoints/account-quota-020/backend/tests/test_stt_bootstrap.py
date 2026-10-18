"""Strict one-shot resolver boundary; synthetic config and pins only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name); self.model = self.root / 'cached model'; self.model.mkdir()
        (self.model / 'model.bin').write_bytes(b'synthetic')
        self.helper = self.root / 'pocket_local_stt.py'; self.helper.write_bytes(b'synthetic pinned helper')
        self.digest = hashlib.sha256(self.helper.read_bytes()).hexdigest()
        self.pins = {'source_files': {str(self.helper): self.digest}}
        self.command = {'command': shlex.join([sys.executable, '-I', '-B', str(self.helper),
            '--model-dir', str(self.model), '--input', '{input_path}', '--output', '{output_path}', '--language', 'auto']),
            'type': 'command', 'format': 'txt', 'normalize': False, 'env_passthrough': []}
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('pocket.stt_bootstrap'), 'safe STT bootstrap not implemented')
        from pocket import stt_bootstrap
        return stt_bootstrap
    def test_resolver_returns_only_allowlisted_stt_settings(self):
        result = self.module().settings_from_provider(SimpleNamespace(stt_command=self.command), self.pins)
        self.assertEqual(result, {'helper_path': str(self.helper), 'helper_sha256': self.digest,
            'model_path': str(self.model), 'language': 'auto'})
    def test_changed_flags_language_placeholders_extra_options_and_environment_fail_closed(self):
        module = self.module()
        bad = [dict(self.command, env_passthrough=['FISH_AUDIO_API_KEY']),
               dict(self.command, normalize=True), dict(self.command, format='json'),
               dict(self.command, private='synthetic'),
               dict(self.command, command=self.command['command'].replace('-I', '-u')),
               dict(self.command, command=self.command['command'].replace('--language auto', '--language en')),
               dict(self.command, command=self.command['command'].replace('{input_path}', '{input}')),
               dict(self.command, command=self.command['command'] + ' --beam-size 5')]
        for command in bad:
            with self.subTest(command=command), self.assertRaises(ValueError):
                module.settings_from_provider(SimpleNamespace(stt_command=command), self.pins)
    def test_bootstrap_does_not_check_fish_auth_or_invoke_speech(self):
        module = self.module(); pins = self.root / 'pins.json'; pins.write_text(json.dumps(self.pins))
        provider = Mock(); provider.stt_command = self.command
        with patch.object(module, 'HermesProviders', return_value=provider):
            value = module.resolve(self.root, pins)
        self.assertEqual(value['language'], 'auto')
        provider.check.assert_not_called(); provider.speak.assert_not_called(); provider.transcribe.assert_not_called()


if __name__ == '__main__': unittest.main()
