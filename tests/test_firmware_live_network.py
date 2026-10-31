"""No network: actual C DNS ownership/deadline state against synthetic time."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
HARNESS=r'''
#include "pocket_live_network.h"
#include <assert.h>
#include <string.h>
#include <stdint.h>
int main(void){
 pocket_live_network_dns s; pocket_live_network_dns_init(&s);uint32_t a=0,b=0;char ip[48];
 assert(pocket_live_network_dns_begin(&s,"bridge.example",&a));
 assert(!pocket_live_network_dns_begin(&s,"other.example",&b));
 assert(pocket_live_network_dns_start(&s,a));
 pocket_live_network_dns_cancel(&s,a);
 assert(!pocket_live_network_dns_begin(&s,"other.example",&b));
 assert(!pocket_live_network_dns_take(&s,a,ip,sizeof ip));
 assert(pocket_live_network_dns_complete(&s,a,"192.0.2.1"));
 assert(!s.busy);
 assert(pocket_live_network_dns_begin(&s,"other.example",&b)&&b>a);
 assert(!pocket_live_network_dns_complete(&s,a,"192.0.2.2"));
 assert(pocket_live_network_dns_start(&s,b));
 assert(pocket_live_network_dns_complete(&s,b,"192.0.2.3"));
 assert(pocket_live_network_dns_take(&s,b,ip,sizeof ip)&&!strcmp(ip,"192.0.2.3"));
 assert(!s.busy);
 assert(pocket_live_network_dns_begin(&s,"queued.example",&a));
 pocket_live_network_dns_cancel(&s,a);
 assert(!pocket_live_network_dns_start(&s,a));
 assert(pocket_live_network_dns_complete(&s,a,0)&&!s.busy);
 assert(!pocket_live_network_dns_begin(&s,"bad\r\nAuthorization:x",&a));
 s.generation=UINT32_MAX;assert(!pocket_live_network_dns_begin(&s,"ok.example",&a));
 pocket_live_network_deadline d; pocket_live_network_deadline_init(&d);
 assert(pocket_live_network_deadline_begin(&d,100,25));
 assert(!pocket_live_network_deadline_begin(&d,110,25)); /* cannot renew */
 assert(pocket_live_network_deadline_slice(&d,100,50,false)==25);
 assert(pocket_live_network_deadline_slice(&d,120,50,false)==5);
 assert(pocket_live_network_deadline_slice(&d,125,50,false)==0&&d.failed);
 assert(!pocket_live_network_deadline_begin(&d,126,25));
 pocket_live_network_deadline_init(&d);assert(pocket_live_network_deadline_begin(&d,10,50));
 assert(pocket_live_network_deadline_slice(&d,12,10,true)==0&&d.failed);
 pocket_live_network_deadline_init(&d);assert(pocket_live_network_deadline_begin(&d,10,50));
 assert(pocket_live_network_deadline_slice(&d,9,10,false)==0&&d.failed);
 pocket_live_network_deadline_init(&d);assert(!pocket_live_network_deadline_begin(&d,UINT64_MAX-1,50));
 pocket_live_network_deadline_init(&d);assert(pocket_live_network_deadline_begin(&d,10,50));
 assert(pocket_live_network_deadline_finish(&d,20,false));assert(!d.active&&!d.failed);
 assert(pocket_live_network_deadline_begin(&d,21,10));
 assert(!pocket_live_network_deadline_finish(&d,31,false));
 return 0;
}
'''
class FirmwareLiveNetworkTests(unittest.TestCase):
 def test_dns_late_callback_and_absolute_operation_deadlines(self):
  root=Path(__file__).resolve().parents[1];source=root/'firmware/main/pocket_live_network.c'
  self.assertTrue(source.exists(),'network ownership core missing')
  cc=shutil.which('gcc')
  if not cc:self.skipTest('GCC unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'main.c').write_text(HARNESS)
   r=subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic','-O2','-I'+str(root/'firmware/main'),str(p/'main.c'),str(source),'-o',str(p/'test')],capture_output=True,text=True,timeout=20)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
