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
void pocket_voice_begin_model(unsigned target,unsigned choice);
void pocket_voice_release(void);
void pocket_voice_cancel(void);
/* State request only; the network worker applies the clamped value to the BSP. */
void pocket_voice_set_volume(unsigned volume);
void pocket_voice_read(pocket_voice_state *out);

#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#endif
#if CONFIG_POCKET_OFFLINE_DIAGNOSTIC
#include "pocket_offline_diagnostic.h"
bool pocket_voice_diagnostic_arm(uint64_t now_ms,uint32_t nonce);
void pocket_voice_diagnostic_button(bool is_ok,bool press,uint64_t event_ms,uint32_t nonce);
bool pocket_voice_diagnostic_begin(uint64_t event_ms,uint32_t nonce);
void pocket_voice_diagnostic_input_lost(void);
void pocket_voice_diagnostic_read(pocket_offline_diagnostic_snapshot *out);
bool pocket_voice_diagnostic_active(void);
#endif

#if CONFIG_POCKET_LIVE_APP_CALLER
#include <stdint.h>
bool pocket_voice_live_begin(unsigned target,uint64_t event_ms,uint32_t generation);
uint32_t pocket_voice_live_button(bool press);
void pocket_voice_live_input_lost(void);
bool pocket_voice_live_active(void);
bool pocket_voice_live_cleanup_pending(void);
#endif

#if CONFIG_POCKET_QUALIFICATION
#include <stdint.h>
#include <stddef.h>
void pocket_voice_qualification_poll(void);
uint32_t pocket_voice_qualification_button(bool is_ok,bool press,bool release,uint64_t *event_ms);
bool pocket_voice_qualification_arm(uint64_t event_ms,uint32_t generation);
bool pocket_voice_qualification_begin(uint64_t event_ms,uint32_t generation);
void pocket_voice_qualification_input_lost(void);
bool pocket_voice_qualification_active(void);
bool pocket_voice_qualification_locked(void);
void pocket_voice_qualification_format(char *title,size_t title_size,char *detail,size_t detail_size,char *hint,size_t hint_size);
#endif
