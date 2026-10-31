idf_component_register(SRCS "bench_main.c" "bench_core.c"
    INCLUDE_DIRS "."
    REQUIRES bsp esp_driver_usb_serial_jtag esp_timer)

# Offline compiler evidence only; not a substitute for stack high-water checks.
target_compile_options(${COMPONENT_LIB} PRIVATE -fstack-usage)
