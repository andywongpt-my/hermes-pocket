"""Fixed-memory numeric observations in the actual finite C runner."""
from pathlib import Path
import subprocess
import tempfile
import unittest

class OfflineObservationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / 'observations'
        root = Path(__file__).resolve().parents[1] / 'firmware'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(root/'core'), str(root/'tests/offline_observations_test.c'),
                        str(root/'core/pocket_offline_diagnostic.c'), '-o', str(cls.binary)], check=True)
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
    def check_mode(self, mode):
        result = subprocess.run([str(self.binary), str(mode)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_identical_totals_distinguish_start_end_distributed_rails(self): self.check_mode(0)
    def test_empty_partial_window_boundaries_signed_endian_and_runs(self): self.check_mode(1)
    def test_full_scale_240000_samples_without_overflow(self): self.check_mode(2)
