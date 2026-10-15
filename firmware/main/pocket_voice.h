#pragma once
#include <stdbool.h>
#include "esp_err.h"
#include "pocket_diagnostics.h"
typedef struct {
 bool busy,recording,playing;
 bool online[2],speech_ready[2];
 pocket_diagnostics diagnostics[2];
 unsigned target,volume;
 char message[128],text[512],reply[2048];
} pocket_voice_state;
esp_err_t pocket_voice_init(void);
void pocket_voice_begin(unsigned target);
void pocket_voice_release(void);
void pocket_voice_cancel(void);
/* State request only; the network worker applies the clamped value to the BSP. */
void pocket_voice_set_volume(unsigned volume);
void pocket_voice_read(pocket_voice_state *out);
