"""Offline native checks for reversible display standby, using real BSP bodies."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BSP = ROOT / "firmware/components/bsp/src"


def production_function(filename, name):
    source = (BSP / filename).read_text(encoding="utf-8")
    declaration = re.search(r"\besp_err_t\s+" + re.escape(name) + r"\s*\([^;{}]*\)\s*\{", source)
    if declaration is None:
        raise AssertionError(f"Missing reversible standby interface {name} in {filename}")
    # Match C braces without treating comments or quoted text as executable C.
    tokens = re.finditer(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]',
                         source[declaration.start():])
    depth = 0
    for token in tokens:
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return source[declaration.start():declaration.start() + token.end()]
    raise AssertionError(f"Unterminated production function {name} in {filename}")


PRELUDE = r'''
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int esp_err_t;
typedef void *TaskHandle_t;
typedef void *esp_lcd_panel_handle_t;
typedef void *esp_lcd_panel_io_handle_t;
typedef struct { int identity; } lv_display_t;
typedef struct { int identity; } lv_obj_t;
#define ESP_OK 0
#define ESP_FAIL (-1)
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NOT_FOUND 0x105
#define ESP_ERR_TIMEOUT 0x107
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
#define ESP_RETURN_ON_ERROR(call, ...) do { esp_err_t e_ = (call); if (e_ != ESP_OK) return e_; } while (0)
#define ESP_RETURN_ON_FALSE(condition, error, ...) do { if (!(condition)) return (error); } while (0)

static const char *scenario;
#define assert(condition) do { if (!(condition)) { \
    fprintf(stderr, "%s: assertion at line %d: %s\n", scenario, __LINE__, #condition); exit(77); \
} } while (0)

/* Only hardware/SDK boundaries are replaced. Production transition logic is
   inserted verbatim below; the fixture never reimplements these three APIs. */
bool s_ready, s_port_initialized, s_task_seen_render, s_task_suspended;
esp_lcd_panel_handle_t s_panel;
esp_lcd_panel_io_handle_t s_io;
lv_display_t *s_disp;
TaskHandle_t s_lvgl_task;
const char *TAG = "offline-display-power";

esp_err_t esp_lcd_panel_disp_on_off(esp_lcd_panel_handle_t panel, bool on);
esp_err_t esp_lcd_panel_disp_sleep(esp_lcd_panel_handle_t panel, bool asleep);
esp_err_t lvgl_port_stop(void);
esp_err_t lvgl_port_resume(void);
TaskHandle_t xTaskGetCurrentTaskHandle(void);
void vTaskSuspend(TaskHandle_t task);
void vTaskResume(TaskHandle_t task);
lv_obj_t *lv_screen_active(void);
void lv_obj_invalidate(lv_obj_t *object);
void lv_refr_now(lv_display_t *display);
esp_lcd_panel_io_handle_t bsp_display_io(void);
esp_err_t esp_lcd_panel_io_tx_param(esp_lcd_panel_io_handle_t io, int command,
                                  const void *parameter, size_t size);
'''


FIXTURE = r'''
enum { OFF_ERROR = 0x7101, SLEEP_ERROR, WAKE_ERROR, ON_ERROR,
       STOP_ERROR, RESUME_ERROR, DRAIN_ERROR };
static int panel_identity, io_identity, lvgl_task_identity, app_task_identity;
static lv_display_t display;
static lv_obj_t screen;
static TaskHandle_t caller;
static bool mutex_held, task_physically_suspended, timer_handling_enabled, tick_running;
static bool panel_asleep, panel_enabled, dma_pending;
static esp_err_t off_result, sleep_result, wake_result, on_result;
static esp_err_t stop_result, resume_result, drain_result;
static char trace[2048];

static void record(const char *event) {
    size_t used = strlen(trace), added = strlen(event);
    assert(used + added + 2 < sizeof trace);
    memcpy(trace + used, event, added);
    trace[used + added] = '|'; trace[used + added + 1] = '\0';
}
static void expect_trace(const char *expected) {
    if (strcmp(trace, expected)) {
        fprintf(stderr, "%s: wanted trace <%s>, got <%s>\n", scenario, expected, trace);
        exit(78);
    }
}
static void reset_fixture(void) {
    s_ready = s_port_initialized = s_task_seen_render = true;
    s_task_suspended = task_physically_suspended = false;
    s_panel = &panel_identity; s_io = &io_identity; s_disp = &display;
    s_lvgl_task = &lvgl_task_identity; caller = &app_task_identity;
    mutex_held = timer_handling_enabled = tick_running = panel_enabled = true;
    panel_asleep = dma_pending = false;
    off_result = sleep_result = wake_result = on_result = ESP_OK;
    stop_result = resume_result = drain_result = ESP_OK;
    trace[0] = '\0';
}

esp_err_t esp_lcd_panel_disp_on_off(esp_lcd_panel_handle_t panel, bool on) {
    assert(mutex_held && panel == &panel_identity);
    record(on ? "on" : "off");
    esp_err_t result = on ? on_result : off_result;
    if (result == ESP_OK) panel_enabled = on;
    return result;
}
esp_err_t esp_lcd_panel_disp_sleep(esp_lcd_panel_handle_t panel, bool asleep) {
    assert(mutex_held && panel == &panel_identity);
    record(asleep ? "sleep_in" : "sleep_out");
    esp_err_t result = asleep ? sleep_result : wake_result;
    if (result == ESP_OK) panel_asleep = asleep;
    return result;
}
esp_err_t lvgl_port_stop(void) {
    assert(mutex_held); record("stop");
    /* Port 2.9.0 disables timer handling even if esp_timer_stop fails. */
    timer_handling_enabled = false;
    if (stop_result == ESP_OK) tick_running = false;
    return stop_result;
}
esp_err_t lvgl_port_resume(void) {
    assert(mutex_held); record("port_resume");
    /* Port 2.9.0 enables handling before trying to start the tick timer. */
    timer_handling_enabled = true;
    if (resume_result == ESP_OK) tick_running = true;
    return resume_result;
}
TaskHandle_t xTaskGetCurrentTaskHandle(void) { return caller; }
void vTaskSuspend(TaskHandle_t task) {
    /* NULL would suspend the caller on real FreeRTOS. Never accept it here. */
    assert(mutex_held && task && task == &lvgl_task_identity && task != caller);
    assert(!task_physically_suspended && !timer_handling_enabled && !tick_running);
    record("suspend"); task_physically_suspended = true;
}
void vTaskResume(TaskHandle_t task) {
    assert(mutex_held && task == &lvgl_task_identity && task != caller);
    assert(task_physically_suspended && timer_handling_enabled && tick_running);
    record("task_resume"); task_physically_suspended = false;
}
lv_obj_t *lv_screen_active(void) { assert(mutex_held); return &screen; }
void lv_obj_invalidate(lv_obj_t *object) {
    assert(mutex_held && object == &screen); record("invalidate");
}
void lv_refr_now(lv_display_t *value) {
    assert(mutex_held && value == &display); record("refresh");
    /* A real final SPI flush remains in flight after lv_refr_now returns. */
    dma_pending = true;
}
esp_lcd_panel_io_handle_t bsp_display_io(void) { return s_io; }
esp_err_t esp_lcd_panel_io_tx_param(esp_lcd_panel_io_handle_t io, int command,
                                  const void *parameter, size_t size) {
    assert(mutex_held && io == &io_identity);
    assert(command == -1 && parameter == NULL && size == 0 && dma_pending);
    record("drain");
    if (drain_result == ESP_OK) dma_pending = false;
    return drain_result;
}

int main(int argc, char **argv) {
    assert(argc == 2); scenario = argv[1]; reset_fixture();
    if (!strcmp(scenario, "panel_not_ready")) {
        s_ready = false;
        assert(bsp_display_set_sleep(true) == ESP_ERR_INVALID_STATE);
        assert(bsp_display_set_sleep(false) == ESP_ERR_INVALID_STATE);
        s_ready = true; s_panel = NULL;
        assert(bsp_display_set_sleep(true) == ESP_ERR_INVALID_STATE);
        assert(bsp_display_set_sleep(false) == ESP_ERR_INVALID_STATE);
        expect_trace("");
    } else if (!strcmp(scenario, "panel_cycle")) {
        assert(bsp_display_set_sleep(true) == ESP_OK && panel_asleep && !panel_enabled);
        expect_trace("off|sleep_in|");
        assert(bsp_display_set_sleep(false) == ESP_OK && !panel_asleep && panel_enabled);
        expect_trace("off|sleep_in|sleep_out|on|");
    } else if (!strcmp(scenario, "panel_sleep_errors")) {
        off_result = OFF_ERROR;
        assert(bsp_display_set_sleep(true) == OFF_ERROR);
        expect_trace("off|");
        reset_fixture(); sleep_result = SLEEP_ERROR;
        assert(bsp_display_set_sleep(true) == SLEEP_ERROR);
        expect_trace("off|sleep_in|");
        reset_fixture(); off_result = OFF_ERROR; sleep_result = SLEEP_ERROR;
        assert(bsp_display_set_sleep(true) == OFF_ERROR);
        expect_trace("off|");
    } else if (!strcmp(scenario, "panel_wake_errors")) {
        wake_result = WAKE_ERROR;
        assert(bsp_display_set_sleep(false) == WAKE_ERROR);
        expect_trace("sleep_out|");
        reset_fixture(); on_result = ON_ERROR;
        assert(bsp_display_set_sleep(false) == ON_ERROR);
        expect_trace("sleep_out|on|");
        reset_fixture(); wake_result = WAKE_ERROR; on_result = ON_ERROR;
        assert(bsp_display_set_sleep(false) == WAKE_ERROR);
        expect_trace("sleep_out|");
    } else if (!strcmp(scenario, "task_no_display")) {
        s_disp = NULL;
        assert(bsp_lvgl_set_sleep(true) == ESP_ERR_INVALID_STATE);
        assert(!s_task_suspended && !task_physically_suspended);
        expect_trace("");
    } else if (!strcmp(scenario, "task_null_handle")) {
        s_lvgl_task = NULL;
        assert(bsp_lvgl_set_sleep(true) == ESP_ERR_INVALID_STATE);
        assert(!s_task_suspended && !task_physically_suspended);
        expect_trace("");
    } else if (!strcmp(scenario, "task_self_handle")) {
        caller = s_lvgl_task;
        assert(bsp_lvgl_set_sleep(true) == ESP_ERR_INVALID_STATE);
        assert(!s_task_suspended && !task_physically_suspended);
        expect_trace("");
    } else if (!strcmp(scenario, "task_before_first_render")) {
        s_task_seen_render = false;
        assert(bsp_lvgl_set_sleep(true) == ESP_ERR_INVALID_STATE);
        assert(!s_task_suspended && !task_physically_suspended);
        expect_trace("");
    } else if (!strcmp(scenario, "task_unsuspended_wake_fallback")) {
        s_lvgl_task = NULL;
        assert(bsp_lvgl_set_sleep(false) == ESP_OK);
        s_lvgl_task = caller;
        assert(bsp_lvgl_set_sleep(false) == ESP_OK);
        s_lvgl_task = &lvgl_task_identity; s_task_seen_render = false;
        assert(bsp_lvgl_set_sleep(false) == ESP_OK);
        assert(!s_task_suspended && !task_physically_suspended);
        expect_trace("");
    } else if (!strcmp(scenario, "task_cycle")) {
        assert(bsp_lvgl_set_sleep(true) == ESP_OK);
        assert(s_task_suspended && task_physically_suspended && !tick_running);
        expect_trace("stop|suspend|");
        assert(bsp_lvgl_set_sleep(true) == ESP_OK);
        expect_trace("stop|suspend|");
        assert(bsp_lvgl_set_sleep(false) == ESP_OK);
        assert(!s_task_suspended && !task_physically_suspended && tick_running);
        expect_trace("stop|suspend|port_resume|task_resume|");
        assert(bsp_lvgl_set_sleep(false) == ESP_OK);
        expect_trace("stop|suspend|port_resume|task_resume|");
    } else if (!strcmp(scenario, "task_stop_failure")) {
        stop_result = STOP_ERROR;
        assert(bsp_lvgl_set_sleep(true) == STOP_ERROR);
        assert(!s_task_suspended && !task_physically_suspended);
        assert(timer_handling_enabled && tick_running);
        expect_trace("stop|port_resume|");
        stop_result = ESP_OK;
        assert(bsp_lvgl_set_sleep(true) == ESP_OK);
        assert(s_task_suspended && task_physically_suspended);
        expect_trace("stop|port_resume|stop|suspend|");
    } else if (!strcmp(scenario, "task_stop_restore_failure")) {
        stop_result = STOP_ERROR; resume_result = RESUME_ERROR;
        assert(bsp_lvgl_set_sleep(true) == STOP_ERROR);
        assert(!s_task_suspended && !task_physically_suspended);
        expect_trace("stop|port_resume|");
        stop_result = resume_result = ESP_OK;
        assert(bsp_lvgl_set_sleep(true) == ESP_OK);
        assert(s_task_suspended && task_physically_suspended);
    } else if (!strcmp(scenario, "task_resume_failure") ||
               !strcmp(scenario, "task_resume_cleanup_failure")) {
        assert(bsp_lvgl_set_sleep(true) == ESP_OK);
        trace[0] = '\0'; resume_result = RESUME_ERROR;
        if (!strcmp(scenario, "task_resume_cleanup_failure")) stop_result = STOP_ERROR;
        assert(bsp_lvgl_set_sleep(false) == RESUME_ERROR);
        assert(s_task_suspended && task_physically_suspended);
        assert(!timer_handling_enabled && !tick_running);
        expect_trace("port_resume|stop|");
        /* A duplicate standby call cannot add an unmatched suspend. */
        assert(bsp_lvgl_set_sleep(true) == ESP_OK);
        expect_trace("port_resume|stop|");
        resume_result = stop_result = ESP_OK;
        assert(bsp_lvgl_set_sleep(false) == ESP_OK);
        assert(!s_task_suspended && !task_physically_suspended);
        assert(timer_handling_enabled && tick_running);
        expect_trace("port_resume|stop|port_resume|task_resume|");
    } else if (!strcmp(scenario, "task_repeated_cycles")) {
        for (unsigned i = 0; i < 64; ++i) {
            trace[0] = '\0';
            assert(bsp_lvgl_set_sleep(true) == ESP_OK);
            assert(bsp_lvgl_set_sleep(false) == ESP_OK);
            assert(!s_task_suspended && !task_physically_suspended);
            assert(timer_handling_enabled && tick_running);
            expect_trace("stop|suspend|port_resume|task_resume|");
        }
    } else if (!strcmp(scenario, "repaint")) {
        assert(bsp_lvgl_repaint() == ESP_OK && !dma_pending);
        expect_trace("invalidate|refresh|drain|");
    } else if (!strcmp(scenario, "repaint_drain_failure")) {
        drain_result = DRAIN_ERROR;
        assert(bsp_lvgl_repaint() == DRAIN_ERROR && dma_pending);
        expect_trace("invalidate|refresh|drain|");
    } else if (!strcmp(scenario, "repaint_not_ready")) {
        s_disp = NULL;
        assert(bsp_lvgl_repaint() == ESP_ERR_INVALID_STATE);
        expect_trace("");
    } else if (!strcmp(scenario, "repaint_while_suspended")) {
        assert(bsp_lvgl_set_sleep(true) == ESP_OK);
        trace[0] = '\0';
        assert(bsp_lvgl_repaint() == ESP_ERR_INVALID_STATE);
        expect_trace("");
    } else {
        fprintf(stderr, "Unknown fixture scenario: %s\n", scenario); return 2;
    }
    printf("PASS display power: %s\n", scenario); return 0;
}
'''


class FirmwareDisplayPowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("clang") or shutil.which("gcc")
        cached = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LLVM/bin/clang.exe"
        if not cls.compiler and cached.is_file():
            cls.compiler = str(cached)
        if not cls.compiler:
            raise unittest.SkipTest("owner-installed native C compiler unavailable")
        cls.temporary = tempfile.TemporaryDirectory(prefix="display-power-", dir=ROOT.parent)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.executable = cls.directory / ("display_power.exe" if os.name == "nt" else "display_power")
        cls.compiled = False

    def binary(self):
        if not self.__class__.compiled:
            bodies = [production_function(filename, name) for filename, name in (
                ("bsp_display.c", "bsp_display_set_sleep"),
                ("bsp_display_lvgl.c", "bsp_lvgl_set_sleep"),
                ("bsp_display_lvgl.c", "bsp_lvgl_repaint"),
            )]
            source = self.directory / "display_power.c"
            source.write_text(PRELUDE + "\n\n".join(bodies) + FIXTURE, encoding="utf-8")
            environment = os.environ.copy()
            environment.update(TMP=str(self.directory), TEMP=str(self.directory), TMPDIR=str(self.directory))
            built = subprocess.run([
                self.compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                str(source), "-o", str(self.executable),
            ], capture_output=True, text=True, timeout=60, env=environment, check=False)
            self.assertEqual(built.returncode, 0, "Actual BSP power bodies must compile:\n" + built.stdout + built.stderr)
            self.__class__.compiled = True
        return str(self.executable)

    def exercise(self, *scenarios):
        executable = self.binary()
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                run = subprocess.run([executable, scenario], capture_output=True, text=True,
                                     timeout=10, check=False)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("PASS display power: " + scenario, run.stdout)

    def test_panel_rejects_uninitialized_or_missing_handle(self):
        self.exercise("panel_not_ready")

    def test_panel_commands_put_sleep_out_before_display_on(self):
        self.exercise("panel_cycle")

    def test_panel_returns_sleep_error_without_issuing_later_commands(self):
        self.exercise("panel_sleep_errors")

    def test_panel_never_turns_on_after_failed_sleep_out(self):
        self.exercise("panel_wake_errors")

    def test_task_rejects_missing_display_null_handle_and_self(self):
        self.exercise("task_no_display", "task_null_handle", "task_self_handle")

    def test_task_rejects_suspension_before_real_task_render(self):
        self.exercise("task_before_first_render")

    def test_unsuspended_wake_allows_backlight_only_fallback(self):
        self.exercise("task_unsuspended_wake_fallback")

    def test_task_suspend_resume_are_ordered_and_idempotent(self):
        self.exercise("task_cycle", "task_repeated_cycles")

    def test_failed_timer_stop_restores_handler_without_suspending_task(self):
        self.exercise("task_stop_failure", "task_stop_restore_failure")

    def test_failed_timer_resume_leaves_task_paused_for_retry(self):
        self.exercise("task_resume_failure", "task_resume_cleanup_failure")

    def test_repaint_drains_final_asynchronous_flush(self):
        self.exercise("repaint")

    def test_repaint_propagates_drain_error(self):
        self.exercise("repaint_drain_failure")

    def test_repaint_rejects_missing_or_still_suspended_display_without_drawing(self):
        self.exercise("repaint_not_ready", "repaint_while_suspended")


if __name__ == "__main__":
    unittest.main()
