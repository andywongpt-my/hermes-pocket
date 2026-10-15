"""Exercise the actual bounded diagnostic formatter with literal observations."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HARNESS = r'''
#include "pocket_diagnostics.h"
#include <stdio.h>
#include <string.h>
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)
int main(void){
 pocket_diagnostics d={0};char title[40],body[240];
 pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));
 CHECK(!strcmp(title,"NOT CHECKED"),"initial zero state looks like a failure or success");
 d.checked=true;d.checked_ms=1000;
 pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"WI-FI OFFLINE"),"Wi-Fi failure obscured");
 d.wifi=true;pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"WAITING FOR TIME"),"secure-time prerequisite obscured");
 d.utc_ready=true;pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"SETUP REQUIRED"),"missing configuration obscured");
 d.configured=true;d.transport_error=-1;d.tls_error=0x801a;d.tls_code=0x2700;d.verify_flags=8;
 d.attempted=true;d.heap_measured=true;
 pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));
 CHECK(!strcmp(title,"TLS FAILED") && strstr(body,"HTTP 0 / ESP -1") && strstr(body,"TLS 801a / MB 2700") && strstr(body,"Verify 8"),"HTTP/transport/TLS evidence was merged or lost");
 d.tls_error=d.tls_code=d.verify_flags=0;pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"CONNECTION FAILED"),"generic failure was falsely attributed to TLS");
 d.tls_error=0x8008;pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"CONNECTION FAILED"),"TCP FIN was falsely attributed to TLS");d.tls_error=0;
 d.transport_error=0;d.http_status=401;pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"LOGIN REQUIRED"),"401 not classified");
 d.http_status=503;pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"HTTP FAILED"),"HTTP failure not classified");
 d.http_status=200;pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"INVALID RESPONSE"),"malformed status became ready");
 d.response_valid=true;pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"HERMES UNAVAILABLE"),"backend unavailable became ready");
 d.backend_available=true;d.stt=true;d.tts=false;d.stt_known=d.tts_known=true;d.free_heap=53052;d.largest_block=40960;
 pocket_diagnostics_format(&d,21000,false,title,sizeof(title),body,sizeof(body));
 CHECK(!strcmp(title,"HERMES AVAILABLE") && strstr(body,"STT yes / TTS no") && strstr(body,"53052 / 40960") && strstr(body,"Age 20s"),"fresh capabilities/heap/age not shown");
 d.stt_known=d.tts_known=false;pocket_diagnostics_format(&d,21000,false,title,sizeof(title),body,sizeof(body));
 CHECK(strstr(body,"STT ? / TTS ?"),"missing/non-boolean capabilities were presented as disabled");
 pocket_diagnostics_format(&d,47000,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"STATUS STALE"),"old success presented as current");
 pocket_diagnostics_format(&d,47000,true,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"POLLING PAUSED"),"voice pause confused with failure");
 pocket_diagnostics_format(&d,0,false,title,sizeof(title),body,sizeof(body));CHECK(!strcmp(title,"STATUS STALE"),"clock reversal presented fresh status");
 char tiny[4]={1,1,1,1};pocket_diagnostics_format(&d,0,false,tiny,sizeof(tiny),NULL,0);CHECK(tiny[3]==0,"bounded formatter did not terminate");
 d=(pocket_diagnostics){.checked=true};pocket_diagnostics_format(&d,1000,false,title,sizeof(title),body,sizeof(body));
 CHECK(strstr(body,"HTTP not attempted") && strstr(body,"Heap not measured") && !strstr(body,"ESP 0") && !strstr(body,"STT no"),"unmeasured prerequisites became real zero/capability observations");
 puts("PASS: diagnostic prerequisites, evidence attribution, capabilities and stale/paused state");return 0;
}
'''


class FirmwareDiagnosticsTests(unittest.TestCase):
    def test_diagnostic_layers_and_stale_success(self):
        compiler = shutil.which('clang') or shutil.which('gcc')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        source = pathlib.Path(__file__).resolve().parents[1]/'firmware/core'
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            (temp/'fixture.c').write_text(HARNESS)
            binary = temp/('diagnostics.exe' if os.name == 'nt' else 'diagnostics')
            built = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                                    '-I'+str(source), str(source/'pocket_diagnostics.c'),
                                    str(temp/'fixture.c'), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)

