# Qualification owns a single USB driver only in this explicit opt-in build.
set(pocket_qualification_requires)
if(CONFIG_POCKET_QUALIFICATION)
    if(CONFIG_POCKET_OFFLINE_DIAGNOSTIC)
        message(FATAL_ERROR "Qualification and silent diagnostic gestures are mutually exclusive")
    endif()
    list(APPEND pocket_qualification_requires esp_driver_usb_serial_jtag)
endif()
idf_component_register(SRCS "main.c" "pocket_ui.c" "pocket_setup.c" "pocket_linux_link.c" "pocket_wifi_store.c" "pocket_pairing.c" "../core/pocket_wifi.c" "../core/pocket_clock.c" "pocket_http.c" "pocket_http_timing.c" "pocket_voice.c" "pocket_speech_client.c" "pocket_usage_client.c" "pocket_preferences.c" "pocket_tasks_client.c" "pocket_models_client.c" "pocket_actions.c" "../core/pocket_model.c" "../core/pocket_usage.c" "../core/pocket_diagnostics.c" "../core/pocket_reminders.c" "../fonts/lv_font_pocket_cjk_16.c"
    INCLUDE_DIRS "." "../core" "../fonts" REQUIRES bsp esp_timer esp_wifi esp_event esp_netif esp_http_server nvs_flash json esp_http_client mbedtls lwip PRIV_REQUIRES tcp_transport ${pocket_qualification_requires})

# Explicit deployment input only; no target is embedded by ordinary builds.
if(DEFINED ENV{POCKET_LINUX_PUBLIC_ORIGIN} AND NOT "$ENV{POCKET_LINUX_PUBLIC_ORIGIN}" STREQUAL "")
    if(NOT "$ENV{POCKET_LINUX_PUBLIC_ORIGIN}" MATCHES "^https://[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?$")
        message(FATAL_ERROR "Invalid Linux public endpoint build input")
    endif()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE POCKET_LINUX_PUBLIC_ORIGIN="$ENV{POCKET_LINUX_PUBLIC_ORIGIN}")
endif()

# This application only: transparent numeric I/O timing, no SDK edits.
target_link_options(${COMPONENT_LIB} INTERFACE
    "LINKER:--wrap=esp_transport_write"
    "LINKER:--wrap=esp_transport_read"
    "LINKER:--wrap=esp_event_post")

# Source verification only: the gate is hard-disabled pending synchronized AEC.
if(CONFIG_POCKET_LIVE_WORKER_COMPILE)
    target_sources(${COMPONENT_LIB} PRIVATE
        "pocket_live_client.c" "pocket_live_worker.c" "pocket_live_control.c"
        "pocket_live_gate.c" "pocket_live_idf.c" "../core/pocket_live_stream.c")
    # IDF 5.5.3 transport_ws logs authenticated Upgrade bytes on write failure.
    # Compile out its component logs; runtime tag settings can be changed later.
    idf_component_get_property(pocket_transport_lib tcp_transport COMPONENT_LIB)
    target_compile_definitions(${pocket_transport_lib} PRIVATE LOG_LOCAL_LEVEL=ESP_LOG_NONE)
endif()

if(CONFIG_POCKET_LIVE_PAIRED_COMPILE)
    target_sources(${COMPONENT_LIB} PRIVATE
        "../core/pocket_live_v2_stream.c" "pocket_live_v2_worker.c"
        "pocket_live_v2_control.c" "pocket_live_v2_audio.c"
        "pocket_live_v2_gate.c" "pocket_live_v2_idf.c"
        "pocket_live_session.c" "pocket_live_session_config.c"
        "pocket_live_network.c" "pocket_live_network_idf.c")
endif()

if(CONFIG_POCKET_OFFLINE_DIAGNOSTIC)
    target_sources(${COMPONENT_LIB} PRIVATE "../core/pocket_offline_diagnostic.c"
        "../core/pocket_offline_diagnostic_ui.c")
endif()

# Optional normal-app wiring is distinct from source-only worker compilation.
# The separate admission implementation still refuses before any side effect.
if(CONFIG_POCKET_LIVE_APP_CALLER)
    target_sources(${COMPONENT_LIB} PRIVATE
        "pocket_live_app.c" "pocket_live_admission.c")
endif()

if(CONFIG_POCKET_QUALIFICATION)
    target_sources(${COMPONENT_LIB} PRIVATE
        "../core/pocket_qualification.c" "../core/pocket_qualification_protocol.c"
        "../core/pocket_qualification_control.c" "pocket_qualification_usb.c"
        "pocket_qualification_transport.c")
endif()
