"""Exercise real allocation-free UTF8 copying at display/state byte limits."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

FIXTURE = r'''
#include "pocket_utf8.h"
#include <stdio.h>
#include <string.h>
#define CHECK(c,m) do {if(!(c)){puts("FAIL: " m);return 1;}}while(0)
int main(void){
 const char mixed[]="A\xc3\xa9\xe6\xb8\xac\xf0\x9f\x90\xb1Z";
 const size_t ends[]={0,1,1,3,3,3,6,6,6,6,10,11};
 for(size_t n=0;n<sizeof ends/sizeof ends[0];n++)
  CHECK(pocket_utf8_prefix(mixed,n)==ends[n],"byte limit split a2/3/4-byte codepoint");
 CHECK(pocket_utf8_prefix(NULL,100)==0 && pocket_utf8_prefix(mixed,100)==11,"null or short input bounds failed");
 const char *bad[]={"\xc0\x80","\xed\xa0\x80","\xf4\x90\x80\x80","\xe6\xb8","\xf0\x9f","\x80"};
 for(size_t n=0;n<sizeof bad/sizeof bad[0];n++)CHECK(pocket_utf8_prefix(bad[n],100)==0,"invalid Unicode scalar accepted");
 char out[16];memset(out,'G',sizeof out);
 CHECK(pocket_utf8_copy(out,8,mixed,100)==6 && !strcmp(out,"A\xc3\xa9\xe6\xb8\xac") && out[8]=='G',"capacity clipping or guard byte failed");
 CHECK(pocket_utf8_copy(out,sizeof out,mixed,4)==3 && !strcmp(out,"A\xc3\xa9"),"explicit display byte limit failed");
 CHECK(pocket_utf8_copy(out,1,mixed,100)==0 && !out[0],"one-byte destination lacks terminator");
 out[0]='G';CHECK(pocket_utf8_copy(out,0,mixed,100)==0 && out[0]=='G',"zero capacity wrote data");
 CHECK(pocket_utf8_copy(out,sizeof out,NULL,100)==0 && !out[0],"null input not empty");
 char alias[sizeof mixed];memcpy(alias,mixed,sizeof mixed);
 CHECK(pocket_utf8_copy(alias,sizeof alias,alias,4)==3 && !strcmp(alias,"A\xc3\xa9"),"in-place clipping failed");
 puts("PASS: UTF8 scalar boundaries, malformed sequences, bounds, terminators and aliasing");return 0;
}
'''


class FirmwareUTF8Tests(unittest.TestCase):
    def test_utf8_copy_preserves_complete_scalars_and_destination_bounds(self):
        compiler = shutil.which('gcc') or shutil.which('clang')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            self.skipTest('host compiler unavailable')
        core = pathlib.Path(__file__).resolve().parents[1] / 'firmware/core'
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            source = temp / 'utf8.c'
            source.write_text(FIXTURE, encoding='utf-8')
            binary = temp / 'utf8.exe'
            run = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                                  '-pedantic', '-I'+str(core), str(source), '-o', str(binary)],
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            self.assertIn('PASS:', run.stdout)
