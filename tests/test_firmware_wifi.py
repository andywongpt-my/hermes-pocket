"""Real bounded Wi-Fi core with synthetic radio/storage boundaries; no device access."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

CORE_HARNESS = r'''
#include "pocket_wifi.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned scans,connects,disconnects,saves;static bool save_ok=true;
static pocket_wifi_profile connected_profile;static pocket_wifi_book persisted;
static pocket_wifi_visible visible[20];static size_t visible_count;
static int dis_result=1;
static int dis(void *x){(void)x;disconnects++;return dis_result;}
static bool con(void *x,const pocket_wifi_profile *p){(void)x;connects++;connected_profile=*p;return true;}
static size_t scan(void *x,pocket_wifi_visible *out,size_t cap){(void)x;scans++;size_t n=visible_count<cap?visible_count:cap;memcpy(out,visible,n*sizeof *out);return n;}
static bool save(void *x,const pocket_wifi_book *b){(void)x;saves++;if(save_ok)persisted=*b;return save_ok;}
static pocket_wifi_profile profile(const char *ssid){pocket_wifi_profile p={0};snprintf(p.ssid,sizeof p.ssid,"%s",ssid);snprintf(p.password,sizeof p.password,"synthetic-only");return p;}
static pocket_wifi_ops ops={NULL,dis,con,scan,save};
static pocket_wifi_snapshot radio;
static void reset(void){scans=connects=disconnects=saves=0;visible_count=0;save_ok=true;dis_result=1;memset(&radio,0,sizeof radio);memset(&persisted,0,sizeof persisted);}
static void success(pocket_wifi_engine *e,int64_t now){radio.association_ip=radio.got_ip;radio.associated++;radio.got_ip++;radio.connected=true;snprintf(radio.ssid,sizeof radio.ssid,"%s",connected_profile.ssid);pocket_wifi_tick(e,now,&radio,false);}
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)
int main(void){
 reset();pocket_wifi_book b;pocket_wifi_book_init(&b);pocket_wifi_profile home=profile("Synthetic Home");
 CHECK(pocket_wifi_book_import(&b,&home)&&b.count==1&&!b.sequence,"legacy profile not imported without a write");
 CHECK(pocket_wifi_book_valid(&b),"imported book invalid");
 pocket_wifi_engine e;pocket_wifi_init(&e,&b,&ops);pocket_wifi_tick(&e,0,&radio,true);
 CHECK(!connects&&!scans&&!saves,"busy voice must prevent radio/storage activity");
 pocket_wifi_tick(&e,1,&radio,false);CHECK(connects==1&&!strcmp(connected_profile.ssid,home.ssid),"boot did not try legacy network");
 success(&e,2);CHECK(!saves,"legacy boot rewrote NVS");
 unsigned n=scans;for(int i=0;i<10;i++)pocket_wifi_tick(&e,5000+i*5000,&radio,false);
 CHECK(scans==n&&connects==1,"healthy connection scanned or roamed");
 pocket_wifi_profile office=profile("Synthetic Office");
 CHECK(pocket_wifi_join(&e,&office,60000,&radio),"new network rejected");CHECK(!saves&&e.book.count==1,"untested network persisted");
 /* Stale connected/IP state alone never admits a save. */
 radio.connected=true;snprintf(radio.ssid,sizeof radio.ssid,"%s",office.ssid);pocket_wifi_tick(&e,60001,&radio,false);
 CHECK(!saves&&e.join_result==POCKET_WIFI_JOIN_TESTING,"stale IP validated new credentials");
 success(&e,60002);CHECK(saves==1&&e.book.count==2&&e.join_result==POCKET_WIFI_JOIN_SAVED,"fresh association/IP did not save");
 CHECK(!memcmp(&e.book.profiles[0],&home,sizeof home),"adding network changed old credentials");
 pocket_wifi_profile wrong=profile("Synthetic Wrong");CHECK(pocket_wifi_join(&e,&wrong,70000,&radio),"wrong-network attempt not admitted");
 radio.connected=false;radio.disconnected++;radio.reason=POCKET_WIFI_REASON_AUTH;pocket_wifi_tick(&e,70001,&radio,false);
 CHECK(e.join_result==POCKET_WIFI_JOIN_FAILED&&e.book.count==2&&saves==1,"authentication failure replaced saved networks");
 pocket_wifi_tick(&e,71002,&radio,false);CHECK(!strcmp(connected_profile.ssid,office.ssid),"failed addition did not fall back to last known network");success(&e,71003);
 CHECK(pocket_wifi_join(&e,&wrong,80000,&radio),"retry not admitted");pocket_wifi_cancel(&e,80001,&radio);CHECK(e.join_result==POCKET_WIFI_JOIN_CANCELLED&&e.book.count==2&&saves==1,"cancel committed candidate");
 pocket_wifi_tick(&e,81002,&radio,false);success(&e,81003);
 CHECK(pocket_wifi_join(&e,&wrong,90000,&radio),"timeout attempt not admitted");radio.connected=false;pocket_wifi_tick(&e,110001,&radio,false);
 CHECK(e.join_result==POCKET_WIFI_JOIN_FAILED&&e.connection==POCKET_WIFI_NO_IP&&saves==1,"bounded DHCP timeout not enforced");
 pocket_wifi_tick(&e,111002,&radio,false);success(&e,111003);
 save_ok=false;CHECK(pocket_wifi_join(&e,&wrong,120000,&radio),"save-failure attempt rejected");success(&e,120001);
 CHECK(e.join_result==POCKET_WIFI_JOIN_SAVE_FAILED&&e.book.count==2,"failed persistence replaced book");save_ok=true;
 pocket_wifi_tick(&e,121002,&radio,false);success(&e,121003);
 /* Fill the five slots, then refuse automatic eviction. */
 for(unsigned i=2;i<5;i++){char name[24];snprintf(name,sizeof name,"Synthetic %u",i);pocket_wifi_profile p=profile(name);CHECK(pocket_wifi_book_put(&e.book,&p)>=0,"could not fill a free slot");}
 unsigned before=saves,connections=connects;CHECK(!pocket_wifi_join(&e,&wrong,130000,&radio)&&e.join_result==POCKET_WIFI_JOIN_FULL,"sixth profile did not report full");
 CHECK(e.book.count==5&&saves==before&&connects==connections,"full list evicted or connected");
 pocket_wifi_profile replacement=e.book.profiles[0];snprintf(replacement.password,sizeof replacement.password,"updated-synthetic");
 CHECK(pocket_wifi_book_put(&e.book,&replacement)==0&&e.book.count==5,"existing SSID update needs an extra slot");
 CHECK(pocket_wifi_forget(&e,"Synthetic 3",140000,&radio)&&e.book.count==4,"explicit forget failed");
 CHECK(!pocket_wifi_forget(&e,"absent",140001,&radio),"unknown forget accepted");
 pocket_wifi_book bad=e.book;bad.count=6;CHECK(!pocket_wifi_book_valid(&bad),"oversized book accepted");bad=e.book;memset(bad.profiles[0].ssid,'a',sizeof bad.profiles[0].ssid);CHECK(!pocket_wifi_book_valid(&bad),"unterminated SSID accepted");
 bad=e.book;bad.profiles[1]=bad.profiles[0];CHECK(!pocket_wifi_book_valid(&bad),"duplicate SSIDs accepted");
 pocket_wifi_book rank;pocket_wifi_book_init(&rank);pocket_wifi_profile a=profile("A"),c=profile("C");pocket_wifi_book_put(&rank,&a);pocket_wifi_book_put(&rank,&c);rank.last=0;
 pocket_wifi_visible seen[2]={{{"A"},-75,false},{{"C"},-40,false}};
 CHECK(pocket_wifi_choose(&rank,seen,2,0)==0,"last successful visible network not preferred");
 CHECK(pocket_wifi_choose(&rank,seen,2,1)==1,"fallback did not select untried known network");
 CHECK(pocket_wifi_choose(&rank,seen,2,3)==-1,"exhausted cycle retried forever");
 CHECK(pocket_wifi_choose(&rank,seen+1,1,0)==1,"unavailable old network blocked visible known network");
 /* A disconnect barrier must finish before applying candidate settings. */
 reset();pocket_wifi_init(&e,&b,&ops);dis_result=0;CHECK(pocket_wifi_join(&e,&office,0,&radio),"barrier test rejected");CHECK(!connects,"candidate started before disconnect acknowledgement");
 radio.disconnected++;pocket_wifi_tick(&e,1,&radio,false);CHECK(connects==1,"candidate not started after disconnect acknowledgement");
 radio.got_ip++;radio.connected=true;snprintf(radio.ssid,sizeof radio.ssid,"%s",office.ssid);pocket_wifi_tick(&e,2,&radio,false);CHECK(!saves,"IP without fresh association saved candidate");
 radio.association_ip=radio.got_ip;radio.associated++;pocket_wifi_tick(&e,3,&radio,false);CHECK(!saves,"stale IP preceding association validated candidate");
 success(&e,4);CHECK(saves==1,"barrier candidate failed valid save");
 /* Empty authoritative book remains empty, including forgotten legacy. */
 CHECK(pocket_wifi_forget(&e,office.ssid,4,&radio),"forget added network failed");CHECK(pocket_wifi_forget(&e,home.ssid,5,&radio),"forget last network failed");CHECK(e.book.count==0&&pocket_wifi_book_valid(&e.book),"empty book invalid");
 puts("PASS Wi-Fi: migration, tested save, capacity, fallback, cancellation, fresh events, voice ownership and no roaming");return 0;
}
'''

NVS_HEADERS = {
    'esp_err.h': '''#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NVS_NOT_FOUND 0x1102
''',
    'nvs.h': '''#pragma once
#include <stddef.h>
#include "esp_err.h"
typedef unsigned nvs_handle_t;
#define NVS_READONLY 0
#define NVS_READWRITE 1
esp_err_t nvs_open(const char *,int,nvs_handle_t *);
esp_err_t nvs_get_blob(nvs_handle_t,const char *,void *,size_t *);
esp_err_t nvs_set_blob(nvs_handle_t,const char *,const void *,size_t);
esp_err_t nvs_commit(nvs_handle_t);
void nvs_close(nvs_handle_t);
''',
}

STORAGE_HARNESS = r'''
#include "pocket_wifi_store.h"
#include "pocket_pairing.h"
#include "nvs.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned char config[3000],slots[2][1024];static size_t sizes[2],config_size;
static unsigned writes,pair_writes;static bool set_fail,commit_fail,open_fail;
esp_err_t nvs_open(const char *name,int mode,nvs_handle_t *h){assert(mode==0||mode==1);if(open_fail)return ESP_FAIL;if(!strcmp(name,"pocket"))*h=1;else{assert(!strcmp(name,"pocket_wifi"));*h=2;}return ESP_OK;}
esp_err_t nvs_get_blob(nvs_handle_t h,const char *key,void *out,size_t *size){
 const void *data;size_t n;if(h==1){assert(!strcmp(key,"config"));data=config;n=config_size;}else{assert(h==2&&(!strcmp(key,"a")||!strcmp(key,"b")));unsigned i=*key=='a'?0:1;data=slots[i];n=sizes[i];}
 if(!n){return ESP_ERR_NVS_NOT_FOUND;}
 if(out){if(*size<n)return ESP_FAIL;memcpy(out,data,n);}*size=n;return ESP_OK;
}
esp_err_t nvs_set_blob(nvs_handle_t h,const char *key,const void *data,size_t size){
 writes++;if(set_fail)return ESP_FAIL;if(h==1){assert(!strcmp(key,"config")&&size<sizeof config);pair_writes++;memcpy(config,data,size);config_size=size;}
 else{assert(h==2&&(!strcmp(key,"a")||!strcmp(key,"b")));unsigned i=*key=='a'?0:1;assert(size<sizeof slots[i]);memcpy(slots[i],data,size);sizes[i]=size;}return ESP_OK;
}
esp_err_t nvs_commit(nvs_handle_t h){assert(h==1||h==2);return commit_fail?ESP_FAIL:ESP_OK;}
void nvs_close(nvs_handle_t h){assert(h==1||h==2);}
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)
int main(void){
 pocket_pairing_config legacy={.version=2};snprintf(legacy.ssid,sizeof legacy.ssid,"Synthetic Home");snprintf(legacy.pass,sizeof legacy.pass,"synthetic-password");
 snprintf(legacy.windows,sizeof legacy.windows,"https://win.invalid");snprintf(legacy.linux_url,sizeof legacy.linux_url,"https://linux.invalid");memset(legacy.token,'s',64);snprintf(legacy.ca,sizeof legacy.ca,"-----BEGIN CERTIFICATE-----\nsynthetic-public\n-----END CERTIFICATE-----");
 memcpy(config,&legacy,sizeof legacy);config_size=sizeof legacy;
 pocket_wifi_store store;pocket_wifi_book b;CHECK(pocket_wifi_store_load(&store,&b)==POCKET_WIFI_STORE_ABSENT&&!writes,"absent profiles wrote storage");
 pocket_wifi_profile imported;CHECK(pocket_pairing_legacy_wifi(&imported),"legacy network not imported");CHECK(!strcmp(imported.ssid,"Synthetic Home")&&!strcmp(imported.password,"synthetic-password"),"legacy network changed");
 pocket_wifi_book_init(&b);CHECK(pocket_wifi_book_import(&b,&imported),"legacy import failed");
 CHECK(pocket_pairing_update("","","","")==ESP_OK&&!writes&&!memcmp(config,&legacy,sizeof legacy),"blank fields cleared pairing");
 CHECK(pocket_pairing_update("https://new.invalid","","","")==ESP_OK&&pair_writes==1,"explicit pairing update failed");
 pocket_pairing_config after;CHECK(pocket_pairing_load(&after)==ESP_OK&&!strcmp(after.windows,"https://new.invalid")&&!strcmp(after.linux_url,legacy.linux_url)&&!strcmp(after.token,legacy.token)&&!strcmp(after.ca,legacy.ca),"pairing update lost unchanged values");
 CHECK(!strcmp(after.ssid,legacy.ssid)&&!strcmp(after.pass,legacy.pass),"pairing update changed legacy Wi-Fi");
 unsigned pair_before=pair_writes;unsigned char pair_snapshot[3000];memcpy(pair_snapshot,config,config_size);
 b.sequence=1;CHECK(pocket_wifi_store_save(&store,&b)==ESP_OK,"first profile commit failed");
 CHECK(pair_writes==pair_before&&!memcmp(pair_snapshot,config,config_size),"profile save wrote pairing namespace");
 pocket_wifi_profile second={"Synthetic Office","synthetic-office"};CHECK(pocket_wifi_book_put(&b,&second)==1,"second profile failed");b.sequence=2;CHECK(pocket_wifi_store_save(&store,&b)==ESP_OK,"second profile commit failed");
 pocket_wifi_store loaded;pocket_wifi_book restored;CHECK(pocket_wifi_store_load(&loaded,&restored)==POCKET_WIFI_STORE_OK&&restored.count==2&&restored.sequence==2,"latest profile book not restored");
 unsigned char kept_slots[2][1024];size_t kept_sizes[2];memcpy(kept_slots,slots,sizeof slots);memcpy(kept_sizes,sizes,sizeof sizes);
 pocket_wifi_book retired=b;retired.count=1;retired.last=0;retired.sequence=3;memset(&retired.profiles[1],0,sizeof retired.profiles[1]);
 CHECK(pocket_wifi_store_save(&loaded,&retired)==ESP_OK,"explicit deletion did not save");
 sizes[0]=7;CHECK(pocket_wifi_store_load(&loaded,&restored)==POCKET_WIFI_STORE_OK&&restored.count==1&&pocket_wifi_book_find(&restored,"Synthetic Office")==-1,"recovery resurrected explicitly forgotten network");
 memcpy(slots,kept_slots,sizeof slots);memcpy(sizes,kept_sizes,sizeof sizes);
 /* Corrupt newest slot: the previous complete book must remain readable. */
 sizes[1]=7;CHECK(pocket_wifi_store_load(&loaded,&restored)==POCKET_WIFI_STORE_OK&&restored.count==1&&restored.sequence==1,"corrupt newest record lost last valid book");
 set_fail=true;b.sequence=3;CHECK(pocket_wifi_store_save(&loaded,&b)!=ESP_OK,"failed write reported success");set_fail=false;
 CHECK(pocket_wifi_store_load(&loaded,&restored)==POCKET_WIFI_STORE_OK&&restored.count==1,"failed write damaged prior book");
 sizes[0]=4;CHECK(pocket_wifi_store_load(&loaded,&restored)==POCKET_WIFI_STORE_CORRUPT&&!loaded.writable,"corrupt store silently treated as empty");
 unsigned before=writes;CHECK(pocket_wifi_store_save(&loaded,&b)!=ESP_OK&&writes==before,"corrupt book was silently overwritten");
 /* An authoritative empty book prevents legacy re-import on later boots. */
 memset(slots,0,sizeof slots);sizes[0]=sizes[1]=0;CHECK(pocket_wifi_store_load(&loaded,&restored)==POCKET_WIFI_STORE_ABSENT,"clear fixture failed");
 pocket_wifi_book_init(&b);b.sequence=1;CHECK(pocket_wifi_store_save(&loaded,&b)==ESP_OK,"empty book save failed");CHECK(pocket_wifi_store_load(&loaded,&restored)==POCKET_WIFI_STORE_OK&&!restored.count,"explicitly forgotten networks became absent store");
 /* Exact v1 layout migration remains read-only. */
 struct old_config{uint32_t version;char ssid[32],pass[64],windows[193],linux_url[193];} old={.version=1};snprintf(old.ssid,sizeof old.ssid,"Synthetic Legacy");snprintf(old.pass,sizeof old.pass,"synthetic-legacy");memcpy(config,&old,sizeof old);config_size=sizeof old;before=writes;
 CHECK(pocket_pairing_legacy_wifi(&imported)&&!strcmp(imported.ssid,"Synthetic Legacy")&&writes==before,"v1 migration changed storage");
 CHECK(pocket_pairing_update("","http://insecure.invalid","","")!=ESP_OK&&writes==before,"invalid pairing update wrote data");
 open_fail=true;CHECK(pocket_pairing_load(&after)!=ESP_OK,"NVS read error hidden");open_fail=false;
 puts("PASS Wi-Fi storage: isolated keys, legacy v1/v2 import, pairing preservation, dual-record recovery and fail-closed capacity");return 0;
}
'''


class FirmwareWifiTests(unittest.TestCase):
    def test_profile_store_and_pairing_preservation(self):
        self.run_harness(STORAGE_HARNESS, ['core/pocket_wifi.c', 'main/pocket_wifi_store.c', 'main/pocket_pairing.c'], NVS_HEADERS)

    def test_bounded_profiles_and_connection_engine(self):
        self.run_harness(CORE_HARNESS, ['core/pocket_wifi.c'])

    def run_harness(self, harness, sources, headers=None):
        compiler = os.environ.get('POCKET_HOST_CC') or shutil.which('clang') or shutil.which('gcc')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        firmware = pathlib.Path(__file__).resolve().parents[1] / 'firmware'
        with tempfile.TemporaryDirectory() as folder:
            temp = pathlib.Path(folder)
            fixture = temp / 'fixture.c'
            fixture.write_text(harness, encoding='utf-8')
            for name, content in (headers or {}).items():
                (temp/name).write_text(content, encoding='utf-8')
            binary = temp / ('wifi.exe' if os.name == 'nt' else 'wifi')
            built = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                                    '-I'+str(temp), '-I'+str(firmware/'core'), '-I'+str(firmware/'main'),
                                    *[str(firmware/name) for name in sources], str(fixture), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn('PASS Wi-Fi', result.stdout)


if __name__ == '__main__':
    unittest.main()
