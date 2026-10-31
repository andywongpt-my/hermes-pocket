"""Read-only numeric panes; no new input/stop semantics."""
from pathlib import Path
import subprocess, tempfile, unittest
HARNESS = r'''
#include "pocket_offline_diagnostic_ui.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#ifndef POCKET_DIAGNOSTIC_PAGES
int main(void){fputs("numeric panes unavailable\n",stderr);assert(0);}
#else
int main(void){
 char title[40],body[240],footer[100];
 pocket_offline_diagnostic_snapshot s={.phase=PDIAG_ERROR,.code=PDIAG_STOPPED,.frames=882,.samples=211680,.mean={-159,0},.rms={2683,0},.peak={32768,0},.clips={1030,0},.negative_clips={1030,0},.first_clip_sample={0,UINT32_MAX},.last_clip_sample={1029,UINT32_MAX},.longest_clip_run={1030,0},.cleanup_attempted=true,.restore_ok=true};
 s.early.samples=2400;s.early.clips[0]=1030;s.late.samples=209280;
 for(unsigned page=0;page<POCKET_DIAGNOSTIC_PAGES;page++){
  pocket_offline_diagnostic_format_page(&s,page,title,sizeof title,body,sizeof body,footer,sizeof footer);
  char n[8];snprintf(n,sizeof n,"%u/5",page+1);assert(strstr(title,n));
  assert(strstr(body,"checked")&&strstr(body,"code 1")&&!strstr(body,"passed"));assert(strstr(footer,"UP/DOWN"));
  if(page==0)assert(strstr(body,"Frames 882")&&strstr(body,"MIC RMS 2683"));
  if(page==1)assert(strstr(body,"MIC DC -159")&&strstr(body,"n2400")&&strstr(body,"n209280")&&strstr(body,"Rails 1030"));
  if(page==2)assert(strstr(body,"REF DC 0")&&strstr(body,"Rails 0"));
  if(page==3)assert(strstr(body,"MIC rails")&&strstr(body,"+ 0  - 1030")&&strstr(body,"First sample 0")&&strstr(body,"Last sample 1029")&&strstr(body,"Longest run 1030"));
  if(page==4)assert(strstr(body,"REF rails")&&strstr(body,"First sample none")&&strstr(body,"Last sample none")&&!strstr(body,"4294967295"));
 }
 s.phase=PDIAG_RUNNING;pocket_offline_diagnostic_format_page(&s,UINT32_MAX,title,sizeof title,body,sizeof body,footer,sizeof footer);assert(strstr(footer,"Any press: stop"));
 s.phase=PDIAG_CLEANUP_FAILED;s.restore_ok=false;pocket_offline_diagnostic_format_page(&s,1,title,sizeof title,body,sizeof body,footer,sizeof footer);assert(strstr(body,"FAILED")&&strstr(footer,"Screen off"));
 s.phase=PDIAG_ARMED;pocket_offline_diagnostic_format_page(&s,4,title,sizeof title,body,sizeof body,footer,sizeof footer);assert(strstr(body,"Fresh")&&!strstr(title,"/5"));
 struct {char before;char text[1];char after;} tiny={'a',{0},'z'};
 pocket_offline_diagnostic_format_page(&s,3,tiny.text,1,tiny.text,1,tiny.text,1);assert(tiny.before=='a'&&tiny.after=='z'&&tiny.text[0]==0);
 return 0;
}
#endif
'''
class OfflineObservationsUITests(unittest.TestCase):
 def test_panes_signs_no_rail_sentinel_cleanup_and_buffer_bounds(self):
  root=Path(__file__).resolve().parents[1]/'firmware/core'
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'test.c').write_text(HARNESS)
   subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(root),str(p/'test.c'),str(root/'pocket_offline_diagnostic_ui.c'),'-o',str(p/'test')],check=True)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
