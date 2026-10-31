from pathlib import Path
import subprocess,tempfile,unittest
HARNESS=r'''
#include "pocket_offline_diagnostic_ui.h"
#include <assert.h>
#include <string.h>
int main(void){char title[40],body[240],footer[100];pocket_offline_diagnostic_snapshot s={0};
 pocket_offline_diagnostic_format(&s,title,sizeof title,body,sizeof body,footer,sizeof footer);
 assert(strstr(title,"SILENT")&&strstr(footer,"Hold OK")&&!strstr(body,"passed"));
 s.phase=PDIAG_ARMED;pocket_offline_diagnostic_format(&s,title,sizeof title,body,sizeof body,footer,sizeof footer);assert(strstr(footer,"fresh OK")&&strstr(body,"10 seconds"));
 s.phase=PDIAG_RUNNING;s.frames=32;s.rms[0]=123;s.peak[1]=32768;s.clips[1]=2;
 pocket_offline_diagnostic_format(&s,title,sizeof title,body,sizeof body,footer,sizeof footer);assert(strstr(body,"Frames 32")&&strstr(body,"123")&&strstr(body,"32768")&&strstr(footer,"stop")&&strstr(body,"pending"));
 s.phase=PDIAG_CLEANUP_FAILED;s.restore_ok=false;pocket_offline_diagnostic_format(&s,title,sizeof title,body,sizeof body,footer,sizeof footer);assert(strstr(title,"CLEANUP FAILED")&&strstr(body,"unavailable")&&strstr(footer,"Screen off"));
 s.phase=PDIAG_ERROR;pocket_offline_diagnostic_format(&s,title,sizeof title,body,sizeof body,footer,sizeof footer);assert(strstr(body,"not started"));
 s.phase=PDIAG_DONE;s.cleanup_attempted=true;s.restore_ok=true;pocket_offline_diagnostic_format(&s,title,sizeof title,body,sizeof body,footer,sizeof footer);assert(strstr(body,"checked")&&strstr(body,"Not acoustic")&&!strstr(body,"passed"));return 0;}
'''
class OfflineDiagnosticUITests(unittest.TestCase):
 def test_observation_labels_and_cleanup(self):
  root=Path(__file__).resolve().parents[1]/'firmware'
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'test.c').write_text(HARNESS)
   r=subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(root/'core'),str(p/'test.c'),str(root/'core/pocket_offline_diagnostic_ui.c'),'-o',str(p/'test')],capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
