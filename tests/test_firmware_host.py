import pathlib
import shutil
import subprocess
import tempfile
import unittest

class FirmwareHostTests(unittest.TestCase):
    def test_model(self):
        compiler=shutil.which('gcc')
        if not compiler:self.skipTest('host C compiler unavailable')
        root=pathlib.Path(__file__).resolve().parents[1]/'firmware'
        with tempfile.TemporaryDirectory() as temp:
            binary=pathlib.Path(temp)/'test-model'
            subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+str(root/'core'),str(root/'core/pocket_model.c'),str(root/'tests/test_model.c'),'-o',str(binary)],check=True,capture_output=True)
            self.assertIn('PASS',subprocess.check_output([str(binary)],text=True))
