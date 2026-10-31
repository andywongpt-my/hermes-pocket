"""Strict host controls using the real pinned cJSON source, not a parser mock."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HARNESS=r'''

#include "pocket_live_v2_control.h"
#include <assert.h>
#include <string.h>
int main(void){
 pocket_live_worker_event e;
 const char *ready="{\"type\":\"ready\",\"version\":2,\"rate\":24000,\"reference\":\"es8311_dac_right\"}";
 assert(pocket_live_v2_control(ready,strlen(ready),&e)&&e.kind==PLW_READY);
 const char *closed="{\"type\":\"closed\",\"finalized\":true,\"reason\":\"stopped\",\"backend_pending\":true,\"playback_pending\":false,\"actual_playout\":\"unknown\"}";
 assert(pocket_live_v2_control(closed,strlen(closed),&e)&&e.kind==PLW_CLOSED&&e.finalized&&e.backend_pending&&!e.playback_pending);
 const char *server="{\"type\":\"closed\",\"finalized\":true,\"reason\":\"server_closed\",\"backend_pending\":false,\"playback_pending\":false,\"actual_playout\":\"unknown\"}";
 assert(pocket_live_v2_control(server,strlen(server),&e)&&e.finalized);
 const char *bad[]={"{\"type\":\"ready\",\"version\":1,\"rate\":24000}","{\"type\":\"ready\",\"version\":2,\"rate\":24000,\"reference\":\"microphone\"}","{\"type\":\"ready\",\"version\":1.0,\"rate\":24000}","{\"type\":\"ready\",\"version\":1,\"rate\":2.4e4}","{\"type\":\"ready\\u0000evil\",\"version\":2,\"rate\":24000,\"reference\":\"es8311_dac_right\"}","{\"type\\u0000evil\":\"ready\",\"version\":2,\"rate\":24000,\"reference\":\"es8311_dac_right\"}","{}","[]","{\"type\":\"ready\",\"version\":true,\"rate\":24000}",
 "{\"type\":\"ready\",\"version\":1,\"rate\":16000}","{\"type\":\"ready\",\"type\":\"ready\",\"version\":2,\"rate\":24000,\"reference\":\"es8311_dac_right\"}",
 "{\"type\":\"ready\",\"version\":1,\"rate\":24000,\"extra\":0}","{\"type\":{\"x\":1}}",
 "{\"type\":\"closed\",\"finalized\":1,\"reason\":\"stopped\",\"backend_pending\":false,\"playback_pending\":false,\"actual_playout\":\"unknown\"}",
 "{\"type\":\"closed\",\"finalized\":true,\"reason\":\"invented\",\"backend_pending\":false,\"playback_pending\":false,\"actual_playout\":\"unknown\"}",
 "{\"type\":\"error\",\"code\":\"private secret\"}","{\"type\":\"ready\",\"version\":2,\"rate\":24000,\"reference\":\"es8311_dac_right\"}x"};
 for(size_t i=0;i<sizeof bad/sizeof bad[0];i++)assert(!pocket_live_v2_control(bad[i],strlen(bad[i]),&e));
 char nul[]="{}\0hidden"; assert(!pocket_live_v2_control(nul,sizeof nul-1,&e));
 const char*err="{\"type\":\"error\",\"code\":\"session_unconfirmed\"}";
 assert(pocket_live_v2_control(err,strlen(err),&e)&&e.kind==PLW_ERROR);
 return 0;
}
'''
class FirmwareLiveV2ControlTests(unittest.TestCase):
    def test_strict_control_schemas(self):
        root=Path(__file__).resolve().parents[1]
        self.assertTrue((root/'firmware/main/pocket_live_v2_control.c').exists(),'control parser missing')
        candidates=[Path(os.environ.get('POCKET_CJSON_DIR','/nonexistent')),
            Path(os.environ.get('IDF_PATH','/nonexistent'))/'components/json/cJSON']
        src=next((p for p in candidates if (p/'cJSON.c').is_file()),None)
        if src is None:self.skipTest('exact cJSON source unavailable; set POCKET_CJSON_DIR')
        cc=shutil.which('gcc')
        if not cc:self.skipTest('GCC unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(HARNESS)
            cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic','-O2',
                '-I'+str(root/'firmware/core'),'-I'+str(root/'firmware/main'),'-I'+str(src),str(p/'test.c'),
                str(root/'firmware/main/pocket_live_v2_control.c'),str(src/'cJSON.c'),'-lm','-o',str(p/'test')]
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
