/* MIT. Wi-Fi records never share the legacy pairing namespace. */
#pragma once
#include "pocket_wifi.h"
#include "esp_err.h"
typedef enum { POCKET_WIFI_STORE_ABSENT,POCKET_WIFI_STORE_OK,POCKET_WIFI_STORE_CORRUPT,POCKET_WIFI_STORE_ERROR } pocket_wifi_store_result;
typedef struct { int slot;bool writable; } pocket_wifi_store;
pocket_wifi_store_result pocket_wifi_store_load(pocket_wifi_store *,pocket_wifi_book *);
esp_err_t pocket_wifi_store_save(pocket_wifi_store *,const pocket_wifi_book *);
