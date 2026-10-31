idf_component_register(
    SRCS "src/bsp_i2c.c"
         "src/bsp_display.c"
         "src/bsp_display_lvgl.c"
         "src/bsp_display_rounding.c"
         "src/bsp_button.c"
         "src/bsp_audio.c"
         "src/bsp_es8311_sleep_check.c"
         "src/bsp_battery.c"
    INCLUDE_DIRS "include"
    REQUIRES driver esp_driver_i2c esp_driver_i2s esp_lcd esp_lvgl_port button esp_adc esp_codec_dev
    PRIV_REQUIRES esp_timer
)

if(CONFIG_BSP_AUDIO_CALLBACK_RENDER)
    target_sources(${COMPONENT_LIB} PRIVATE "src/bsp_audio_render.c" "src/bsp_audio_capture.c")
endif()
