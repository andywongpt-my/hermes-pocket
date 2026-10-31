"""Qualification TLS framing only; no sockets/configuration/device operations."""
from pathlib import Path
import subprocess,tempfile,unittest
class TLSProtocolTests(unittest.TestCase):
 def test_strict_lengths_budgets(self):
  root=Path(__file__).resolve().parents[1];core=root/'firmware/core'
  code=r'''
#include "pocket_qualification_protocol.h"
#include <assert.h>
#include <string.h>
int main(void){uint8_t p[1024]={0},out[1056];uint32_t v[]={24000,60000,1440000,1024,3145728,443,60000,7|(32u<<16),1};for(unsigned i=0;i<9;i++)pocket_qualification_put_u32(p+4*i,v[i]);memcpy(p+36,"10.0.0.1",7);memset(p+43,'a',32);p[75]='x';pocket_qualification_packet q={.nonce=1,.kind=PQ_ARM_TLS,.profile=PQ_AUDIO,.payload=p,.bytes=76};assert(pocket_qualification_encode(&q,out,sizeof out)==108);pocket_qualification_packet parsed;assert(pocket_qualification_decode(out,108,&parsed)==PQ_WIRE_OK);for(unsigned i=0;i<9;i++){uint32_t old=pocket_qualification_get_u32(p+4*i);pocket_qualification_put_u32(p+4*i,0);assert(!pocket_qualification_encode(&q,out,sizeof out));pocket_qualification_put_u32(p+4*i,old);}uint32_t bad[][2]={{5,65536},{6,60001},{7,6|(32u<<16)},{7,16|(32u<<16)},{7,7|(31u<<16)},{7,7|(129u<<16)},{8,769},{8,0x10001}};for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){unsigned k=bad[i][0];uint32_t old=pocket_qualification_get_u32(p+4*k);pocket_qualification_put_u32(p+4*k,bad[i][1]);assert(!pocket_qualification_encode(&q,out,sizeof out));pocket_qualification_put_u32(p+4*k,old);}q.bytes++;assert(!pocket_qualification_encode(&q,out,sizeof out));q.bytes=76;q.sequence=1;assert(!pocket_qualification_encode(&q,out,sizeof out));q.sequence=0;q.kind=PQ_TLS_START;q.bytes=48;assert(pocket_qualification_encode(&q,out,sizeof out)==80);q.kind=PQ_TLS_TERMINAL;q.bytes=96;q.sequence=1;assert(pocket_qualification_encode(&q,out,sizeof out)==128);q.sequence=2;assert(!pocket_qualification_encode(&q,out,sizeof out));return 0;}
'''
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'t.c').write_text(code)
   subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I',str(core),str(p/'t.c'),str(core/'pocket_qualification_protocol.c'),'-o',str(p/'t')],check=True)
   subprocess.run([str(p/'t')],check=True)
