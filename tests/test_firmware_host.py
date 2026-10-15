import os
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

    def test_ui_snapshots(self):
        # Renders firmware/main/pocket_ui.c with the same 24 KB LVGL pool as the device;
        # an out-of-memory assert hangs, so the timeout catches pool regressions.
        compiler=shutil.which('gcc');lvgl=os.environ.get('LVGL_DIR')
        if not compiler or not lvgl:self.skipTest('host C compiler or LVGL_DIR (LVGL 9.5 source) unavailable')
        root=pathlib.Path(__file__).resolve().parents[1]/'firmware'
        include=['-DLV_CONF_INCLUDE_SIMPLE','-I'+str(root/'tests'),'-I'+lvgl,'-I'+str(root/'main'),'-I'+str(root/'core')]
        names=['home','home-offline','chat-ready','chat-listening','chat-thinking','chat-speaking','chat-error','chat-offline','usage','screen','setup']
        with tempfile.TemporaryDirectory() as temp:
            out=os.environ.get('POCKET_SNAPSHOT_DIR') or temp
            subprocess.run([compiler,'-c','-std=c11','-O1','-Wall','-Wextra','-Werror',*include,str(root/'main/pocket_ui.c'),str(root/'tests/ui_snapshot.c')],cwd=temp,check=True,capture_output=True)
            sources=sorted(str(p) for p in pathlib.Path(lvgl,'src').rglob('*.c'))
            binary=pathlib.Path(temp)/('ui-snapshot.exe' if os.name=='nt' else 'ui-snapshot')
            subprocess.run([compiler,'-std=c11','-O1','-w',*include,'pocket_ui.o','ui_snapshot.o',*sources,'-lm','-o',str(binary)],cwd=temp,check=True,capture_output=True)
            subprocess.run([str(binary),out],check=True,capture_output=True,timeout=120)
            for name in names:
                image=pathlib.Path(out,name+'.bmp').read_bytes()
                self.assertEqual(len(image),54+240*320*3,name)
                self.assertGreater(len(set(image[54::3])),8,name+' rendered blank')
