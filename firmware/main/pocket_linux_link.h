#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

/* Separate, non-credential Linux override. Never writes pocket/config.
 * A valid existing record is immutable; a later manual legacy retarget wins. */
esp_err_t pocket_linux_link_apply_once(const char *approved_origin,const char *legacy_origin);
bool pocket_linux_link_select(const char *approved_origin,const char *legacy_origin,
                              char *origin,size_t capacity);
