/* Host UI snapshot config mirroring the LVGL choices in firmware/sdkconfig.defaults. */
#ifndef LV_CONF_H
#define LV_CONF_H
#define LV_COLOR_DEPTH 16
#define LV_USE_STDLIB_MALLOC LV_STDLIB_BUILTIN
#define LV_MEM_SIZE (24U * 1024U)
#define LV_DEF_REFR_PERIOD 20
#define LV_USE_OS LV_OS_NONE
#define LV_DRAW_SW_COMPLEX 1
#define LV_FONT_MONTSERRAT_14 1
#define LV_FONT_MONTSERRAT_20 1
#define LV_FONT_SOURCE_HAN_SANS_SC_16_CJK 1
#define LV_USE_LOG 1
#define LV_LOG_LEVEL LV_LOG_LEVEL_WARN
#define LV_LOG_PRINTF 1
#define LV_USE_ASSERT_MEM 1
#endif
