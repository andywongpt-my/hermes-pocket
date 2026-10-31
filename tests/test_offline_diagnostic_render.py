"""Full real LVGL 9.5 render; no firmware/device execution."""
import os,shutil,subprocess,tempfile,unittest
from pathlib import Path
class OfflineDiagnosticRenderTests(unittest.TestCase):
 def test_actual_lvgl_silent_diagnostic_states(self):
  lvgl=os.environ.get('LVGL_DIR');cc=shutil.which('gcc');root=Path(__file__).resolve().parents[1]/'firmware'
  if not lvgl or not cc:self.skipTest('LVGL_DIR and GCC required')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);out=Path(os.environ.get('POCKET_SNAPSHOT_DIR',d));out.mkdir(parents=True,exist_ok=True)
   inc=['-DCONFIG_POCKET_OFFLINE_DIAGNOSTIC=1','-DLV_CONF_INCLUDE_SIMPLE',*['-I'+str(x) for x in [root/'tests',Path(lvgl),root/'main',root/'core',root/'fonts']]]
   ours=[root/'core'/n for n in ['pocket_model.c','pocket_usage.c','pocket_diagnostics.c','pocket_clock.c','pocket_offline_diagnostic_ui.c']]+[root/'fonts/lv_font_pocket_cjk_16.c',root/'tests/offline_ui_snapshot.c']
   objects=[]
   for src in ours:
    obj=p/(src.stem+'.o');objects.append(str(obj))
    r=subprocess.run([cc,'-std=c11','-O1','-Wall','-Wextra','-Werror',*inc,'-c',str(src),'-o',str(obj)],capture_output=True,text=True,timeout=60);self.assertEqual(r.returncode,0,r.stderr)
   args=['-std=c11','-O1','-w',*inc,*objects,*map(str,Path(lvgl,'src').rglob('*.c')),'-lm','-o',str(p/'render')]
   rsp=p/'args';rsp.write_text('\n'.join('"'+a+'"' for a in args))
   r=subprocess.run([cc,'@'+str(rsp)],capture_output=True,text=True,timeout=600);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(p/'render'),str(out)],capture_output=True,text=True,timeout=120);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
   for name in ['ready','armed','running','done','cleanup-failed']:
    blob=(out/('offline-'+name+'.bmp')).read_bytes();self.assertEqual(len(blob),230454);self.assertGreater(len(set(blob[54::3])),8)
