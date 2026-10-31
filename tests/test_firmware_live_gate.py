"""The real-device gate cannot be enabled by a config value or build option."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
class FirmwareLiveGateTests(unittest.TestCase):
    def test_device_entry_refuses_without_any_io(self):
        root=Path(__file__).resolve().parents[1]
        source=root/'firmware/main/pocket_live_gate.c'
        self.assertTrue(source.exists(),'hard refusal gate missing')
        cc=shutil.which('gcc')
        if not cc:self.skipTest('GCC unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'esp_err.h').write_text('typedef int esp_err_t;\n#define ESP_ERR_NOT_SUPPORTED 0x106\n')
            (p/'test.c').write_text('''#include "pocket_live_idf.h"
#include <assert.h>
int main(void){pocket_live_worker_result r;
assert(pocket_live_device_run(0,0,&r)==ESP_ERR_NOT_SUPPORTED);
assert(r.reason==PLW_REFERENCE_UNAVAILABLE&&!r.finalized&&!r.terminal_received&&r.cleanup_complete);
assert(pocket_live_device_run(0,0,0)==ESP_ERR_NOT_SUPPORTED);return 0;}
''')
            cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+str(p),
                '-I'+str(root/'firmware/main'),'-I'+str(root/'firmware/core'),str(p/'test.c'),str(source),'-o',str(p/'test')]
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
