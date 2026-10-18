/* MIT. Reversible screen standby; active work retains the existing cadence. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
enum { POCKET_AUTO_SCREEN_OFF_MS=90000,POCKET_IDLE_POLL_MS=5000,POCKET_SCREEN_OFF_POLL_MS=15000 };
static inline bool pocket_power_screen_due(int64_t now_ms,int64_t last_activity_ms,bool blocked){
 return !blocked && now_ms>=last_activity_ms && now_ms-last_activity_ms>=POCKET_AUTO_SCREEN_OFF_MS;
}
static inline unsigned pocket_power_poll_ms(bool screen_off,bool active){
 return screen_off&&!active?POCKET_SCREEN_OFF_POLL_MS:POCKET_IDLE_POLL_MS;
}
