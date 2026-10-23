"""Offline contract tests; the native client is never invoked by this suite."""
import contextlib
import importlib.util
import io
import inspect
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import official_claude_usage as producer
except ModuleNotFoundError:
    producer = None


def epoch(text):
    return int(datetime.fromisoformat(text).timestamp())


OBSERVED = epoch("2026-10-16T05:30:00+00:00")


def has_zone(name):
    try:
        ZoneInfo(name)
        return True
    except ZoneInfoNotFoundError:
        return False


HAS_KL = has_zone("Asia/Kuala_Lumpur")


def panel(session=32, week=93, *, suffix="", session_reset="1:50pm", week_reset="Oct 17, 1am"):
    rows = ["Usage", "Current session"]
    if session is not None:
        rows += [f"████░ {session}% used", f"Resets {session_reset} (Asia/Kuala_Lumpur)"]
    rows += ["Current week (all models)"]
    if week is not None:
        rows += [f"████░ {week}% used", f"Resets {week_reset} (Asia/Kuala_Lumpur)"]
    rows += ["Current week (Fable)", "0% used", "Extra usage", "Local session cost: $0", suffix]
    return "\n".join(rows)


def frame(text):
    return ("\x1b[2J\x1b[H" + text.replace("\n", "\r\n")).encode()


class ContractBase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(producer, "official_claude_usage implementation is missing")

    def machine(self):
        return producer.FreshUsage(stable_seconds=0.5)

    def refreshed(self, text=None):
        machine = self.machine()
        machine.observe(panel(), monotonic=1, wall_time=OBSERVED)
        machine.observe(panel(suffix="Refreshing."), monotonic=2, wall_time=OBSERVED + 1)
        machine.observe(text or panel(33, 94), monotonic=3, wall_time=OBSERVED + 2)
        result = machine.observe(text or panel(33, 94), monotonic=3.6, wall_time=OBSERVED + 3)
        return machine, result


class Contract(ContractBase):
    def test_cache_is_never_a_fresh_snapshot(self):
        machine = self.machine()
        for tick in (1, 10, 50):
            self.assertIsNone(machine.observe(panel(), monotonic=tick, wall_time=OBSERVED + tick))
        self.assertEqual(machine.failure_code(), "freshness_unverified")

    def test_cache_then_refresh_then_stable_fresh_numeric_snapshot(self):
        machine, result = self.refreshed()
        self.assertEqual(result.snapshot, {
            "version": 1, "scope": "account", "provider": "claude", "source": "claude_code",
            "fetched_at": OBSERVED + 3,
            "metrics": [
                {"window": "five_hour", "used_percent": 33, "resets_at": None, "window_minutes": 300},
                {"window": "seven_day", "used_percent": 94, "resets_at": None, "window_minutes": 10080},
            ],
        })
        self.assertTrue(result.evidence["refresh_started"])
        self.assertTrue(result.evidence["refresh_completed"])
        self.assertTrue(result.evidence["stable_final_rows"])
        expected_interval = {
            "start": epoch("2026-10-16T05:50:00+00:00"),
            "end": epoch("2026-10-16T05:51:00+00:00"), "precision_seconds": 60,
        } if HAS_KL else None
        self.assertEqual(result.evidence["reset_intervals"]["five_hour"], expected_interval)

    def test_unchanged_values_need_same_completed_refresh(self):
        machine, result = self.refreshed(panel())
        self.assertEqual([row["used_percent"] for row in result.snapshot["metrics"]], [32, 93])
        self.assertEqual(result.snapshot["fetched_at"], OBSERVED + 3)

    def test_cache_then_error_cannot_publish(self):
        machine = self.machine()
        machine.observe(panel(suffix="Refreshing."), monotonic=1, wall_time=OBSERVED)
        with self.assertRaisesRegex(producer.ProbeError, "refresh_failed"):
            machine.observe(panel(suffix="Error: PRIVATE provider detail"), monotonic=2, wall_time=OBSERVED)

    def test_source_loading_or_refresh_errors_cannot_become_fresh_cached_rows(self):
        for error in ("Error loading usage data", "Failed to refresh usage"):
            machine = self.machine()
            machine.observe(panel(suffix="Refreshing."), monotonic=1, wall_time=OBSERVED)
            failure = None
            try:
                for tick in (2, 4):
                    machine.observe(panel(suffix=error), monotonic=tick, wall_time=OBSERVED + tick)
            except producer.ProbeError as caught:
                failure = caught.code
            self.assertEqual(failure, "refresh_failed", error)
            self.assertIsNone(machine.result)

    def test_visible_stale_status_cannot_publish(self):
        with self.assertRaisesRegex(producer.ProbeError, "stale_data"):
            self.refreshed(panel(33, 94, suffix="Showing cached data; stale"))

    def test_refresh_must_disappear_before_stability(self):
        machine = self.machine()
        for tick in (1, 3, 20):
            self.assertIsNone(machine.observe(panel(suffix="Refreshing…"), monotonic=tick, wall_time=OBSERVED))

    def test_partial_rows_stabilize_before_completion(self):
        machine = self.machine()
        machine.observe(panel(suffix="Refreshing."), monotonic=1, wall_time=OBSERVED)
        machine.observe(panel(33, None), monotonic=2, wall_time=OBSERVED)
        self.assertIsNone(machine.observe(panel(33, 94), monotonic=2.4, wall_time=OBSERVED))
        result = machine.observe(panel(33, 94), monotonic=3, wall_time=OBSERVED + 1)
        self.assertEqual(len(result.snapshot["metrics"]), 2)

    def test_changed_or_refreshing_screen_invalidates_earlier_result(self):
        for next_frame in (panel(34, 94), panel(suffix="Refreshing."), "Usage\nNo account rows"):
            machine, result = self.refreshed()
            self.assertIsNotNone(result)
            self.assertIsNone(machine.observe(next_frame, monotonic=4, wall_time=OBSERVED + 4))
            self.assertIsNone(machine.result)

    def test_absent_window_is_dropped_independently(self):
        _, result = self.refreshed(panel(None, 94))
        self.assertEqual([row["window"] for row in result.snapshot["metrics"]], ["seven_day"])

    def test_local_footer_preserves_only_available_account_window(self):
        for label, window, duration in producer.WINDOWS:
            with self.subTest(label=label):
                text = "Usage\n" + label + "\n33% used\nResets 6am (UTC)\nLocal session cost: $0"
                rows = producer.parse_rows(text, OBSERVED)
                self.assertEqual([(row["window"], row["used_percent"]) for row in rows], [(window, 33)])
                _, result = self.refreshed(text)
                self.assertEqual(result.snapshot["metrics"], [
                    {"window": window, "used_percent": 33, "resets_at": None, "window_minutes": duration},
                ])

    def test_missing_rows_never_make_usable_snapshot(self):
        machine = self.machine()
        machine.observe("Usage\nRefreshing.", monotonic=1, wall_time=OBSERVED)
        for tick in (2, 4, 8):
            self.assertIsNone(machine.observe("Usage\nNo data available", monotonic=tick, wall_time=OBSERVED))
        self.assertEqual(machine.failure_code(), "no_usable_windows")

    def test_model_specific_and_local_numbers_do_not_leak(self):
        text = "Usage\nCurrent week (Fable)\n99% used\nContext window 78% used\nLocal tokens 123456"
        self.assertEqual(producer.parse_rows(text, OBSERVED), [])

    def test_context_or_local_descriptors_under_account_header_are_not_quota(self):
        for line in ("Context window 78% used", "Context window78% used", "Local tokens 78% used"):
            text = "Usage\nCurrent session\n" + line + "\nLocal tokens 123456"
            self.assertEqual(producer.parse_rows(text, OBSERVED), [], line)

    def test_percent_is_integer_display_precision_and_range_checked(self):
        for invalid in ("101", "-1", "1.5", "NaN", "1e2", "+2"):
            with self.subTest(invalid=invalid):
                rows = producer.parse_rows(panel(invalid, None), OBSERVED)
                self.assertEqual(rows, [])
        self.assertEqual([row["used_percent"] for row in producer.parse_rows(panel(0, 100), OBSERVED)], [0, 100])

    def test_unsupported_reset_text_retains_percentage_only(self):
        _, result = self.refreshed(panel(33, 94, session_reset="in about an hour"))
        self.assertIsNone(result.snapshot["metrics"][0]["resets_at"])
        self.assertIsNone(result.evidence["reset_intervals"]["five_hour"])

    def test_final_snapshot_passes_existing_pure_validator(self):
        location = Path(os.environ.get("CLAUDE_PRODUCER_TEST_BACKEND", str(Path(__file__).parent / "backend")))
        if not (location / "pocket" / "quota_snapshots.py").is_file():
            self.skipTest("frozen backend is intentionally not copied into isolated Linux staging")
        with patch.object(sys, "path", [str(location), *sys.path]):
            from pocket.quota_snapshots import validate_snapshot
        _, result = self.refreshed()
        self.assertEqual(validate_snapshot(result.snapshot), result.snapshot)

    def test_safe_errors_never_embed_raw_detail(self):
        for message in ("Error: PRIVATE identity", "Failed to fetch usage PRIVATE path", "Unable to load usage PRIVATE"):
            machine = self.machine()
            with self.assertRaises(producer.ProbeError) as caught:
                machine.observe(panel(suffix=message), monotonic=1, wall_time=OBSERVED)
            self.assertEqual(str(caught.exception), "refresh_failed")
        with self.assertRaises(ValueError):
            producer.ProbeError("PRIVATE detail")

    def test_setup_login_trust_and_unknown_commands_fail_closed(self):
        for text in ("Choose the text style", "Welcome to Claude Code\nLet's get started",
                     "Please log in", "Do you trust the files in this folder?", "Unknown command: /usage"):
            with self.subTest(text=text), self.assertRaises(producer.ProbeError):
                producer.reject_unsafe_screen(text)


class ResetIntervals(ContractBase):
    def interval(self, text, observed=OBSERVED, window="five_hour"):
        return producer.reset_interval(text, observed, window)

    @unittest.skipUnless(HAS_KL, "system IANA timezone database unavailable")
    def test_ui_minute_precision_is_half_open_interval(self):
        self.assertEqual(self.interval("Resets 1:50pm (Asia/Kuala_Lumpur)"), {
            "start": epoch("2026-10-16T05:50:00+00:00"), "end": epoch("2026-10-16T05:51:00+00:00"),
            "precision_seconds": 60,
        })

    @unittest.skipUnless(HAS_KL, "system IANA timezone database unavailable")
    def test_midnight_rollover(self):
        observed = epoch("2026-10-16T15:59:30+00:00")
        value = self.interval("Resets 12:02am (Asia/Kuala_Lumpur)", observed)
        self.assertEqual(value["start"], epoch("2026-10-16T16:02:00+00:00"))

    @unittest.skipUnless(HAS_KL, "system IANA timezone database unavailable")
    def test_weekly_year_rollover(self):
        observed = epoch("2026-12-31T12:00:00+00:00")
        value = self.interval("Resets Jan 1, 1am (Asia/Kuala_Lumpur)", observed, "seven_day")
        self.assertEqual(value["start"], epoch("2026-12-31T17:00:00+00:00"))

    @unittest.skipUnless(HAS_KL, "system IANA timezone database unavailable")
    def test_reset_observed_inside_its_minute_does_not_roll_forward(self):
        value = self.interval("Resets 1:50pm (Asia/Kuala_Lumpur)", epoch("2026-10-16T05:50:30+00:00"))
        self.assertEqual(value["start"], epoch("2026-10-16T05:50:00+00:00"))

    def test_stale_and_unreasonable_reset_dates_are_unknown(self):
        for text, window in (("Resets 1:20pm (Asia/Kuala_Lumpur)", "five_hour"),
                             ("Resets Oct 1, 1am (Asia/Kuala_Lumpur)", "seven_day"),
                             ("Resets Oct 30, 1am (Asia/Kuala_Lumpur)", "seven_day"),
                             ("Resets Feb 30, 1am (Asia/Kuala_Lumpur)", "seven_day")):
            self.assertIsNone(self.interval(text, window=window))

    def test_invalid_timezone_and_unsupported_grammar_are_unknown(self):
        for text in ("Resets 1:50pm (PRIVATE/Missing)", "Resets 13:50 (UTC)",
                     "Resets tomorrow (UTC)", "Resets 13:50pm (UTC)", "Resets 1:99pm (UTC)"):
            self.assertIsNone(self.interval(text))

    @unittest.skipUnless(has_zone("America/New_York"), "system IANA timezone database unavailable")
    def test_dst_ambiguous_or_nonexistent_minute_is_unknown(self):
        self.assertIsNone(self.interval("Resets Nov 1, 1:30am (America/New_York)",
                                       epoch("2026-11-01T03:00:00+00:00"), "seven_day"))
        self.assertIsNone(self.interval("Resets Mar 8, 2:30am (America/New_York)",
                                       epoch("2026-03-08T04:00:00+00:00"), "seven_day"))


class TerminalTests(ContractBase):
    def test_ansi_cursor_and_erase_replace_instead_of_accumulating_history(self):
        terminal = producer.Terminal(rows=80, columns=200)
        terminal.feed(frame(panel(suffix="Refreshing.")))
        terminal.feed(frame(panel(33, 94)))
        self.assertNotIn("Refreshing", terminal.text())
        self.assertNotIn("32%", terminal.text())
        self.assertIn("33%", terminal.text())

    def test_split_utf8_osc_and_csi_are_incremental(self):
        terminal = producer.Terminal(rows=8, columns=40)
        data = b"\x1b]0;PRIVATE title\x07" + "❯ 界x".encode() + b"\r\x1b[2K>\x1b[1;5Hok"
        for item in data:
            terminal.feed(bytes([item]))
        self.assertEqual(terminal.text().splitlines()[0].rstrip(), ">   ok")
        self.assertNotIn("PRIVATE", terminal.text())

    def test_carriage_return_backspace_and_erase_line(self):
        terminal = producer.Terminal(rows=4, columns=30)
        terminal.feed(b"Refreshing.\r\x1b[2KDoneX\b \b")
        self.assertEqual(terminal.text().splitlines()[0].rstrip(), "Done")

    def test_grid_cursor_and_control_string_memory_are_bounded(self):
        terminal = producer.Terminal(rows=4, columns=30)
        terminal.feed(b"\x1b[999999;999999H" + b"x" * 5000)
        self.assertLessEqual(len(terminal.text()), 4 * 31)
        with self.assertRaisesRegex(producer.ProbeError, "terminal_limit"):
            terminal.feed(b"\x1b]" + b"x" * 5000)

    def test_unsupported_screen_mutation_fails_closed(self):
        terminal = producer.Terminal(rows=4, columns=30)
        with self.assertRaisesRegex(producer.ProbeError, "terminal_unsupported"):
            terminal.feed(b"\x1b[1z")

    def test_native_color_scheme_notification_mode_does_not_mutate_text(self):
        terminal = producer.Terminal(rows=4, columns=30)
        failure = None
        try:
            terminal.feed(b"preserved\x1b[?2031h\x1b[?2031l")
        except producer.ProbeError as error:
            failure = error.code
        self.assertIsNone(failure, "native color-scheme notification mode must be recognized")
        self.assertEqual(terminal.text(), "preserved")
        with self.assertRaisesRegex(producer.ProbeError, "terminal_unsupported"):
            terminal.feed(b"\x1b[?2032h")

    def test_native_terminal_version_query_does_not_mutate_text_or_broaden_queries(self):
        terminal = producer.Terminal(rows=4, columns=30)
        failure = None
        try:
            terminal.feed(b"preserved\x1b[>0q")
        except producer.ProbeError as error:
            failure = error.code
        self.assertIsNone(failure, "observed XTVERSION query must be recognized")
        self.assertEqual(terminal.text(), "preserved")
        with self.assertRaisesRegex(producer.ProbeError, "terminal_unsupported"):
            terminal.feed(b"\x1b[>1q")

    def test_transitions_in_one_os_read_are_not_lost(self):
        terminal = producer.Terminal(rows=80, columns=200)
        machine = self.machine()
        terminal.feed(frame(panel()) + frame(panel(suffix="Refreshing.")) + frame(panel(33, 94)),
                      observe=lambda text: machine.observe(text, monotonic=1, wall_time=OBSERVED))
        result = machine.observe(terminal.text(), monotonic=2, wall_time=OBSERVED + 1)
        self.assertEqual(result.snapshot["metrics"][0]["used_percent"], 33)

    def test_partial_csi_and_utf8_have_no_complete_observation_boundary(self):
        terminal = producer.Terminal(rows=4, columns=30)
        self.assertTrue(callable(getattr(terminal, "complete", None)), "terminal completeness gate is missing")
        self.assertTrue(terminal.complete())
        terminal.feed(b"\x1b[")
        self.assertFalse(terminal.complete())
        terminal.feed(b"0m")
        self.assertTrue(terminal.complete())
        encoded = "界".encode()
        terminal.feed(encoded[:1])
        self.assertFalse(terminal.complete())
        terminal.feed(encoded[1:])
        self.assertTrue(terminal.complete())

    def test_incomplete_terminal_boundary_invalidates_stable_candidate(self):
        machine = self.machine()
        self.assertIn("complete", inspect.signature(machine.observe).parameters,
                      "freshness must accept a terminal completeness boundary")
        machine.observe(panel(suffix="Refreshing."), monotonic=1, wall_time=OBSERVED)
        machine.observe(panel(), monotonic=2, wall_time=OBSERVED)
        self.assertIsNone(machine.observe(panel(), monotonic=3, wall_time=OBSERVED,
                                          complete=False))
        self.assertIsNone(machine.result)
        self.assertIsNone(machine.observe(panel(), monotonic=4, wall_time=OBSERVED))
        self.assertIsNotNone(machine.observe(panel(), monotonic=5, wall_time=OBSERVED + 1))

    def test_native_keyboard_flag_query_changes_neither_screen_nor_keyboard_mode(self):
        terminal = producer.Terminal(rows=4, columns=30)
        failure = None
        try:
            terminal.feed(b"preserved\x1b[?u")
        except producer.ProbeError as error:
            failure = error.code
        self.assertIsNone(failure, "observed keyboard status query must be recognized")
        self.assertEqual(terminal.text(), "preserved")
        with self.assertRaisesRegex(producer.ProbeError, "terminal_unsupported"):
            terminal.feed(b"\x1b[?1u")

    def test_synchronized_update_exposes_only_last_completed_screen(self):
        terminal = producer.Terminal(rows=80,columns=200)
        previous = panel(suffix="Refreshing.")
        terminal.feed(frame(previous))
        terminal.feed(b"\x1b[?2026h"+frame(panel()))
        self.assertFalse(terminal.complete())
        self.assertIn("Refreshing.",terminal.text())
        terminal.feed(b"\x1b[?2026l")
        self.assertTrue(terminal.complete())
        self.assertNotIn("Refreshing",terminal.text())

    def test_stalled_synchronized_refresh_cannot_publish_cached_rows(self):
        terminal = producer.Terminal(rows=80,columns=200)
        machine = self.machine()
        terminal.feed(frame(panel(suffix="Refreshing.")))
        machine.observe(terminal.text(),monotonic=1,wall_time=OBSERVED)
        terminal.feed(b"\x1b[?2026h"+frame(panel()))
        for tick in (2,4):
            self.assertIsNone(machine.observe(terminal.text(),monotonic=tick,
                                              wall_time=OBSERVED,complete=terminal.complete()))
        terminal.feed(b"\x1b[?2026l")
        self.assertIsNone(machine.observe(terminal.text(),monotonic=5,wall_time=OBSERVED))
        self.assertIsNotNone(machine.observe(terminal.text(),monotonic=6,wall_time=OBSERVED+1))


class NativeDollarPromptTests(ContractBase):
    def terminal(self, text="Claude Code v2.1.299\n$"):
        terminal = producer.Terminal()
        terminal.feed(frame(text))
        return terminal

    def ready(self, terminal, owned=True):
        predicate = getattr(producer, "native_dollar_prompt", None)
        self.assertTrue(callable(predicate), "structured native-dollar predicate is missing")
        return predicate(terminal, owned_native_foreground=owned)

    def test_observed_owned_official_empty_active_dollar_is_recognized(self):
        terminal = self.terminal()
        self.assertTrue(self.ready(terminal))
        self.assertFalse(producer.ordinary_prompt(terminal.text()))
        self.assertEqual((terminal.row, terminal.column), (1, 1))

    def test_native_identity_and_foreground_are_required(self):
        for owned in (False, None, 1, "true"):
            with self.subTest(owned=owned):
                self.assertFalse(self.ready(self.terminal(), owned))

    def test_exact_single_current_banner_is_required(self):
        for header in ("Native client", "Claude Code v2.1.298", "Claude Code v2.1.299 extra",
                       "you: Claude Code v2.1.299", "> Claude Code v2.1.299",
                       '"Claude Code v2.1.299"', "Claude Code v2.1.299\nClaude Code v2.1.299"):
            with self.subTest(header=header):
                self.assertFalse(self.ready(self.terminal(header + "\n$")))

    def test_shell_unsafe_and_fenced_examples_are_rejected(self):
        for marker in ("GNU bash", "Last login:", "Choose a text style", "Please login",
                       "Trust this workspace", "Error loading usage data", "```", "~~~"):
            with self.subTest(marker=marker):
                self.assertFalse(self.ready(self.terminal("Claude Code v2.1.299\n" + marker + "\n$")))

    def test_only_the_empty_last_active_dollar_at_column_zero_is_accepted(self):
        for input_rows in ("$ command", "$$", " $", "$ ", "$\t", "$\r", "$\nstatus", "$\n$"):
            with self.subTest(input_rows=input_rows):
                self.assertFalse(self.ready(self.terminal("Claude Code v2.1.299\n" + input_rows)))
        terminal = self.terminal()
        terminal.feed(b"\x1b[H")
        self.assertFalse(self.ready(terminal))

    def test_partial_escape_utf8_and_synchronized_frames_are_rejected(self):
        for suffix in (b"\x1b[", chr(0x4e2d).encode()[:1], b"\x1b[?2026h"):
            with self.subTest(suffix=suffix):
                terminal = self.terminal()
                terminal.feed(suffix)
                self.assertFalse(self.ready(terminal))

    def test_dollar_does_not_broaden_cleanup_or_legacy_text_predicate(self):
        self.assertFalse(producer.ordinary_prompt("$"))
        self.assertTrue(self.ready(self.terminal()))
        cleanup = producer.CleanupPrompt(stable_seconds=.06)
        self.assertFalse(cleanup.feed(frame("Claude Code v2.1.299\n$"), monotonic=0))
        self.assertFalse(cleanup.observe(monotonic=1))


class CleanupTests(ContractBase):
    def cleanup_model(self):
        self.assertTrue(callable(getattr(producer,"CleanupPrompt",None)),
                        "post-Esc cleanup must own a fresh terminal observer")
        return producer.CleanupPrompt(stable_seconds=.06)

    def test_idle_stale_startup_screen_cannot_authorize_cleanup_text(self):
        previous = producer.Terminal(rows=80,columns=200)
        previous.feed(frame("Native client\n❯ "))
        model = self.cleanup_model()
        self.assertFalse(model.observe(monotonic=0))
        self.assertFalse(model.observe(monotonic=.07))

    def test_style_or_osc_only_post_escape_output_does_not_authorize_text(self):
        model = self.cleanup_model()
        self.assertFalse(model.feed(b"\x1b[0m\x1b]0;PRIVATE synthetic title\x07",monotonic=0))
        self.assertFalse(model.observe(monotonic=.2))

    def test_complete_new_post_escape_prompt_redraw_must_stabilize(self):
        model = self.cleanup_model()
        self.assertFalse(model.feed(frame("Native client\n❯ "),monotonic=0))
        self.assertFalse(model.observe(monotonic=.03))
        self.assertTrue(model.observe(monotonic=.07))

    def test_partial_csi_or_utf8_never_qualifies_as_cleanup_recovery(self):
        for suffix,completion in ((b"\x1b[",b"0m"),("界".encode()[:1],"界".encode()[1:])):
            model = self.cleanup_model()
            self.assertFalse(model.feed(frame("Native client\n❯ \n")+suffix,monotonic=0))
            self.assertFalse(model.observe(monotonic=.2))
            self.assertFalse(model.feed(completion,monotonic=.3))
            self.assertTrue(model.observe(monotonic=.4))

    def test_unsafe_post_escape_state_permanently_blocks_cleanup_text(self):
        model = self.cleanup_model()
        self.assertFalse(model.feed(frame("Please log in"),monotonic=0))
        self.assertFalse(model.feed(frame("Native client\n❯ "),monotonic=1))
        self.assertFalse(model.observe(monotonic=2))

    def test_synchronized_prompt_is_unusable_until_end_and_new_stability(self):
        model = self.cleanup_model()
        self.assertFalse(model.feed(b"\x1b[?2026h"+frame("Native client\n❯ "),monotonic=0))
        self.assertFalse(model.observe(monotonic=.2))
        self.assertFalse(model.feed(b"\x1b[?2026l",monotonic=.3))
        self.assertTrue(model.observe(monotonic=.4))

    def test_discard_queued_bytes_obeys_budget_and_requires_empty_queue(self):
        self.assertTrue(callable(getattr(producer,"_discard_queued",None)),
                        "queued pre-Esc output must be discarded before cleanup input")
        buffer = bytearray(b"Native client\n> ")
        def read(_descriptor,maximum):
            if not buffer:
                raise BlockingIOError()
            value = bytes(buffer[:maximum]); del buffer[:maximum]
            return value
        with patch.object(producer.os,"read",side_effect=read):
            count,drained = producer._discard_queued(123,max_bytes=5,deadline=time.monotonic()+.1)
            self.assertEqual(count,5)
            self.assertFalse(drained)
            count,drained = producer._discard_queued(123,max_bytes=1024,deadline=time.monotonic()+.1)
            self.assertGreater(count,0)
            self.assertTrue(drained)


CHILD = r'''
import json, os, signal, sys, time, tty
tty.setraw(0)
mode, transcript = sys.argv[1:3]
def out(text): os.write(1, text.encode())
def screen(text): out("\x1b[2J\x1b[H" + text.replace("\n", "\r\n"))
def usage(a=32,b=93,tail=""):
    return "Usage\nCurrent session\n%d%% used\nResets 1:50pm (Asia/Kuala_Lumpur)\nCurrent week (all models)\n%d%% used\nResets Oct 17, 1am (Asia/Kuala_Lumpur)\nCurrent week (Fable)\n0%% used\n%s" % (a,b,tail)
out("\x1b[?2031h\x1b[>0q\x1b[?u")
if mode == "setup": screen("Choose the text style\n❯ Dark")
elif mode == "no_prompt": screen("Native client is waiting without an ordinary prompt")
elif mode == "transient_prompt":
    screen("Native client\n❯ "); time.sleep(.07); screen("Choose the text style\n❯ Dark")
elif mode == "native_dollar": screen("Claude Code v2.1.299\n$")
else: screen("Native client\n❯ ")
if mode == "ignore": signal.signal(signal.SIGTERM, signal.SIG_IGN)
received = b""
while True:
    data = os.read(0, 4096)
    if not data: break
    received += data
    with open(transcript, "w") as handle: json.dump({"received": received.hex(), "pid": os.getpid(), "env": {
        name: os.environ.get(name) for name in ("CLAUDE_CODE_SKIP_PROMPT_HISTORY","DISABLE_UPDATES","IS_DEMO","CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC","TERM")}}, handle)
    if b"/usage\r" in data:
        if mode == "flood": out("x" * 270000)
        elif mode in ("silent", "style_only"): pass
        elif mode == "interrupt": os.kill(os.getppid(),signal.SIGINT)
        elif mode in ("cache", "ignore"): screen(usage())
        elif mode == "exit": sys.exit(0)
        else:
            screen(usage()); time.sleep(.06); screen(usage(tail="Refreshing.")); time.sleep(.06)
            if mode == "error": screen(usage(tail="Error: PRIVATE path identity"))
            elif mode in ("partial_csi", "partial_utf8"):
                suffix=b"\x1b[" if mode=="partial_csi" else "界".encode()[:1]
                os.write(1,("\x1b[2J\x1b[H"+usage().replace("\n","\r\n")).encode()+suffix)
            else: screen(usage(32 if mode == "unchanged" else 33,93 if mode == "unchanged" else 94))
    if b"\x1b" in data and mode=="style_only": out("\x1b[0m\x1b]0;PRIVATE synthetic title\x07")
    if b"\x1b" in data and mode not in ("error", "ignore", "setup", "transient_prompt", "silent", "style_only"):
        screen("Claude Code v2.1.299\n$" if mode=="native_dollar" else "Native client\n❯ ")
    if b"/exit\r" in data and mode != "ignore": break
'''


@unittest.skipUnless(sys.platform.startswith("linux"), "native PTY contract requires Linux")
class PtyTests(ContractBase):
    def run_child(self, mode="fresh", *, trusted_native=False, **changes):
        temporary = tempfile.TemporaryDirectory(prefix="claude-producer-test-",
                                                 dir=os.environ.get("CLAUDE_PRODUCER_TEST_TMPDIR"))
        self.addCleanup(temporary.cleanup)
        transcript = Path(temporary.name) / "fixed-command-evidence.json"
        limits = dict(startup_seconds=0.7, total_seconds=2.5, stable_seconds=0.12,
                      graceful_seconds=0.25, terminate_seconds=0.15)
        limits.update(changes)
        result = None
        failure = None
        start = time.monotonic()
        command = (sys.executable, "-B", "-u", "-c", CHILD, mode, str(transcript))
        try:
            with patch.object(producer, "NATIVE_COMMAND", command) if trusted_native else contextlib.nullcontext():
                result = producer._probe_command(command, limits=producer.Limits(**limits))
        except producer.ProbeError as error:
            failure = error
        elapsed = time.monotonic() - start
        record = json.loads(transcript.read_text()) if transcript.exists() else None
        if failure:
            self.assertTrue(failure.evidence["child_reaped"])
            self.assertTrue(failure.evidence["pty_closed"])
        if record:
            with self.assertRaises(ProcessLookupError):
                os.kill(record["pid"], 0)
            self.assertIn(bytes.fromhex(record["received"]),
                          (b"/usage\r", b"/usage\r\x1b", b"/usage\r\x1b/exit\r"))
        return result, failure, record, elapsed

    def test_owned_native_dollar_refreshes_and_uses_control_only_cleanup(self):
        result, failure, record, _ = self.run_child("native_dollar", trusted_native=True)
        self.assertIsNone(failure)
        self.assertEqual([row["used_percent"] for row in result.snapshot["metrics"]], [33, 94])
        self.assertEqual(bytes.fromhex(record["received"]), b"/usage\r\x1b")
        self.assertFalse(result.evidence["cleanup_exit_sent"])
        self.assertTrue(result.evidence["terminated"])
        self.assertTrue(result.evidence["child_reaped"])

    def test_identical_dollar_banner_from_untrusted_argv_sends_no_input(self):
        result, failure, record, _ = self.run_child("native_dollar", total_seconds=.9)
        self.assertIsNone(result)
        self.assertEqual(failure.code, "startup_timeout")
        self.assertFalse(failure.evidence["usage_command_sent"])
        self.assertIsNone(record)

    def test_real_pty_cache_refresh_final_and_owned_child_cleanup(self):
        result, failure, record, elapsed = self.run_child()
        self.assertIsNone(failure)
        self.assertEqual([row["used_percent"] for row in result.snapshot["metrics"]], [33, 94])
        self.assertTrue(result.evidence["child_reaped"])
        self.assertTrue(result.evidence["pty_closed"])
        self.assertEqual(bytes.fromhex(record["received"]), b"/usage\r\x1b/exit\r")
        self.assertLess(elapsed, 2.5)

    def test_real_pty_unchanged_fresh_data(self):
        result, failure, _, _ = self.run_child("unchanged")
        self.assertIsNone(failure)
        self.assertEqual([row["used_percent"] for row in result.snapshot["metrics"]], [32, 93])

    def test_real_pty_cache_only_fails_and_reaps(self):
        result, failure, _, elapsed = self.run_child("cache", total_seconds=0.9)
        self.assertIsNone(result)
        self.assertEqual(str(failure), "freshness_unverified")
        self.assertLess(elapsed, 1.5)

    def test_real_pty_error_never_returns_cached_snapshot(self):
        result, failure, _, _ = self.run_child("error")
        self.assertIsNone(result)
        self.assertEqual(str(failure), "refresh_failed")

    def test_real_pty_setup_sends_no_commands(self):
        result, failure, record, _ = self.run_child("setup")
        self.assertIsNone(result)
        self.assertEqual(str(failure), "setup_required")
        self.assertIsNone(record)

    def test_transient_prompt_before_setup_is_never_used(self):
        result, failure, record, _ = self.run_child("transient_prompt")
        self.assertIsNone(result)
        self.assertEqual(str(failure), "setup_required")
        self.assertIsNone(record)

    def test_uncertain_error_cleanup_sends_only_escape(self):
        result, failure, record, _ = self.run_child("error")
        self.assertIsNone(result)
        self.assertEqual(str(failure), "refresh_failed")
        self.assertEqual(bytes.fromhex(record["received"]), b"/usage\r\x1b")

    def test_pre_escape_prompt_is_not_reused_without_a_post_escape_redraw(self):
        for mode in ("silent", "style_only"):
            result, failure, record, _ = self.run_child(mode, total_seconds=0.9)
            self.assertIsNone(result)
            self.assertEqual(str(failure), "freshness_unverified")
            self.assertEqual(bytes.fromhex(record["received"]), b"/usage\r\x1b")
            self.assertFalse(failure.evidence["cleanup_exit_sent"])

    def test_partial_csi_or_utf8_after_refresh_cannot_publish_cached_rows(self):
        for mode in ("partial_csi", "partial_utf8"):
            result, failure, _, _ = self.run_child(mode, total_seconds=1.2)
            self.assertIsNone(result)
            self.assertIsNotNone(failure)

    def test_pre_escape_queued_bytes_are_drained_with_a_strict_budget(self):
        read_fd, write_fd = os.pipe()
        try:
            os.set_blocking(read_fd, False)
            os.write(write_fd, b"Native client\n> \x1b[0m")
            count, drained = producer._discard_queued(read_fd, max_bytes=1024,
                                                      deadline=time.monotonic() + 0.1)
            self.assertGreater(count, 0)
            self.assertTrue(drained)
            with self.assertRaises(BlockingIOError):
                os.read(read_fd, 1)
            os.write(write_fd, b"x" * 20)
            count, drained = producer._discard_queued(read_fd, max_bytes=5,
                                                      deadline=time.monotonic() + 0.1)
            self.assertEqual(count, 5)
            self.assertFalse(drained)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_interrupted_continuous_main_reaps_then_stops_without_another_child(self):
        temporary = tempfile.TemporaryDirectory(prefix="claude-producer-test-",
                                                 dir=os.environ.get("CLAUDE_PRODUCER_TEST_TMPDIR"))
        self.addCleanup(temporary.cleanup)
        transcript = Path(temporary.name) / "interrupt-evidence.json"
        calls = []
        def controlled_probe():
            calls.append(1)
            if len(calls) > 1:
                raise AssertionError("another child was requested after interruption")
            return producer._probe_command((sys.executable,"-B","-u","-c",CHILD,"interrupt",str(transcript)),
                                            limits=producer.Limits(startup_seconds=.7,total_seconds=2.5,
                                                stable_seconds=.12,graceful_seconds=.25,terminate_seconds=.15))
        unexpected_retry = None
        exit_code = None
        with patch.object(producer,"probe",side_effect=controlled_probe), patch.object(producer.time,"sleep"), contextlib.redirect_stderr(io.StringIO()):
            try:
                exit_code = producer.main(["--count","0","--interval","60"])
            except AssertionError as error:
                unexpected_retry = str(error)
        self.assertIsNone(unexpected_retry)
        self.assertEqual(exit_code,130)
        self.assertEqual(len(calls),1)
        record = json.loads(transcript.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(record["pid"],0)

    def test_real_pty_startup_timeout_sends_no_commands(self):
        result, failure, record, elapsed = self.run_child("no_prompt")
        self.assertIsNone(result)
        self.assertEqual(str(failure), "startup_timeout")
        self.assertIsNone(record)
        self.assertLess(elapsed, 1.5)

    def test_real_pty_output_limit(self):
        result, failure, _, _ = self.run_child("flood", max_bytes=4096)
        self.assertIsNone(result)
        self.assertEqual(str(failure), "output_limit")
        self.assertLessEqual(failure.evidence["captured_bytes"], 4096)

    def test_real_pty_uncooperative_child_is_killed_and_reaped(self):
        result, failure, _, elapsed = self.run_child("ignore", total_seconds=0.9)
        self.assertIsNone(result)
        self.assertEqual(str(failure), "freshness_unverified")
        self.assertLess(elapsed, 1.5)

    def test_child_env_is_cloned_and_only_child_suppression_flag_removed(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "TERM": "dumb"}):
            _, failure, record, _ = self.run_child()
            self.assertIsNone(failure)
            self.assertEqual(os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
            self.assertEqual(os.environ["TERM"], "dumb")
        self.assertEqual(record["env"], {"CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1", "DISABLE_UPDATES": "1",
                                         "IS_DEMO": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": None,
                                         "TERM": "xterm-256color"})


class EntrypointTests(ContractBase):
    def test_child_terminal_contract_does_not_change_parent_environment(self):
        for parent_term in (None, "dumb", "vt100"):
            parent = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "PARENT_MARKER": "kept"}
            if parent_term is not None:
                parent["TERM"] = parent_term
            with self.subTest(parent_term=parent_term), patch.dict(os.environ, parent, clear=True):
                child = producer.child_environment()
                self.assertEqual(child.get("TERM"), "xterm-256color")
                self.assertEqual(dict(os.environ), parent)
                child["TERM"] = "synthetic child mutation"
                self.assertEqual(dict(os.environ), parent)

    def test_only_approved_native_command_is_constructed(self):
        self.assertEqual(producer.NATIVE_COMMAND, ("/home/example/.npm-global/bin/claude", "--safe-mode", "--ax-screen-reader"))

    def test_default_one_shot_stdout_is_canonical_only(self):
        _, result = self.refreshed()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(producer, "probe", return_value=result) as probe, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(producer.main([]), 0)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(json.loads(stdout.getvalue()), result.snapshot)
        self.assertEqual(stderr.getvalue(), "")

    def test_count_and_minimum_interval_validation(self):
        for arguments in (["--interval", "59"], ["--interval", "nan"], ["--count", "-1"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                producer.main(arguments)

    def test_safe_failure_has_no_stdout_or_raw_exception_text(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(producer, "probe", side_effect=producer.ProbeError("freshness_unverified")), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(producer.main([]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "claude_usage: freshness_unverified\n")

    def test_interrupted_probe_returns_130_without_repeating_or_sleeping(self):
        for count in ("0","2"):
            retried = None
            exit_code = None
            effects = [producer.ProbeError("interrupted"),AssertionError("interrupted probe was retried")]
            with patch.object(producer,"probe",side_effect=effects) as probe, patch.object(producer.time,"sleep") as sleep, contextlib.redirect_stderr(io.StringIO()):
                try:
                    exit_code = producer.main(["--count",count])
                except AssertionError as error:
                    retried = str(error)
            self.assertIsNone(retried)
            self.assertEqual(exit_code,130)
            self.assertEqual(probe.call_count,1)
            sleep.assert_not_called()

    def test_repeated_dry_run_waits_at_least_requested_interval(self):
        _, result = self.refreshed()
        with patch.object(producer, "probe", return_value=result) as probe, patch.object(producer.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(producer.main(["--count", "2", "--interval", "120"]), 0)
        self.assertEqual(probe.call_count, 2)
        sleep.assert_called_once_with(120.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
