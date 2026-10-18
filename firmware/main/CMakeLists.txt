idf_component_register(SRCS "main.c" "pocket_ui.c" "pocket_setup.c" "pocket_wifi_store.c" "pocket_pairing.c" "../core/pocket_wifi.c" "../core/pocket_clock.c" "pocket_http.c" "pocket_http_timing.c" "pocket_voice.c" "pocket_speech_client.c" "pocket_usage_client.c" "pocket_preferences.c" "pocket_tasks_client.c" "pocket_models_client.c" "pocket_actions.c" "../core/pocket_model.c" "../core/pocket_usage.c" "../core/pocket_diagnostics.c" "../core/pocket_reminders.c" "../fonts/lv_font_pocket_cjk_16.c"
    INCLUDE_DIRS "." "../core" "../fonts" REQUIRES bsp esp_timer esp_wifi esp_event esp_netif esp_http_server nvs_flash json esp_http_client mbedtls lwip PRIV_REQUIRES tcp_transport)

# This application only: transparent numeric I/O timing, no SDK edits.
target_link_options(${COMPONENT_LIB} INTERFACE
    "LINKER:--wrap=esp_transport_write"
    "LINKER:--wrap=esp_transport_read"
    "LINKER:--wrap=esp_event_post")
