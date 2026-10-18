#pragma once
#include "esp_err.h"

/* Non-secret UI preference; pairing configuration is never opened here. */
unsigned pocket_preferences_load_target(void);
esp_err_t pocket_preferences_save_target(unsigned target);
