"""Characterize the pinned gain API surprise, not acoustic/ADC correctness.

No device/network access. Real ES8311 driver; fake I2C/register and ref boundaries.
Set POCKET_CODEC_DIR to the exact extracted esp_codec_dev 1.6.2 component.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/codec_register_trace'
CHECKSUM_SHA = '549328c602f3b603de982cc2b64ff79b6ce595662af80fb458018ed7faffbf9c'
COMPONENT_HASH = '4779f31a3ba3f9b38aee1afe6ccb9ba8f6108dc3a6e77d39d7d714e9a215371b'


def verify_identity(codec):
    lock = (ROOT / 'firmware/dependencies.lock').read_text()
    match = re.search(r'^  espressif/esp_codec_dev:\n(.*?)(?=^  [^ ]|\Z)', lock, re.M | re.S)
    if not match or COMPONENT_HASH not in match.group(1) or not re.search(r'^    version: 1\.6\.2$', match.group(1), re.M):
        raise ValueError('firmware lock no longer selects the characterized codec')
    raw = (FIXTURE / 'CHECKSUMS.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != CHECKSUM_SHA:
        raise ValueError('pinned official checksum receipt changed')
    provenance = json.loads((FIXTURE / 'provenance.json').read_text())
    if provenance['version_record']['component_hash'] != COMPONENT_HASH:
        raise ValueError('component provenance hash mismatch')
    for item in json.loads(raw)['files']:
        data = (codec / item['path']).read_bytes()
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['hash']:
            raise ValueError('pinned component mismatch: ' + item['path'])


class CodecRegisterTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not compiler:
            raise unittest.SkipTest('host C compiler unavailable')
        selected = os.environ.get('POCKET_CODEC_DIR')
        candidates = [Path(selected)] if selected else [ROOT / 'firmware/managed_components/espressif__esp_codec_dev']
        if not selected:
            candidates += sorted((ROOT.parent / 'HPsdk/component-cache').glob('*/espressif__esp_codec_dev_1.6.2_*'))
        codec = next((p for p in candidates if (p / 'device/es8311/es8311.c').is_file()), None)
        if codec is None:
            if selected:
                raise AssertionError('explicit POCKET_CODEC_DIR has no ES8311 source: ' + selected)
            raise unittest.SkipTest('pinned codec absent; set POCKET_CODEC_DIR (no automatic download)')
        verify_identity(codec)
        cls.codec, cls.compiler = codec, compiler

    def build_and_run(self, driver_text=None):
        with tempfile.TemporaryDirectory(prefix='codec-register-') as td:
            path = Path(td)
            driver = self.codec / 'device/es8311/es8311.c'
            if driver_text is not None:
                driver = path / 'es8311_mutant.c'
                driver.write_text(driver_text)
            binary = path / ('trace.exe' if os.name == 'nt' else 'trace')
            includes = [FIXTURE, self.codec / 'include', self.codec / 'interface',
                        self.codec / 'device/include', self.codec / 'device/priv_include',
                        self.codec / 'device/es8311']
            command = [self.compiler, '-std=c11', '-include', 'stdlib.h', '-Wall', '-Wextra',
                       *['-I' + str(p) for p in includes], str(FIXTURE / 'register_trace.c'),
                       str(driver), str(self.codec / 'esp_codec_dev_vol.c'), '-lm', '-o', str(binary)]
            result = subprocess.run(command, text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)

    def test_pinned_legacy_and_paired_gain_writes(self):
        result = self.build_and_run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for route in ('08', '58'):
            self.assertIn('PASS final reg14=1a reg16=05 reg17=bf route=' + route, result.stdout)

    def test_changed_driver_is_rejected_by_identity(self):
        with tempfile.TemporaryDirectory(prefix='codec-identity-') as td:
            clone = Path(td) / 'codec'
            shutil.copytree(self.codec, clone)
            driver = clone / 'device/es8311/es8311.c'
            driver.write_bytes(driver.read_bytes() + b'\n/* mutation */\n')
            with self.assertRaisesRegex(ValueError, 'pinned component mismatch'):
                verify_identity(clone)

    def test_trace_rejects_changed_register_behavior(self):
        original = (self.codec / 'device/es8311/es8311.c').read_text()
        # Explicit mutation testing AFTER validating original dependency bytes.
        # Bypass provenance only for temporary mutants to test behavioral checks.
        mutations = [
            ('ES8311_ADC_REG16, 0x24', 'ES8311_ADC_REG16, 0x04'),
            ('ES8311_SYSTEM_REG14, 0x1A', 'ES8311_SYSTEM_REG14, 0x19'),
            ('ES8311_ADC_REG17, 0xBF', 'ES8311_ADC_REG17, 0xBE'),
            ('ES8311_ADC_REG16, gain_db', 'ES8311_ADC_REG16, gain_db | 0x20'),
            ('ES8311_GPIO_REG44, 0x58', 'ES8311_GPIO_REG44, 0x08'),
        ]
        for old, new in mutations:
            with self.subTest(mutation=new):
                self.assertIn(old, original)
                result = self.build_and_run(original.replace(old, new))
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn('CHECK FAILED', result.stderr)


if __name__ == '__main__':
    unittest.main()
