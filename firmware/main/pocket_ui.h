/* Hermes Pocket MIT. Screen layout and animated cartoon cat in pure LVGL.
 * No ESP-IDF calls here, so host snapshot tests render exactly this code. */
#pragma once
#include <stdbool.h>
#include "pocket_model.h"
#include "pocket_usage.h"
#include "pocket_diagnostics.h"
#include "pocket_offline_diagnostic.h"

typedef struct {
 pocket_page page;
 unsigned target,usage_window;
 const pocket_usage_snapshot *usage;
 int64_t now;
 int64_t uptime_ms;
 const pocket_diagnostics *diagnostics;
 const pocket_offline_diagnostic_snapshot *offline_diagnostic; /* NULL in default build. */
 bool target_save_failed,clock_ready;
 pocket_tool tool;
 unsigned volume,alert_threshold,task_count,task_selected;
 bool audio_edit,usd_floor_enabled,alert_pending,task_approval,task_unknown,task_has_result,task_truncated;
 bool reader_active,reader_has_page,reader_loading,reader_navigation,reader_has_prev,reader_has_next,reader_open_failed;
 unsigned reader_target,reader_generation,reader_page,reader_page_count,reader_utf8_bytes;
 const char *reader_status;
 unsigned model_choice,model_candidate,models_available_mask,models_verified_mask;
 bool model_apply_failed;
 const char *models_status;
 const char *task_phase,*task_status,*task_result,*alert_note;
 int battery_percent; /* Negative when the gauge has no trustworthy reading. */
 bool setup_active, wifi_connected, online, speech_ready;
 /* Qualification labels must be fixed controller text, never raw data/secrets. */
 bool qualification_mode,qualification_locked;
 const char *qualification_title,*qualification_detail,*qualification_hint;
 bool live_mode,live_available,live_cleanup_pending; /* False preserves legacy UI. */
 bool voice_here, recording, playing, busy; /* voice_here: voice state belongs to target. */
 const char *ssid, *password, *message, *text, *reply, *setup_message;
} pocket_ui_view;

/* All calls must hold the LVGL lock. */
void pocket_ui_create(void);
void pocket_ui_render(const pocket_ui_view *view);
/* Pause the cat's animation while the backlight is off. */
void pocket_ui_set_awake(bool awake);
/* True only when a server block is needed; otherwise changes the local screen. */
bool pocket_ui_reader_step(int direction);
