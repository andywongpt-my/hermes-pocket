"""Exercise the actual pure C Malaysia clock; no device, SDK, or network."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MIN_UTC = 1704067200  # 2024-01-01T00:00:00Z
MAX_UTC = 253402300799  # 9999-12-31T23:59:59Z
WAITING = "Waiting for time (MYT)"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

HARNESS = r'''
#if defined(_MSC_VER)
#define _CRT_SECURE_NO_WARNINGS
#endif
#include "pocket_clock.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#define CHECK(c, m) do { if (!(c)) { fprintf(stderr, "FAIL: %s\n", m); return 1; } } while (0)
#define MIN_UTC INT64_C(1704067200)
#define MAX_UTC INT64_C(253402300799)
static int latch(void) {
    char out[64];
    const int64_t invalid[] = {INT64_MIN, -1, 0, MIN_UTC - 1, MAX_UTC + 1, INT64_MAX};
    CHECK(!pocket_clock_ready(), "static boot readiness must be false");
    CHECK(pocket_clock_format(MIN_UTC, true, out, sizeof out), "explicit synchronized formatter must be independent of latch");
    CHECK(!pocket_clock_ready(), "formatting must not latch synchronization");
    pocket_clock_reset();
    CHECK(!pocket_clock_ready(), "boot must wait for sync");
    for (size_t i = 0; i < sizeof invalid / sizeof invalid[0]; ++i) {
        pocket_clock_note_sync(invalid[i]);
        CHECK(!pocket_clock_ready(), "invalid epoch must not mark the clock ready");
    }
    pocket_clock_note_sync(MIN_UTC);
    CHECK(pocket_clock_ready(), "valid minimum UTC sync must latch ready");
    CHECK(pocket_clock_format(MIN_UTC, pocket_clock_ready(), out, sizeof out), "valid synced clock must format");
    CHECK(!strcmp(out, "01 Jan 2024 08:00 MYT"), "fixed MYT offset at minimum");
    /* Offline ticking needs no further callback. Reconnection/invalid reports
       must not clear the successful boot-session sync latch. */
    CHECK(pocket_clock_format(MIN_UTC + 65, pocket_clock_ready(), out, sizeof out), "offline clock must keep ticking");
    CHECK(!strcmp(out, "01 Jan 2024 08:01 MYT"), "offline minute must advance");
    for (size_t i = 0; i < sizeof invalid / sizeof invalid[0]; ++i) {
        pocket_clock_note_sync(invalid[i]);
        CHECK(pocket_clock_ready(), "invalid/reconnect sync must not clear ready");
    }
    CHECK(pocket_clock_format(MIN_UTC + 125, pocket_clock_ready(), out, sizeof out), "reconnected clock must keep ticking");
    CHECK(!strcmp(out, "01 Jan 2024 08:02 MYT"), "reconnect must not reset time display");
    CHECK(!pocket_clock_format(INT64_MAX, pocket_clock_ready(), out, sizeof out), "invalid formatting must be safe");
    CHECK(pocket_clock_ready(), "format error must not clear the sync latch");
    CHECK(!pocket_clock_format(MIN_UTC, false, out, sizeof out), "explicit unsynchronized formatting must wait");
    CHECK(pocket_clock_ready(), "formatter must not mutate readiness");
    pocket_clock_reset();
    CHECK(!pocket_clock_ready(), "new boot reset must clear ready");
    pocket_clock_note_sync(MAX_UTC);
    CHECK(pocket_clock_ready(), "valid maximum UTC sync must latch ready");
    pocket_clock_reset();
    CHECK(!pocket_clock_ready(), "final reset must clear ready");
    puts("PASS clock sync latch and offline ticking");
    return 0;
}
static int null_outputs(void) {
    char guard = 'Z';
    CHECK(!pocket_clock_format(MIN_UTC, true, NULL, 0), "null zero-capacity output must fail safely");
    CHECK(!pocket_clock_format(MIN_UTC, true, NULL, 64), "null output must fail safely");
    CHECK(!pocket_clock_format(MIN_UTC, true, &guard, 0), "zero capacity must fail");
    CHECK(guard == 'Z', "zero capacity must not write");
    puts("PASS clock null and zero outputs");
    return 0;
}
static int formats(void) {
    int64_t utc;
    unsigned synchronized;
    size_t cap;
    int scanned;
    while ((scanned = scanf("%" SCNd64 " %u %zu", &utc, &synchronized, &cap)) == 3) {
        char out[64];
        CHECK(cap <= sizeof out, "invalid harness capacity");
        memset(out, 'Z', sizeof out);
        const bool ok = pocket_clock_format(utc, synchronized != 0, out, cap);
        for (size_t i = cap; i < sizeof out; ++i)
            CHECK(out[i] == 'Z', "formatter wrote beyond caller capacity");
        CHECK(cap == 0 || memchr(out, '\0', cap) != NULL, "output must be NUL terminated inside capacity");
        printf("%u|%s\n", ok ? 1u : 0u, cap ? out : "");
    }
    CHECK(scanned == EOF, "invalid harness input");
    return 0;
}
int main(int argc, char **argv) {
    CHECK(argc == 2, "harness mode missing");
    if (!strcmp(argv[1], "latch")) return latch();
    if (!strcmp(argv[1], "null")) return null_outputs();
    CHECK(!strcmp(argv[1], "formats"), "invalid harness mode");
    return formats();
}
'''


def seconds(year, month, day, hour=0, minute=0, second=0):
    value = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc) - EPOCH
    return value.days * 86400 + value.seconds


def expected(utc, synchronized, cap):
    if synchronized and MIN_UTC <= utc <= MAX_UTC - 8 * 3600 and cap >= 22:
        local = EPOCH + timedelta(seconds=utc, hours=8)
        return "1|" + (f"{local.day:02} {MONTHS[local.month - 1]} {local.year:04} "
                       f"{local.hour:02}:{local.minute:02} MYT")
    return "0|" + WAITING[:max(0, cap - 1)]


class FirmwareClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("gcc") or shutil.which("clang")
        cached = Path(r"C:\Program Files\LLVM\bin\clang.exe")
        if not cls.compiler and cached.is_file():
            cls.compiler = str(cached)
        if not cls.compiler:
            raise unittest.SkipTest("owner-installed host C compiler unavailable")
        # Windows may redirect the default Temp into a restricted app sandbox;
        # the owner's ordinary Temp is an explicitly writable build location.
        temp_base = Path.home() / "AppData/Local/Temp" if os.name == "nt" else None
        cls.temp = tempfile.TemporaryDirectory(dir=temp_base)
        cls.directory = Path(cls.temp.name)
        cls.fixture = cls.directory / "clock_fixture.c"
        cls.fixture.write_text(HARNESS, encoding="utf-8")
        cls.executable = cls.directory / ("clock.exe" if os.name == "nt" else "clock")
        cls.compiled = False

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def binary(self):
        if not self.__class__.compiled:
            core = ROOT / "firmware/core"
            built = subprocess.run([
                self.compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-I" + str(core), str(core / "pocket_clock.c"), str(self.fixture),
                "-o", str(self.executable),
            ], capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(built.returncode, 0, "Actual clock C must compile:\n" + built.stdout + built.stderr)
            self.__class__.compiled = True
        return str(self.executable)

    def formats(self, cases):
        data = "".join(f"{utc} {int(synced)} {cap}\n" for utc, synced, cap in cases)
        run = subprocess.run([self.binary(), "formats"], input=data, capture_output=True,
                             text=True, timeout=30, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        lines = run.stdout.splitlines()
        self.assertEqual(len(lines), len(cases))
        for case, actual in zip(cases, lines):
            self.assertEqual(actual, expected(*case), f"UTC/synchronized/cap={case}")

    def test_malaysia_rollovers(self):
        instants = [seconds(2026, 10, 16, 8, 35), seconds(2026, 10, 16, 15, 59, 59),
                    seconds(2026, 10, 16, 16), seconds(2026, 1, 31, 16),
                    seconds(2026, 12, 31, 16), seconds(2024, 2, 28, 16),
                    seconds(2024, 2, 29, 16), seconds(2025, 2, 28, 16),
                    seconds(2100, 2, 28, 16), seconds(2400, 2, 28, 16),
                    seconds(2400, 2, 29, 16), seconds(2026, 3, 8, 16),
                    seconds(2026, 11, 1, 16)]
        self.formats([(utc, True, 64) for utc in instants])

    def test_all_year_month_boundaries_2024_to_9999(self):
        cases = []
        for year in range(2024, 10000):
            for month in range(1, 13):
                midnight = seconds(year, month, 1) - 8 * 3600
                if midnight >= MIN_UTC:
                    cases.extend([(midnight - 1, True, 64), (midnight, True, 64)])
        self.formats(cases)

    def test_unsynchronized_never_invents_date(self):
        self.formats([(utc, False, 64) for utc in
                      [0, MIN_UTC, seconds(2026, 10, 16, 8, 35), MAX_UTC]])

    def test_invalid_epochs_and_overflow_guards(self):
        invalid = [-(1 << 63), -1, 0, MIN_UTC - 1, MAX_UTC - 8 * 3600 + 1,
                   MAX_UTC, MAX_UTC + 1, (1 << 63) - 8 * 3600,
                   (1 << 63) - 1]
        self.formats([(utc, True, 64) for utc in invalid])

    def test_minimum_and_maximum_local_date(self):
        self.formats([(MIN_UTC, True, 64), (MAX_UTC - 8 * 3600, True, 64)])

    def test_tiny_capacities_are_terminated_and_never_overwrite(self):
        self.formats([(seconds(2026, 10, 16, 8, 35), synchronized, cap)
                      for synchronized in [False, True] for cap in range(0, 25)])

    def test_sync_latch_offline_reconnect_and_reset(self):
        run = subprocess.run([self.binary(), "latch"], capture_output=True, text=True,
                             timeout=10, check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("PASS clock sync latch and offline ticking", run.stdout)

    def test_null_and_zero_capacity_outputs(self):
        run = subprocess.run([self.binary(), "null"], capture_output=True, text=True,
                             timeout=10, check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
