/* MIT. Bounded UI-only NVS preference; no pairing/configuration migration. */
#include "pocket_preferences.h"
#include "nvs.h"
#include <stdint.h>

unsigned pocket_preferences_load_target(void){
 nvs_handle_t handle;uint8_t target=0;
 if(nvs_open("pocket_ui",NVS_READONLY,&handle)!=ESP_OK)return 0;
 esp_err_t error=nvs_get_u8(handle,"target",&target);nvs_close(handle);
 return error==ESP_OK && target<2?target:0;
}
esp_err_t pocket_preferences_save_target(unsigned target){
 if(target>1)return ESP_ERR_INVALID_ARG;
 nvs_handle_t handle;esp_err_t error=nvs_open("pocket_ui",NVS_READWRITE,&handle);
 if(error!=ESP_OK)return error;
 uint8_t previous=2;
 if(nvs_get_u8(handle,"target",&previous)==ESP_OK && previous==target){nvs_close(handle);return ESP_OK;}
 error=nvs_set_u8(handle,"target",(uint8_t)target);
 if(error==ESP_OK)error=nvs_commit(handle);
 nvs_close(handle);return error;
}
