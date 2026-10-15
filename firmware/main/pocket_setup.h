#pragma once
#include <stdbool.h>
#include "esp_err.h"
typedef struct {
 bool active, wifi_connected, configured;
 char ssid[33], password[25], message[96];
} pocket_setup_state;
esp_err_t pocket_setup_init(void);
void pocket_setup_request(void);
void pocket_setup_read(pocket_setup_state *out);

/* Device-only connection settings. Never log or expose token contents. */
typedef struct { char origin[193], token[129], ca[2049]; } pocket_link_config;
bool pocket_setup_link(unsigned target,pocket_link_config *out);
