#include "pocket_live_idf.h"
esp_err_t pocket_live_device_run(const pocket_live_idf_config *config,
    pocket_live_worker *worker,pocket_live_worker_result *result) {
    (void)config;(void)worker;
    if(result)*result=(pocket_live_worker_result){.reason=PLW_REFERENCE_UNAVAILABLE,.cleanup_complete=true};
    return ESP_ERR_NOT_SUPPORTED;
}
