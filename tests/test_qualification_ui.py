"""Full real LVGL 9.5 render; no firmware/device execution."""
import os,shutil,subprocess,tempfile,unittest
from pathlib import Path
class QualificationRenderTests(unittest.TestCase):
 def test_actual_lvgl_qualification_and_legacy_states(self):
  lvgl=os.environ.get('LVGL_DIR');cc=shutil.which('gcc');root=Path(__file__).resolve().parents[1]/'firmware'
  if not lvgl or not cc:self.skipTest('LVGL_DIR and GCC required')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);out=Path(os.environ.get('POCKET_SNAPSHOT_DIR',d));out.mkdir(parents=True,exist_ok=True)
   source=(root/'main/pocket_qualification_voice.inc').read_text()
   marker='void pocket_voice_qualification_format('
   self.assertEqual(source.count(marker),1)
   formatter=marker+source.split(marker,1)[1].split('\n}',1)[0]+'\n}\n'
   (p/'qualification_formatter_fixture.h').write_text('#include "pocket_qualification_control.h"\nstatic int mux;\nstatic pocket_qualification_control qualification_control;\nstatic uint32_t qualification_frames;\nstatic bool qualification_terminal_confirmed,qualification_network_wait;\n#define portENTER_CRITICAL(p) ((void)(p))\n#define portEXIT_CRITICAL(p) ((void)(p))\nvoid pocket_qualification_control_read(const pocket_qualification_control*c,pocket_qualification_control_snapshot*s){*s=c->state;}\n'+formatter)
   inc=['-I'+str(p),'-DLV_CONF_INCLUDE_SIMPLE',*['-I'+str(x) for x in [root/'tests',Path(lvgl),root/'main',root/'core',root/'fonts']]]
   ours=[root/'core'/n for n in ['pocket_model.c','pocket_usage.c','pocket_diagnostics.c','pocket_clock.c','pocket_offline_diagnostic_ui.c']]+[root/'fonts/lv_font_pocket_cjk_16.c',root/'tests/qualification_ui_snapshot.c']
   objects=[]
   for src in ours:
    obj=p/(src.stem+'.o');objects.append(str(obj))
    r=subprocess.run([cc,'-std=c11','-O1','-Wall','-Wextra','-Werror',*inc,'-c',str(src),'-o',str(obj)],capture_output=True,text=True,timeout=60);self.assertEqual(r.returncode,0,r.stderr)
   args=['-std=c11','-O1','-w',*inc,*objects,*map(str,Path(lvgl,'src').rglob('*.c')),'-lm','-o',str(p/'render')]
   rsp=p/'args';rsp.write_text('\n'.join('"'+a+'"' for a in args))
   r=subprocess.run([cc,'@'+str(rsp)],capture_output=True,text=True,timeout=600);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(p/'render'),str(out)],capture_output=True,text=True,timeout=120);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
   for name in ['usb-max','usb-armed','usb-cleanup','tls-max','tls-pending','tls-cleanup','tls-finished','idle','silent-request','audio-request','armed','running','host-loss','finished','cleanup-locked']:
    blob=(out/('qualification-'+name+'.bmp')).read_bytes();self.assertEqual(len(blob),230454);self.assertGreater(len(set(blob[54::3])),8)
