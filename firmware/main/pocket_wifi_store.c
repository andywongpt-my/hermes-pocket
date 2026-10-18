/* MIT. Alternate complete records, retaining the previous valid generation. */
#include "pocket_wifi_store.h"
#include "nvs.h"
#include <string.h>

static int read_slot(nvs_handle_t handle,const char *key,pocket_wifi_book *out){
 size_t size=0;esp_err_t error=nvs_get_blob(handle,key,NULL,&size);
 if(error==ESP_ERR_NVS_NOT_FOUND)return 0;
 if(error!=ESP_OK)return -1;
 if(size!=sizeof *out)return 1;
 error=nvs_get_blob(handle,key,out,&size);
 if(error!=ESP_OK)return -1;
 return size==sizeof *out && out->sequence && pocket_wifi_book_valid(out)?2:1;
}
pocket_wifi_store_result pocket_wifi_store_load(pocket_wifi_store *store,pocket_wifi_book *out){
 if(!store || !out)return POCKET_WIFI_STORE_ERROR;
 *store=(pocket_wifi_store){.slot=-1,.writable=false};pocket_wifi_book_init(out);
 nvs_handle_t handle;esp_err_t error=nvs_open("pocket_wifi",NVS_READONLY,&handle);
 if(error==ESP_ERR_NVS_NOT_FOUND){store->writable=true;return POCKET_WIFI_STORE_ABSENT;}
 if(error!=ESP_OK)return POCKET_WIFI_STORE_ERROR;
 pocket_wifi_book records[2]={{0}};
 int a=read_slot(handle,"a",&records[0]),b=read_slot(handle,"b",&records[1]);nvs_close(handle);
 pocket_wifi_store_result result=POCKET_WIFI_STORE_ERROR;
 if(a==2 || b==2){
  if(a==2 && b==2 && records[0].sequence==records[1].sequence && memcmp(&records[0],&records[1],sizeof records[0]))result=POCKET_WIFI_STORE_CORRUPT;
  else {
   store->slot=a==2 && (b!=2 || (int32_t)(records[0].sequence-records[1].sequence)>=0)?0:1;
   *out=records[store->slot];store->writable=a>=0 && b>=0;result=POCKET_WIFI_STORE_OK;
  }
 }else if(a>=0 && b>=0){
  result=(a || b)?POCKET_WIFI_STORE_CORRUPT:POCKET_WIFI_STORE_ABSENT;
  store->writable=result==POCKET_WIFI_STORE_ABSENT;
 }
 memset(records,0,sizeof records);return result;
}
static esp_err_t write_record(int slot,const pocket_wifi_book *book){
 const char *key=slot?"b":"a";
 nvs_handle_t handle;esp_err_t error=nvs_open("pocket_wifi",NVS_READWRITE,&handle);
 if(error!=ESP_OK)return error;
 error=nvs_set_blob(handle,key,book,sizeof *book);
 if(error==ESP_OK)error=nvs_commit(handle);
 nvs_close(handle);
 if(error!=ESP_OK)return error;
 error=nvs_open("pocket_wifi",NVS_READONLY,&handle);
 if(error!=ESP_OK)return error;
 pocket_wifi_book verified={0};int valid=read_slot(handle,key,&verified);nvs_close(handle);
 bool same=valid==2 && !memcmp(book,&verified,sizeof verified);memset(&verified,0,sizeof verified);
 if(!same)return ESP_FAIL;
 return ESP_OK;
}
esp_err_t pocket_wifi_store_save(pocket_wifi_store *store,const pocket_wifi_book *book){
 if(!store || !store->writable || !book || !book->sequence || !pocket_wifi_book_valid(book))return ESP_ERR_INVALID_STATE;
 bool retire=store->slot<0 || book->count==0;
 if(store->slot>=0){
  nvs_handle_t handle;esp_err_t error=nvs_open("pocket_wifi",NVS_READONLY,&handle);if(error!=ESP_OK)return error;
  pocket_wifi_book previous={0};int valid=read_slot(handle,store->slot?"b":"a",&previous);nvs_close(handle);
  if(valid==2)retire=retire || book->count<previous.count;
  memset(&previous,0,sizeof previous);if(valid!=2)return ESP_ERR_INVALID_STATE;
 }
 const int slot=store->slot==0?1:0;esp_err_t error=write_record(slot,book);
 if(error!=ESP_OK)return error;
 /* A confirmed forget must not be undone by recovery from the older copy.
    First-time import also gets two complete copies. No automatic deletion. */
 if(retire){error=write_record(1-slot,book);if(error!=ESP_OK){store->writable=false;return error;}}
 store->slot=slot;return ESP_OK;
}
