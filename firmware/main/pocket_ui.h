/* Hermes Pocket MIT. Screen layout and animated cartoon cat in pure LVGL.
 * No ESP-IDF calls here, so host snapshot tests render exactly this code. */
#pragma once
#include <stdbool.h>
#include "pocket_model.h"
#include "pocket_usage.h"

typedef struct {
 pocket_page page;
 unsigned target,usage_window;
 const pocket_usage_snapshot *usage;
 int64_t now;
 int battery_percent; /* Negative when the gauge has no trustworthy reading. */
 bool setup_active, wifi_connected, online, speech_ready;
 bool voice_here, recording, playing, busy; /* voice_here: voice state belongs to target. */
 const char *ssid, *password, *message, *text, *reply;
} pocket_ui_view;

/* All calls must hold the LVGL lock. */
void pocket_ui_create(void);
void pocket_ui_render(const pocket_ui_view *view);
/* Pause the cat's animation while the backlight is off. */
void pocket_ui_set_awake(bool awake);
