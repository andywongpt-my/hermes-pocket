/* MIT. The original v2 binary record is kept compatible and private. */
#pragma once
#include <stdint.h>
#include "pocket_wifi.h"
#include "esp_err.h"
typedef struct {
 uint32_t version;char ssid[32],pass[64],windows[193],linux_url[193],token[129],ca[2049];
} pocket_pairing_config;
esp_err_t pocket_pairing_load(pocket_pairing_config *);
bool pocket_pairing_legacy_wifi(pocket_wifi_profile *);
/* Empty fields preserve stored values. No implicit unpair action. */
esp_err_t pocket_pairing_update(const char *,const char *,const char *,const char *);
