import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


def host_compiler():
    compiler = os.environ.get('POCKET_HOST_CC') or shutil.which('gcc') or shutil.which('clang')
    cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
    return compiler or (str(cached) if cached.is_file() else None)


class FirmwareUsageCoreTests(unittest.TestCase):
    def test_usage_formatter(self):
        compiler = host_compiler()
        if not compiler:
            self.skipTest('host C compiler unavailable')
        root = pathlib.Path(__file__).resolve().parents[1] / 'firmware'
        with tempfile.TemporaryDirectory() as temp:
            binary = pathlib.Path(temp) / ('test-usage.exe' if os.name == 'nt' else 'test-usage')
            built = subprocess.run([
                compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                '-I' + str(root / 'core'), str(root / 'core/pocket_usage.c'),
                str(root / 'tests/test_usage.c'), '-o', str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
