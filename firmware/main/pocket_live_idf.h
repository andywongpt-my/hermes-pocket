/* MIT. Experimental transport/audio adapter. Real-device entry remains refused. */
#pragma once
#include "esp_err.h"
#include "pocket_live_worker.h"
typedef struct {
    const char *host, *token, *ca_pem, *chat_id;
    unsigned port, target;
    uint32_t epoch;
    bool (*stopped)(void *);
    void *stop_context;
} pocket_live_idf_config;
/* Always ESP_ERR_NOT_SUPPORTED in this checkpoint, including an opt-in build.
 * There is no owner-configurable bypass. A verified capture/reference timeline
 * transport is absent; compiling this adapter does not establish usable duplex. */
esp_err_t pocket_live_device_run(const pocket_live_idf_config *,
    pocket_live_worker *, pocket_live_worker_result *);
