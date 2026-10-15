idf_component_register(SRCS "main.c" "pocket_setup.c" "pocket_http.c" "pocket_voice.c" "../core/pocket_model.c"
    INCLUDE_DIRS "." "../core" REQUIRES bsp esp_timer esp_wifi esp_event esp_netif esp_http_server nvs_flash json esp_http_client mbedtls lwip)
