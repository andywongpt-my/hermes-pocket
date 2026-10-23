"""Real-file settings installation tests; no real user settings or credentials."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / 'claude_settings_020.py'


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TARGET.is_file(), 'settings integration not implemented')
        spec = importlib.util.spec_from_file_location('claude_settings_fixture', TARGET)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = self.root / 'settings.json'
        self.backup = self.root / 'statusline-backup.json'
        self.secret = 'SYNTHETIC-PRIVATE-SETTING-NEVER-BACKUP'
        self.original = {'env': {'SYNTHETIC_TOKEN': self.secret}, 'permissions': {'allow': ['Read']}, 'theme': 'dark'}
        self.settings.write_text(json.dumps(self.original), encoding='utf-8')
        self.settings.chmod(0o600)
        self.command = 'fixture-command --numeric-only'

    def install(self):
        return self.m.install_settings(self.settings, self.backup, self.command)

    def test_install_preserves_other_fields_and_backup_omits_their_bytes(self):
        receipt = self.install()
        doc = json.loads(self.settings.read_bytes())
        self.assertEqual(doc['statusLine'], {'type': 'command', 'command': self.command})
        doc.pop('statusLine')
        self.assertEqual(doc, self.original)
        backup = self.backup.read_bytes()
        self.assertNotIn(self.secret.encode(), backup)
        self.assertNotIn(b'SYNTHETIC_TOKEN', backup)
        self.assertFalse(receipt['prior_statusline_present'])
        self.assertTrue(self.m.verify_settings(self.settings, self.backup))

    def test_rollback_removes_only_our_field_and_keeps_later_unrelated_changes(self):
        self.install()
        doc = json.loads(self.settings.read_bytes()); doc['theme'] = 'light'; doc['later'] = 5
        self.settings.write_text(json.dumps(doc), encoding='utf-8')
        self.m.restore_settings(self.settings, self.backup)
        expected = dict(self.original, theme='light', later=5)
        self.assertEqual(json.loads(self.settings.read_bytes()), expected)

    def test_prior_explicit_null_is_restored_as_null(self):
        doc = dict(self.original, statusLine=None); self.settings.write_text(json.dumps(doc), encoding='utf-8')
        self.install(); self.m.restore_settings(self.settings, self.backup)
        self.assertEqual(json.loads(self.settings.read_bytes()), doc)

    def test_existing_renderer_or_foreign_restore_change_is_never_overwritten(self):
        doc = dict(self.original, statusLine={'type': 'command', 'command': 'keep-existing-renderer'})
        self.settings.write_text(json.dumps(doc), encoding='utf-8'); before = self.settings.read_bytes()
        with self.assertRaises(self.m.SettingsError): self.install()
        self.assertEqual(self.settings.read_bytes(), before); self.assertFalse(self.backup.exists())
        self.settings.write_text(json.dumps(self.original), encoding='utf-8'); self.install()
        doc = json.loads(self.settings.read_bytes()); doc['statusLine']['command'] = 'new-owner-renderer'
        self.settings.write_text(json.dumps(doc), encoding='utf-8'); before = self.settings.read_bytes()
        with self.assertRaises(self.m.SettingsError): self.m.restore_settings(self.settings, self.backup)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_exclusive_backup_collision_and_malformed_settings_fail_before_mutation(self):
        self.backup.write_text('{}'); before = self.settings.read_bytes()
        with self.assertRaises(self.m.SettingsError): self.install()
        self.assertEqual(self.settings.read_bytes(), before)
        self.backup.unlink()
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{', b'x' * 1048577):
            self.settings.write_bytes(raw)
            with self.subTest(raw=raw[:20]), self.assertRaises(self.m.SettingsError): self.install()
            self.assertEqual(self.settings.read_bytes(), raw); self.assertFalse(self.backup.exists())

    def test_replace_failure_keeps_original_and_leaves_no_raw_temporary_file(self):
        before = self.settings.read_bytes()
        with patch.object(self.m, 'replace_existing', side_effect=OSError('fixture failure')):
            with self.assertRaises(self.m.SettingsError): self.install()
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual({x.name for x in self.root.iterdir()}, {'settings.json', 'statusline-backup.json'})
        self.assertNotIn(self.secret.encode(), self.backup.read_bytes())

    def test_concurrent_settings_change_after_backup_is_not_overwritten(self):
        actual = self.m.write_backup
        changed = dict(self.original, owner_edit='keep')
        def collide(path, value):
            actual(path, value); self.settings.write_text(json.dumps(changed), encoding='utf-8')
        with patch.object(self.m, 'write_backup', side_effect=collide):
            with self.assertRaises(self.m.SettingsError): self.install()
        self.assertEqual(json.loads(self.settings.read_bytes()), changed)

    def test_symlink_or_hardlinked_settings_are_rejected(self):
        other = self.root / 'other.json'; other.write_text('{}')
        self.settings.unlink(); os.link(other, self.settings)
        with self.assertRaises(self.m.SettingsError): self.install()
        self.assertFalse(self.backup.exists())
        self.settings.unlink()
        try: self.settings.symlink_to(other)
        except OSError: return
        with self.assertRaises(self.m.SettingsError): self.install()
        self.assertEqual(other.read_text(), '{}')

    def test_command_contains_only_fixed_paths_and_config_digest(self):
        command = self.m.command_for('C:/Program Space/Python/python.exe', 'C:/Pocket Space/callback.py',
                                     'C:/Pocket Space/config.json', 'a' * 64)
        self.assertIn("& 'C:/Program Space/Python/python.exe'", command)
        self.assertIn("--config 'C:/Pocket Space/config.json'", command)
        self.assertIn("--config-sha256 '" + 'a' * 64 + "'", command)
        for invalid in ('relative.py', 'C:/bad$env.py', 'C:/bad%TEMP%.py', 'C:/bad\n.py'):
            with self.subTest(invalid=invalid), self.assertRaises(self.m.SettingsError):
                self.m.command_for(invalid, 'C:/callback.py', 'C:/config.json', 'a' * 64)

    @unittest.skipUnless(os.name == 'nt', 'native PowerShell stdin check is Windows only')
    def test_configured_shell_shape_preserves_binary_stdin_with_spaced_paths(self):
        fixture = self.root / 'stdin fixture.py'
        fixture.write_text('import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())', encoding='utf-8')
        config = self.root / 'config file.json'; config.write_text('{}')
        command = self.m.command_for(sys.executable, str(fixture), str(config), 'a' * 64)
        raw = 'fixture raw JSON: \u2713 \u4f60\u597d\r\n\x00'.encode('utf-8')
        p = subprocess.run(command, shell=True, input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr.decode(errors='replace'))
        self.assertEqual(p.stdout.decode().strip(), hashlib.sha256(raw).hexdigest())


if __name__ == '__main__': unittest.main()
