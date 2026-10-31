"""Actual opt-in USB binding against a strict fake IDF driver; never opens USB."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include "pocket_qualification_usb.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include <assert.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
static bool installed;
static int installs,switches,hooks,logs,writes,reads,waits,mode;
static int written=-99,waiting=ESP_OK,read_result;
static unsigned char copied[1056];static size_t copied_size;
static int original_log(const char*f,va_list a){(void)f;(void)a;logs++;if(mode==6){assert(pocket_qualification_usb_export_begin());assert(pocket_qualification_usb_export_ready()==0&&waits==0);}return 7;}
static vprintf_like_t logger=original_log;
bool usb_serial_jtag_is_driver_installed(void){return installed;}
esp_err_t usb_serial_jtag_driver_install(usb_serial_jtag_driver_config_t*c){installs++;assert(c->tx_buffer_size==4096&&c->rx_buffer_size==4096);if(mode==2)return ESP_FAIL;installed=true;return ESP_OK;}
void usb_serial_jtag_vfs_use_driver(void){assert(installed);switches++;}
vprintf_like_t esp_log_set_vprintf(vprintf_like_t v){hooks++;vprintf_like_t old=logger;logger=v;return old;}
int usb_serial_jtag_write_bytes(const void*p,size_t n,TickType_t t){assert(t==0);writes++;assert(n<=sizeof copied);memcpy(copied,p,n);copied_size=n;return written==-99?(int)n:written;}
int usb_serial_jtag_read_bytes(void*p,uint32_t n,TickType_t t){assert(t==0&&n<=256);reads++;if(read_result>0&&read_result<=(int)n)memset(p,0x5a,read_result);return read_result;}
esp_err_t usb_serial_jtag_wait_tx_done(TickType_t t){assert(t==0);waits++;return waiting;}
static int logit(const char*f,...){va_list a;va_start(a,f);int r=logger(f,a);va_end(a);return r;}
int main(int argc,char**argv){assert(argc==2);mode=atoi(argv[1]);
 if(mode==1)installed=true;
 assert(!pocket_qualification_usb_available());
 bool ok=pocket_qualification_usb_init();
 if(mode==1||mode==2){assert(!ok&&!pocket_qualification_usb_available()&&!switches&&!hooks);assert(installs==(mode==2));assert(!pocket_qualification_usb_init());assert(installs==(mode==2));return 0;}
 assert(ok&&pocket_qualification_usb_available()&&installs==1&&switches==1&&hooks==1);
 assert(pocket_qualification_usb_init()&&installs==1&&switches==1&&hooks==1);
 if(mode==6){assert(logit("in-flight")==7);assert(pocket_qualification_usb_export_ready()==1&&waits==1);pocket_qualification_usb_export_end();return 0;}
 assert(logit("before")==7&&logs==1);
 unsigned char data[1056]={0x0a,0x0d,0,0xff};
 assert(pocket_qualification_usb_write(data,32)==-1&&!writes);
 assert(pocket_qualification_usb_export_begin());assert(!pocket_qualification_usb_export_begin());
 assert(logit("must not format %s","not inspected")==0&&logs==1);
 waiting=ESP_ERR_TIMEOUT;assert(pocket_qualification_usb_export_ready()==0&&waits==1);
 waiting=ESP_OK;assert(pocket_qualification_usb_export_ready()==1&&waits==2);
 assert(pocket_qualification_usb_write(data,sizeof data)==1&&writes==1&&copied_size==sizeof data&&!memcmp(copied,data,sizeof data));
 written=0;assert(pocket_qualification_usb_write(data,32)==0&&writes==2&&pocket_qualification_usb_available());
 assert(pocket_qualification_usb_write(data,31)==-1&&writes==2);
 assert(pocket_qualification_usb_write(data,1057)==-1&&writes==2);
 assert(pocket_qualification_usb_read(data,257)==-1&&!reads);
 read_result=3;assert(pocket_qualification_usb_read(data,10)==3&&reads==1&&data[0]==0x5a);
 if(mode==3){written=1;assert(pocket_qualification_usb_write(data,32)==-1&&!pocket_qualification_usb_available());assert(pocket_qualification_usb_write(data,32)==-1&&writes==3);}
 if(mode==4){read_result=11;assert(pocket_qualification_usb_read(data,10)==-1&&!pocket_qualification_usb_available());}
 if(mode==5){waiting=ESP_FAIL;assert(pocket_qualification_usb_export_ready()==-1&&!pocket_qualification_usb_available());}
 pocket_qualification_usb_export_end();assert(logit("after")==7&&logs==2);assert(pocket_qualification_usb_write(data,32)==-1);
 /* Installed driver/VFS/log hook persist for the boot; no uninstall API exists. */
 assert(installed&&installs==1&&switches==1&&hooks==1);return 0;
}
'''

class QualificationUSBTests(unittest.TestCase):
    def test_actual_usb_binding(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td); (p/'driver').mkdir()
            (p/'esp_err.h').write_text('#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_FAIL -1\n#define ESP_ERR_TIMEOUT 0x107\n')
            (p/'sdkconfig.h').write_text('#define CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG 1\n#define CONFIG_ESP_CONSOLE_SECONDARY_NONE 1\n')
            (p/'driver/usb_serial_jtag.h').write_text('''#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"
typedef unsigned TickType_t;
typedef struct {size_t tx_buffer_size,rx_buffer_size;} usb_serial_jtag_driver_config_t;
bool usb_serial_jtag_is_driver_installed(void);
esp_err_t usb_serial_jtag_driver_install(usb_serial_jtag_driver_config_t*);
int usb_serial_jtag_write_bytes(const void*,size_t,TickType_t);
int usb_serial_jtag_read_bytes(void*,uint32_t,TickType_t);
esp_err_t usb_serial_jtag_wait_tx_done(TickType_t);
''')
            (p/'driver/usb_serial_jtag_vfs.h').write_text('#pragma once\nvoid usb_serial_jtag_vfs_use_driver(void);\n')
            (p/'esp_log.h').write_text('#pragma once\n#include <stdarg.h>\ntypedef int(*vprintf_like_t)(const char*,va_list);\nvprintf_like_t esp_log_set_vprintf(vprintf_like_t);\n')
            (p/'test.c').write_text(HARNESS)
            subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(p),'-I',str(ROOT/'firmware/main'),str(p/'test.c'),str(ROOT/'firmware/main/pocket_qualification_usb.c'),'-o',str(p/'test')],check=True)
            for mode in range(7):
                with self.subTest(mode=mode):
                    result=subprocess.run([str(p/'test'),str(mode)],text=True,capture_output=True)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_usb_binding_has_no_destructive_or_vfs_binary_path(self):
        text=(ROOT/'firmware/main/pocket_qualification_usb.c').read_text()
        for forbidden in ('usb_serial_jtag_driver_uninstall(', 'usb_serial_jtag_vfs_use_nonblocking(', 'fwrite(', 'fputc(', 'usb_serial_jtag_is_connected('):
            self.assertNotIn(forbidden,text)
