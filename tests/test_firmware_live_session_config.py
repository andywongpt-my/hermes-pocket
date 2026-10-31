"""Actual inactive Live config parser; no network, credentials or device access."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

HARNESS = r'''
#include "pocket_live_session_config.h"
#include <assert.h>
#include <string.h>
static const char *token="abcdefghijklmnopqrstuvwxyz012345";
static void zero(const pocket_live_session_config *c){const unsigned char*p=(const unsigned char*)c;for(unsigned i=0;i<sizeof *c;i++)assert(!p[i]);}
static bool build(pocket_live_session_config*c,const char*origin){return pocket_live_session_config_snapshot(c,origin,token,"CERT\r\n", "chat_A-1",1,1,60);}
int main(void){
 pocket_live_session_config c;
 assert(build(&c,"https://Bridge.example:8443/"));
 assert(!strcmp(c.host,"Bridge.example")&&c.port==8443&&c.target==1&&c.epoch==1&&c.volume==60);
 assert(!strcmp(c.token,token)&&!strcmp(c.ca_pem,"CERT\r\n")&&!strcmp(c.chat_id,"chat_A-1"));
 assert(pocket_live_session_config_valid(&c));
 assert(build(&c,"https://192.168.1.5"));assert(c.port==443);
 const char *bad[]={"","http://bridge.example","wss://bridge.example","https://","https://host/path","https://host//","https://host?x","https://host#x","https://user@host","https://host:0","https://host:65536","https://host:+443","https://host:0443","https://host:","https://host:443:5","https://[::1]","https://a..b","https://.host","https://host.","https://-host","https://host-","https://ho_st","https://host%2eexample","https://01.2.3.4","https://999.2.3.4","https://1.2.3","https://host\n","https://host\\x"};
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){memset(&c,0x5a,sizeof c);assert(!build(&c,bad[i]));zero(&c);}
 char origin[194];memset(origin,'a',sizeof origin);assert(!build(&c,origin));zero(&c);
 char label[90]="https://";memset(label+8,'a',64);label[72]=0;assert(!build(&c,label));label[71]=0;assert(build(&c,label));
 char t[130],ca[2050],chat[66];memset(t,'x',128);t[128]=0;memset(ca,'A',2048);ca[2048]=0;memset(chat,'x',64);chat[64]=0;
 assert(pocket_live_session_config_snapshot(&c,"https://host",t,ca,chat,0,0xffffffffu,100));assert(pocket_live_session_config_valid(&c));
 char saved[129];memcpy(saved,c.token,sizeof saved);t[0]='y';ca[0]='B';chat[0]='y';assert(c.token[0]=='x'&&c.ca_pem[0]=='A'&&c.chat_id[0]=='x');
 assert(pocket_live_session_config_snapshot(&c,"https://host",c.token,c.ca_pem,c.chat_id,0,2,0));assert(!strcmp(c.token,saved)&&c.volume==0);
 t[128]='x';t[129]=0;assert(!pocket_live_session_config_snapshot(&c,"https://host",t,"","chat",0,1,60));zero(&c);t[128]=0;
 ca[2048]='A';ca[2049]=0;assert(!pocket_live_session_config_snapshot(&c,"https://host",token,ca,"chat",0,1,60));zero(&c);
 assert(!pocket_live_session_config_snapshot(&c,"https://host","short","","chat",0,1,60));zero(&c);
 const char *badchat[]={"","chat space","chat\"","chat/1","chat.1"};for(unsigned i=0;i<5;i++){assert(!pocket_live_session_config_snapshot(&c,"https://host",token,"",badchat[i],0,1,60));zero(&c);}
 assert(!pocket_live_session_config_snapshot(&c,"https://host",token,"bad\1","chat",0,1,60));zero(&c);
 assert(!pocket_live_session_config_snapshot(&c,"https://host",token,"","chat",2,1,60));zero(&c);
 assert(!pocket_live_session_config_snapshot(&c,"https://host",token,"","chat",0,0,60));zero(&c);
 assert(!pocket_live_session_config_snapshot(&c,"https://host",token,"","chat",0,1,101));zero(&c);
 assert(!pocket_live_session_config_snapshot(&c,0,token,"","chat",0,1,60));zero(&c);
 assert(!pocket_live_session_config_snapshot(0,"https://host",token,"","chat",0,1,60));
 assert(!pocket_live_session_config_valid(0));assert(build(&c,"https://host"));c.port=0;assert(!pocket_live_session_config_valid(&c));c.port=443;c.host[0]='-';assert(!pocket_live_session_config_valid(&c));
 assert(build(&c,"https://host"));memset(c.chat_id,'x',sizeof c.chat_id);assert(!pocket_live_session_config_valid(&c));
 assert(build(&c,"https://host"));pocket_live_session_config_clear(&c);zero(&c);pocket_live_session_config_clear(0);
 return 0;
}
'''

class LiveSessionConfigTests(unittest.TestCase):
    def test_real_config_parser_and_secret_lifetime(self):
        root = Path(__file__).resolve().parents[1]
        implementation = root / 'firmware/main/pocket_live_session_config.c'
        self.assertTrue(implementation.exists(), 'inactive Live config snapshot implementation missing')
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            (temp / 'test.c').write_text(HARNESS)
            flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-Wconversion', '-Wshadow', '-pedantic']
            if os.environ.get('POCKET_TEST_SANITIZE') == '1':
                flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
            subprocess.run(['cc', *flags, '-I', str(implementation.parent), str(temp/'test.c'), str(implementation), '-o', str(temp/'test')], check=True)
            subprocess.run([str(temp/'test')], check=True, timeout=10)

    def test_session_sources_are_compile_only_and_gate_still_refuses(self):
        root = Path(__file__).resolve().parents[1]
        cmake = (root/'firmware/main/CMakeLists.txt').read_text()
        paired = cmake.split('if(CONFIG_POCKET_LIVE_PAIRED_COMPILE)', 1)[1].split('endif()', 1)[0]
        self.assertIn('"pocket_live_session.c"', paired)
        self.assertIn('"pocket_live_session_config.c"', paired)
        for file in ['main.c', 'pocket_voice.c', 'pocket_ui.c']:
            text = (root/'firmware/main'/file).read_text()
            self.assertNotIn('pocket_live_session_execute(', text)
            self.assertNotIn('pocket_live_v2_idf_execute(', text)
        gate = (root/'firmware/main/pocket_live_v2_gate.c').read_text()
        self.assertIn('return ESP_ERR_NOT_SUPPORTED;', gate)
        self.assertIn('PLW_DEVICE_VALIDATION_REQUIRED', gate)
