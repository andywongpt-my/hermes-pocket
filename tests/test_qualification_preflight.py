"""No port operations: real candidate bytes, synthetic recovery bytes only."""
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest import mock
try:
    from pocket import qualification_preflight as p
except ImportError:
    p = None

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(p, 'no-port qualification preflight missing')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def candidate(self):
        default=Path(__file__).resolve().parents[3]/'pocket-qualification-20261030/hermes-pocket-normal-qualification-app-only-candidate-20261030.zip'
        return Path(os.environ.get('POCKET_QUALIFICATION_CANDIDATE_ZIP',str(default)))
    def test_fixed_candidate_and_new_recovery_identity(self):
        self.assertEqual(p.CANDIDATE_ZIP_SHA256, '73e14b885f32db723a09fb13880829234e42eb19900d2e5cd1ebd64c644b2501')
        self.assertEqual(p.APP_SHA256, 'c8f58b769bf6004435da7f8f45bf545f4d6f8cd6cfaedfdcd6819377d05a063d')
        self.assertEqual(p.RECOVERY_SHA256, 'e21556a8b9fef5378ce6658c9e45c6583d6797845944fd57808d8eb8ecf569ad')
        self.assertEqual(p.RECOVERY_BYTES, 0x271000-0x10000)
    def test_windows_paths_reject_devices_unc_ads_traversal_and_aliases_before_io(self):
        bad = ['COM3', r'\\.\COM3', r'\\?\C:\test.bin', r'\\host\share\x',
               r'C:\a\COM7.txt', r'C:\a\nul.bin', r'C:\a\LPT1', r'C:\a\b:stream',
               r'C:\a\..\b', 'C:\\a\\b ', 'C:\\a\\b.', r'C:a.bin', 'C:\\a\n.bin',
               r'C:\a\COM¹.zip', r'C:\a\LPT².bin', r'C:\a\COM³.txt',
               r'C:\a\CONIN$', r'C:\a\CONOUT$', r'C:\a\COM1 .zip', r'C:\a\NUL .bin']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(p.PreflightError):
                p.local_path(value, platform='win32')
        self.assertEqual(str(p.local_path(r'C:\Users\Example\candidate.zip', platform='win32')), r'C:\Users\Example\candidate.zip')
    def test_remote_unknown_and_nonfixed_drives_refused_before_file_access(self):
        self.assertTrue(callable(getattr(p,'require_local_drive',None)),'local-drive gate missing')
        for value in (0,1,2,4,5,6):
            with mock.patch.object(p,'drive_type',return_value=value,create=True),mock.patch.object(p.os,'lstat') as access:
                with self.assertRaises(p.PreflightError):p.require_local_drive(p.PureWindowsPath(r'Z:\data\candidate.zip'))
                access.assert_not_called()
        with mock.patch.object(p,'drive_type',return_value=3,create=True):
            p.require_local_drive(p.PureWindowsPath(r'C:\data\candidate.zip'))
    def test_exact_port_identity_and_boolean_confusion_rejected(self):
        for port in ('com7','COM0','COM257','COM01',r'\\.\COM7','COM7\n','socket://private',True):
            with self.assertRaises(p.PreflightError):p.validate_binding(port,'board-1')
        for identity in ('','space name','x'*129,'private\n'):
            with self.assertRaises(p.PreflightError):p.validate_binding('COM7',identity)
        p.validate_binding('COM256','operator-attested-board-1')
    def test_regular_file_hash_size_bounds_and_symlink_rejection(self):
        f=self.root/'data';f.write_bytes(b'bounded')
        self.assertEqual(p.verified_file(str(f),7,hashlib.sha256(b'bounded').hexdigest()),True)
        for size,digest in ((6,hashlib.sha256(b'bounded').hexdigest()),(7,'0'*64)):
            with self.assertRaises(p.PreflightError):p.verified_file(str(f),size,digest)
        # Fake only this existing regular file's lstat mode. Unlike creating an
        # actual symlink this also runs on Windows without Developer Mode/admin.
        original=p.os.lstat
        def symlink_mode(path):
            info=original(path)
            return os.stat_result((stat.S_IFLNK|0o777,*tuple(info)[1:])) if Path(path)==f else info
        with mock.patch.object(p.os,'lstat',side_effect=symlink_mode):
            with self.assertRaises(p.PreflightError):p.verified_file(str(f),7,hashlib.sha256(b'bounded').hexdigest())
        with self.assertRaises(p.PreflightError):p.verified_file(str(self.root),7,'0'*64)
    def test_real_candidate_metadata_and_fake_recovery_without_serial_import(self):
        candidate=self.candidate();self.assertTrue(candidate.is_file(),'verified candidate input required')
        recovery=self.root/'synthetic-recovery.bin';data=b'\0'*p.RECOVERY_BYTES;recovery.write_bytes(data)
        real_import=__import__
        def no_serial(name,*args,**kwargs):
            if name=='serial' or name.startswith('serial.'):raise AssertionError('serial import during preflight')
            return real_import(name,*args,**kwargs)
        with mock.patch.object(p,'RECOVERY_SHA256',hashlib.sha256(data).hexdigest()),mock.patch('builtins.__import__',side_effect=no_serial):
            facts=p.preflight(str(candidate),str(recovery),'COM7','board-1')
        for key in ('candidate_verified','recovery_verified'):self.assertEqual(facts[key],1)
        for key in ('physical_identity_verified','platform_behavior_verified','device_opened','flash_authorized'):self.assertEqual(facts[key],0)
        self.assertTrue(all(type(v) is int for v in facts.values()));self.assertNotIn(str(recovery),json.dumps(facts))
    def test_real_preflight_rejects_synthetic_recovery_without_hash_substitution(self):
        f=self.root/'not-real-recovery.bin';f.write_bytes(b'\0'*p.RECOVERY_BYTES)
        with self.assertRaises(p.PreflightError) as error:p.preflight(str(self.candidate()),str(f),'COM7','board-1')
        self.assertNotIn(str(f),str(error.exception))
    def test_environment_support_is_not_physical_acceptance(self):
        for system,version,serial,expected in [('win32',(3,12,14),'3.5',1),('linux',(3,12,1),'3.5',0),('win32',(3,13,0),'3.5',0),('win32',(3,12,1),'3.4',0)]:
            with mock.patch.object(p.sys,'platform',system),mock.patch.object(p.sys,'version_info',version),mock.patch.object(p.platform,'python_implementation',return_value='CPython'),mock.patch.object(p.metadata,'version',return_value=serial):
                facts=p.runtime_facts();self.assertEqual(facts['runtime_compatible'],expected)
                self.assertNotIn('platform_behavior_verified',facts)
if __name__=='__main__':unittest.main()
