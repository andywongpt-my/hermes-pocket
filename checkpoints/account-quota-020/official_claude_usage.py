"""Bounded, stdout-only adapter for the official Claude Code /usage panel.

Linux native PTY only, existing login only, no model input. No files are read or
written by this adapter. The source's reset display has minute precision, so
canonical reset epochs are always null; intervals live in separate evidence.
"""
from __future__ import annotations

import argparse
import codecs
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import json
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import time
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


NATIVE_COMMAND = ("/home/example/.npm-global/bin/claude", "--safe-mode", "--ax-screen-reader")
WINDOWS = (("Current session", "five_hour", 300),
           ("Current week (all models)", "seven_day", 10080))
SAFE_ERRORS = frozenset({"unsupported_platform", "spawn_failed", "io_failed", "startup_timeout",
                         "freshness_unverified", "no_usable_windows", "refresh_failed", "stale_data",
                         "setup_required", "login_required", "trust_required", "unknown_command",
                         "output_limit", "terminal_limit", "terminal_invalid", "terminal_unsupported",
                         "child_exited", "cleanup_failed", "interrupted"})


class ProbeError(Exception):
    """Only a fixed category and sanitized mechanical evidence may escape."""
    def __init__(self, code, evidence=None):
        if code not in SAFE_ERRORS:
            raise ValueError("unknown safe category")
        super().__init__(code)
        self.code = code
        self.evidence = {} if evidence is None else evidence


@dataclass(frozen=True)
class Limits:
    startup_seconds: float = 15.0
    total_seconds: float = 55.0
    stable_seconds: float = 0.8
    graceful_seconds: float = 1.5
    terminate_seconds: float = 0.5
    max_bytes: int = 256 * 1024

    def __post_init__(self):
        times = (self.startup_seconds, self.total_seconds, self.stable_seconds,
                 self.graceful_seconds, self.terminate_seconds)
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in times):
            raise ValueError("invalid limits")
        if (self.total_seconds > 60 or self.startup_seconds > self.total_seconds or
                self.graceful_seconds + self.terminate_seconds + 0.1 >= self.total_seconds or
                type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 256 * 1024):
            raise ValueError("invalid limits")


@dataclass
class ProbeResult:
    snapshot: dict
    evidence: dict


def reject_unsafe_screen(text):
    lowered = text.casefold()
    rules = (
        ("unknown_command", ("unknown command", "unknown slash command", "unrecognized command")),
        ("trust_required", ("do you trust", "trust this folder", "trust the files", "trust this workspace")),
        ("login_required", ("please log in", "please login", "not logged in", "sign in to claude", "login required", "select login method")),
        ("setup_required", ("choose the text style", "choose a text style", "let's get started", "let’s get started", "accept the terms", "do you accept", "onboarding")),
        ("stale_data", ("stale", "showing cached", "cached data", "last successful refresh")),
        ("refresh_failed", ("error:", "error fetching", "error loading usage", "failed to refresh usage", "failed to fetch", "failed to load", "unable to load", "unable to fetch", "could not fetch", "couldn't fetch", "usage unavailable")),
    )
    for code, phrases in rules:
        if any(phrase in lowered for phrase in phrases):
            raise ProbeError(code)


def ordinary_prompt(text):
    """Accept only a fresh empty input prompt or the client's fixed Try hint."""
    return any(re.fullmatch(r"\s*[❯>]\s*(?:Try [\"“].*)?", line) for line in text.splitlines())


_RESET = re.compile(r"^Resets\s+(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+)?"
                    r"(\d{1,2})(?::(\d{2}))?(am|pm)\s+\(([A-Za-z0-9_+\-/]+)\)$", re.IGNORECASE)
_MONTHS = {name.casefold(): number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def reset_interval(display, observed_at, window):
    """Return only a supported, unambiguous future minute, never guessed seconds."""
    match = _RESET.fullmatch(display.strip())
    if not match or window not in {"five_hour", "seven_day"}:
        return None
    month, day, hour, minute, period, name = match.groups()
    hour, minute = int(hour), int(minute or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    hour = hour % 12 + (12 if period.casefold() == "pm" else 0)
    try:
        zone = timezone.utc if name == "UTC" else ZoneInfo(name)
        observed = datetime.fromtimestamp(observed_at, zone)
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        return None
    if month:
        dates = []
        for year in (observed.year - 1, observed.year, observed.year + 1):
            try:
                dates.append(datetime(year, _MONTHS[month.casefold()], int(day), hour, minute))
            except ValueError:
                pass
    else:
        today = observed.replace(tzinfo=None, hour=hour, minute=minute, second=0, microsecond=0)
        dates = [today, today + timedelta(days=1)]
    maximum = 300 * 60 if window == "five_hour" else 10080 * 60
    candidates = []
    for local in dates:
        instants = set()
        for fold in (0, 1):
            instant = local.replace(tzinfo=zone, fold=fold).timestamp()
            if datetime.fromtimestamp(instant, zone).replace(tzinfo=None) == local:
                instants.add(int(instant))
        # Reject ambiguous/nonexistent local times, even if one fold looks plausible.
        if len(instants) != 1:
            continue
        start = instants.pop()
        if start + 60 > observed_at and start - observed_at <= maximum:
            candidates.append(start)
    if not candidates:
        return None
    start = min(candidates)
    return {"start": start, "end": start + 60, "precision_seconds": 60}


_PERCENT = re.compile(r"(?:[█▉▊▋▌▍▎▏░▒▓─━▫▪■□=#\[\]|-]+\s+)?([0-9]{1,3})%\s+used", re.ASCII)


def parse_rows(text, observed_at):
    """Recognize the two official account labels, not historical/global numbers."""
    lines = [line.strip() for line in text.splitlines()]
    result = []
    for label, window, duration in WINDOWS:
        indices = [index for index, line in enumerate(lines) if line == label]
        if len(indices) != 1:
            continue
        block = []
        invalid_block = False
        for line in lines[indices[0] + 1:indices[0] + 8]:
            if line.startswith("Local "):
                break
            if line.casefold().startswith(("context", "local", "session cost", "tokens", "cost")):
                invalid_block = True
                break
            if line.startswith(("Current ", "Extra usage", "Session ")):
                break
            block.append(line)
        if invalid_block:
            continue
        values = [int(match.group(1)) for line in block if (match := _PERCENT.fullmatch(line))]
        if len(values) != 1 or values[0] > 100:
            continue
        reset = [line for line in block if line.startswith("Resets ")]
        interval = reset_interval(reset[0], observed_at, window) if len(reset) == 1 else None
        result.append({"window": window, "used_percent": values[0], "window_minutes": duration,
                       "reset_interval": interval})
    return result


class FreshUsage:
    """Require a visible refresh cycle and then a stable current-screen result."""
    def __init__(self, stable_seconds=0.8):
        self.stable_seconds = stable_seconds
        self.refresh_started = False
        self.refresh_cleared = False
        self.signature = None
        self.stable_since = None
        self.result = None
        self.incomplete = False

    def observe(self, text, *, monotonic, wall_time, complete=True):
        self.incomplete = not complete
        try:
            reject_unsafe_screen(text)
        except ProbeError:
            self.signature = self.stable_since = self.result = None
            raise
        if re.search(r"\bRefreshing(?:[.\u2026]|\b)", text, re.IGNORECASE):
            self.refresh_started = True
            self.refresh_cleared = False
            self.signature = self.stable_since = None
            self.result = None
            return None
        if not complete:
            self.signature = self.stable_since = self.result = None
            return None
        if not self.refresh_started:
            return None
        self.refresh_cleared = True
        rows = parse_rows(text, wall_time)
        if not rows:
            self.signature = self.stable_since = None
            self.result = None
            return None
        signature = json.dumps(rows, sort_keys=True, separators=(",", ":"))
        if signature != self.signature:
            self.signature, self.stable_since = signature, monotonic
            self.result = None
            return None
        if monotonic - self.stable_since < self.stable_seconds:
            return None
        if self.result is None:
            metrics = [{"window": row["window"], "used_percent": row["used_percent"],
                        "resets_at": None, "window_minutes": row["window_minutes"]} for row in rows]
            self.result = ProbeResult(
                {"version": 1, "scope": "account", "provider": "claude", "source": "claude_code",
                 "fetched_at": int(wall_time), "metrics": metrics},
                {"refresh_started": True, "refresh_completed": True, "stable_final_rows": True,
                 "reset_intervals": {row["window"]: row["reset_interval"] for row in rows}},
            )
        return self.result

    def failure_code(self):
        if self.incomplete:
            return "freshness_unverified"
        return "no_usable_windows" if self.refresh_started and self.refresh_cleared else "freshness_unverified"


class Terminal:
    """Small bounded incremental terminal, with no scrollback or raw transcript.

    Notifications before cursor/erase controls preserve a short Refreshing frame
    even when multiple native renders arrive in a single read(). OSC/DCS payloads
    never become screen text. Unknown screen mutations fail closed.
    """
    def __init__(self, rows=80, columns=200):
        if type(rows) is not int or type(columns) is not int or not 1 <= rows <= 100 or not 1 <= columns <= 240:
            raise ValueError("invalid terminal bounds")
        self.rows, self.columns = rows, columns
        self.grid = [[" "] * columns for _ in range(rows)]
        self.row = self.column = 0
        self.saved = (0, 0)
        self.top, self.bottom = 0, rows - 1
        self.state, self.control = "text", ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.dirty = False
        self.alternate = None
        self.last_char = " "
        self.generation = 0
        self.synchronized = False
        self.committed_text = None

    def text(self):
        if self.synchronized:
            return self.committed_text
        return "\n".join("".join(row).rstrip() for row in self.grid).rstrip()

    def complete(self):
        return self.state == "text" and not self.decoder.getstate()[0] and not self.synchronized

    def _notify(self, observe):
        if self.synchronized:
            return
        if observe is not None and self.dirty:
            observe(self.text())
        self.dirty = False

    def _scroll(self, direction=1):
        if direction == 1:
            del self.grid[self.top]
            self.grid.insert(self.bottom, [" "] * self.columns)
        else:
            del self.grid[self.bottom]
            self.grid.insert(self.top, [" "] * self.columns)
        self.dirty = True

    def _linefeed(self):
        if self.row == self.bottom:
            self._scroll()
        else:
            self.row = min(self.rows - 1, self.row + 1)

    def _put(self, char):
        if unicodedata.combining(char):
            if self.column and len(self.grid[self.row][self.column - 1]) < 8:
                self.grid[self.row][self.column - 1] += char
                self.dirty = True
                self.generation += 1
            return
        if unicodedata.category(char).startswith("C"):
            return
        width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if self.column >= self.columns or self.column + width > self.columns:
            self.column = 0
            self._linefeed()
        self.grid[self.row][self.column] = char
        if width == 2:
            self.grid[self.row][self.column + 1] = ""
        self.column += width
        self.dirty = True
        self.generation += 1
        self.last_char = char

    def _erase_line(self, mode):
        start, end = ((self.column, self.columns) if mode == 0 else
                      (0, min(self.columns, self.column + 1)) if mode == 1 else (0, self.columns))
        self.grid[self.row][start:end] = [" "] * (end - start)
        self.dirty = True

    def _csi(self, value, final):
        # Observed version/keyboard status queries: no mutation or metadata reply.
        if value == ">0" and final == "q" or value == "?" and final == "u":
            return
        private = value.startswith("?")
        if private:
            value = value[1:]
        if not re.fullmatch(r"[0-9;: ]*", value):
            raise ProbeError("terminal_unsupported")
        # Style parameters can contain colon subfields; they never affect text.
        if final == "m":
            return
        if ":" in value or " " in value:
            raise ProbeError("terminal_unsupported")
        values = [min(int(part or 0), 1000000) for part in value.split(";")]
        count = values[0] or 1
        if private:
            # 2031 requests color-scheme notifications; it changes no screen cells.
            if final not in "hl" or any(v not in {1, 7, 25, 1000, 1002, 1003, 1004, 1006, 1049, 2004, 2026, 2031} for v in values):
                raise ProbeError("terminal_unsupported")
            if 2026 in values:
                if final == "h" and not self.synchronized:
                    self.committed_text = self.text()
                    self.synchronized = True
                elif final == "l":
                    self.synchronized = False
                    self.committed_text = None
                    self.dirty = True
            if 1049 in values:
                if final == "h" and self.alternate is None:
                    self.alternate = (self.grid, self.row, self.column)
                    self.grid = [[" "] * self.columns for _ in range(self.rows)]
                    self.row = self.column = 0
                elif final == "l" and self.alternate is not None:
                    self.grid, self.row, self.column = self.alternate
                    self.alternate = None
                self.dirty = True
            return
        if final in "Hf":
            self.row = min(self.rows - 1, max(0, (values[0] or 1) - 1))
            self.column = min(self.columns - 1, max(0, ((values[1] if len(values) > 1 else 1) or 1) - 1))
        elif final == "A": self.row = max(self.top, self.row - count)
        elif final == "B": self.row = min(self.bottom, self.row + count)
        elif final == "C": self.column = min(self.columns - 1, self.column + count)
        elif final == "D": self.column = max(0, self.column - count)
        elif final == "E": self.row, self.column = min(self.bottom, self.row + count), 0
        elif final == "F": self.row, self.column = max(self.top, self.row - count), 0
        elif final in "G`": self.column = min(self.columns - 1, count - 1)
        elif final == "d": self.row = min(self.rows - 1, count - 1)
        elif final == "K": self._erase_line(values[0])
        elif final == "J":
            mode = values[0]
            if mode not in (0, 1, 2, 3): raise ProbeError("terminal_unsupported")
            if mode in (2, 3):
                self.grid = [[" "] * self.columns for _ in range(self.rows)]
            elif mode == 0:
                self._erase_line(0)
                for row in range(self.row + 1, self.rows): self.grid[row] = [" "] * self.columns
            else:
                self._erase_line(1)
                for row in range(self.row): self.grid[row] = [" "] * self.columns
            self.dirty = True
        elif final == "s": self.saved = (self.row, self.column)
        elif final == "u": self.row, self.column = self.saved
        elif final == "r":
            top = max(0, (values[0] or 1) - 1)
            bottom = min(self.rows - 1, ((values[1] if len(values) > 1 else self.rows) or self.rows) - 1)
            if top >= bottom: raise ProbeError("terminal_unsupported")
            self.top, self.bottom, self.row, self.column = top, bottom, 0, 0
        elif final in "ST":
            for _ in range(min(count, self.rows)): self._scroll(1 if final == "S" else -1)
        elif final in "LMP@X":
            count = min(count, self.rows if final in "LM" else self.columns - min(self.column, self.columns - 1))
            column = min(self.column, self.columns - 1)
            if final in "LM":
                for _ in range(count):
                    if final == "L":
                        del self.grid[self.bottom]; self.grid.insert(self.row, [" "] * self.columns)
                    else:
                        del self.grid[self.row]; self.grid.insert(self.bottom, [" "] * self.columns)
            elif final == "P":
                del self.grid[self.row][column:column + count]; self.grid[self.row].extend([" "] * count)
            elif final == "@":
                self.grid[self.row][column:column] = [" "] * count
                del self.grid[self.row][self.columns:]
            else: self.grid[self.row][column:column + count] = [" "] * count
            self.dirty = True
        elif final == "b":
            for _ in range(min(count, self.columns)): self._put(self.last_char)
        elif final in "nc": pass  # terminal queries do not alter the screen
        else: raise ProbeError("terminal_unsupported")

    def feed(self, data, observe=None):
        try:
            text = self.decoder.decode(data)
        except UnicodeError:
            raise ProbeError("terminal_invalid") from None
        for char in text:
            if self.state == "text":
                if char in "\x1b\r\n\b\t":
                    self._notify(observe)
                if char == "\x1b": self.state = "escape"
                elif char == "\r": self.column = 0
                elif char == "\n": self._linefeed()
                elif char == "\b": self.column = max(0, self.column - 1)
                elif char == "\t": self.column = min(self.columns - 1, (self.column // 8 + 1) * 8)
                elif char not in "\x00\x07\x7f": self._put(char)
            elif self.state == "escape":
                if char == "[": self.state, self.control = "csi", ""
                elif char in "]P^_": self.state, self.control = "string", ""
                elif char in "()": self.state = "charset"
                elif char == "7": self.saved, self.state = (self.row, self.column), "text"
                elif char == "8": (self.row, self.column), self.state = self.saved, "text"
                elif char in "DE":
                    self._linefeed()
                    if char == "E": self.column = 0
                    self.state = "text"
                elif char == "M":
                    if self.row == self.top: self._scroll(-1)
                    else: self.row = max(0, self.row - 1)
                    self.state = "text"
                elif char == "c":
                    self.grid = [[" "] * self.columns for _ in range(self.rows)]
                    self.row = self.column = 0
                    self.state, self.dirty = "text", True
                else: raise ProbeError("terminal_unsupported")
            elif self.state == "charset": self.state = "text"
            elif self.state == "csi":
                if "@" <= char <= "~":
                    self._csi(self.control, char)
                    self.state, self.control = "text", ""
                else:
                    self.control += char
                    if len(self.control) > 128: raise ProbeError("terminal_limit")
            elif self.state == "string":
                if char == "\x07": self.state, self.control = "text", ""
                elif char == "\x1b": self.state = "string_escape"
                else:
                    self.control += char
                    if len(self.control) > 4096: raise ProbeError("terminal_limit")
            elif self.state == "string_escape":
                if char == "\\": self.state, self.control = "text", ""
                else:
                    self.state = "string"
                    self.control += "\x1b" + char
                    if len(self.control) > 4096: raise ProbeError("terminal_limit")
        self._notify(observe)


def native_dollar_prompt(terminal, *, owned_native_foreground=False):
    """Recognize only the observed owned AX/demo v2.1.299 empty input frame."""
    if owned_native_foreground is not True or not terminal.complete():
        return False
    text = terminal.text()
    try:
        reject_unsafe_screen(text)
    except ProbeError:
        return False
    if "```" in text or "~~~" in text or any(phrase in text.casefold() for phrase in (
            "gnu bash", "gnu bourne", "bash:", "zsh:", "sh:", "last login:",
            "welcome to ubuntu", "debian gnu/linux", "microsoft powershell")):
        return False
    lines = ["".join(row).rstrip() for row in terminal.grid]
    banners = [index for index, line in enumerate(lines) if line.strip(" ") == "Claude Code v2.1.299"]
    if len(banners) != 1 or banners[0] >= terminal.row:
        return False
    active = terminal.grid[terminal.row]
    if terminal.column != 1 or active[0] != "$" or any(cell != " " for cell in active[1:]):
        return False
    if any(line.strip() for line in lines[terminal.row + 1:]):
        return False
    return [index for index, line in enumerate(lines) if re.fullmatch(r" *\$ *", line)] == [terminal.row]


class CleanupPrompt:
    """Only bytes received after Esc can establish a fresh cleanup prompt."""
    def __init__(self, stable_seconds=0.06):
        self.terminal = Terminal()
        self.stable_seconds = stable_seconds
        self.signature = self.since = None
        self.invalid = self.ready = False

    def _observe(self, text, *, monotonic, complete):
        try:
            reject_unsafe_screen(text)
        except ProbeError:
            self.invalid = True
        safe = (complete and not self.invalid and self.terminal.generation > 0 and
                ordinary_prompt(text) and not any(label in text for label, _, _ in WINDOWS))
        if not safe:
            self.signature = self.since = None
            self.ready = False
        elif text != self.signature:
            self.signature, self.since = text, monotonic
            self.ready = False
        else:
            self.ready = monotonic - self.since >= self.stable_seconds
        return self.ready

    def feed(self, data, *, monotonic):
        try:
            self.terminal.feed(data, observe=lambda text: self._observe(
                text, monotonic=monotonic, complete=False))
        except ProbeError:
            self.invalid = True
        return self.observe(monotonic=monotonic)

    def observe(self, *, monotonic):
        return self._observe(self.terminal.text(), monotonic=monotonic,
                             complete=self.terminal.complete())


def _discard_queued(descriptor, *, max_bytes, deadline):
    """Discard bounded bytes already queued before Esc; never decode or retain."""
    count = 0
    while count < max_bytes and time.monotonic() < deadline:
        try:
            data = os.read(descriptor, min(4096, max_bytes - count))
        except BlockingIOError:
            return count, True
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return count, True
            return count, False
        if not data:
            return count, False
        count += len(data)
    return count, False


def child_environment():
    result = os.environ.copy()
    result.update(CLAUDE_CODE_SKIP_PROMPT_HISTORY="1", DISABLE_UPDATES="1", IS_DEMO="1", TERM="xterm-256color")
    result.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", None)
    return result


def _probe_command(command, *, limits=None):
    """Own one child and its PTY. The public entrypoint supplies a fixed command."""
    if not sys.platform.startswith("linux"):
        raise ProbeError("unsupported_platform")
    import fcntl
    import pty
    import struct
    import termios

    limits = limits or Limits()
    start = time.monotonic()
    hard_deadline = start + limits.total_seconds
    work_deadline = hard_deadline - limits.graceful_seconds - limits.terminate_seconds - 0.1
    startup_deadline = min(start + limits.startup_seconds, work_deadline)
    terminal = Terminal()
    machine = FreshUsage(limits.stable_seconds)
    child = None
    master = slave = None
    selector = None
    command_sent = False
    prompt_signature = prompt_since = None
    cleanup_model = None
    cleanup_parser_ok = True
    total_bytes = 0
    result = None
    failure = None
    cleanup_ok = True
    evidence = {"refresh_started": False, "refresh_completed": False, "stable_final_rows": False,
                "captured_bytes": 0, "child_reaped": False, "pty_closed": False,
                "graceful_exit": False, "terminated": False, "killed": False,
                "startup_prompt_stable": False, "cleanup_prompt_stable": False,
                "usage_command_sent": False, "cleanup_exit_sent": False,
                "pre_escape_queue_drained": False, "cleanup_post_escape_render": False}

    def observe(text, *, complete=False):
        nonlocal command_sent, result, prompt_signature, prompt_since
        now = time.monotonic()
        if now >= work_deadline:
            raise ProbeError(machine.failure_code() if command_sent else "startup_timeout")
        reject_unsafe_screen(text)
        if not command_sent:
            prompt_ready = ordinary_prompt(text)
            if complete and not prompt_ready and tuple(command) == NATIVE_COMMAND:
                try:
                    owned_foreground = os.tcgetpgrp(master) == child.pid
                except OSError:
                    owned_foreground = False
                prompt_ready = native_dollar_prompt(terminal, owned_native_foreground=owned_foreground)
            if complete and prompt_ready:
                if text != prompt_signature:
                    prompt_signature, prompt_since = text, now
                elif now - prompt_since >= 0.15:
                    os.write(master, b"/usage\r")
                    command_sent = True
                    evidence["startup_prompt_stable"] = True
                    evidence["usage_command_sent"] = True
            else:
                prompt_signature = prompt_since = None
            return
        candidate = machine.observe(text, monotonic=now, wall_time=time.time(), complete=complete)
        result = candidate if complete else None

    def drain_until(deadline, *, seek_prompt=False):
        """Bounded cleanup; only a visibly recovered prompt permits /exit text."""
        nonlocal total_bytes
        while child.poll() is None and time.monotonic() < deadline:
            remaining = limits.max_bytes - total_bytes
            if remaining <= 0:
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
                continue
            ready = selector.select(min(0.03, max(0, deadline - time.monotonic())))
            if ready:
                try:
                    data = os.read(master, min(4096, remaining))
                    total_bytes += len(data)
                    if not data: break
                    if seek_prompt and cleanup_parser_ok:
                        if cleanup_model.feed(data, monotonic=time.monotonic()):
                            return True
                except OSError:
                    break
            elif seek_prompt and cleanup_parser_ok:
                if cleanup_model.observe(monotonic=time.monotonic()):
                    return True
        return False

    try:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 80, 200, 0, 0))

        def controlling_tty():
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        child = subprocess.Popen(tuple(command), stdin=slave, stdout=slave, stderr=slave,
                                 close_fds=True, start_new_session=True, preexec_fn=controlling_tty,
                                 env=child_environment())
        os.close(slave)
        slave = None
        os.set_blocking(master, False)
        selector = selectors.DefaultSelector()
        selector.register(master, selectors.EVENT_READ)
        while result is None:
            now = time.monotonic()
            if now >= work_deadline:
                raise ProbeError(machine.failure_code() if command_sent else "startup_timeout")
            if not command_sent and now >= startup_deadline:
                raise ProbeError("startup_timeout")
            if selector.select(min(0.05, work_deadline - now)):
                try:
                    data = os.read(master, min(4096, limits.max_bytes - total_bytes))
                except OSError as error:
                    if error.errno == errno.EIO: data = b""
                    elif error.errno in (errno.EAGAIN, errno.EWOULDBLOCK): continue
                    else: raise ProbeError("io_failed") from None
                if not data:
                    raise ProbeError(machine.failure_code() if command_sent else "child_exited")
                total_bytes += len(data)
                if total_bytes >= limits.max_bytes:
                    raise ProbeError("output_limit")
                terminal.feed(data, observe=observe)
                observe(terminal.text(), complete=terminal.complete())
            else:
                observe(terminal.text(), complete=terminal.complete())
            if child.poll() is not None and result is None:
                raise ProbeError(machine.failure_code() if command_sent else "child_exited")
    except ProbeError as error:
        failure = error
    except KeyboardInterrupt:
        failure = ProbeError("interrupted")
    except (OSError, subprocess.SubprocessError, ValueError):
        failure = ProbeError("spawn_failed" if child is None else "io_failed")
    finally:
        evidence["refresh_started"] = machine.refresh_started
        if child is not None:
            try:
                if child.poll() is None and command_sent:
                    cleanup_deadline = min(time.monotonic() + limits.graceful_seconds, hard_deadline)
                    cleanup_parser_ok = failure is None or failure.code not in {
                        "terminal_invalid", "terminal_unsupported", "terminal_limit", "output_limit"}
                    discarded, drained = _discard_queued(
                        master, max_bytes=limits.max_bytes - total_bytes,
                        deadline=min(cleanup_deadline, time.monotonic() + 0.05))
                    total_bytes += discarded
                    evidence["pre_escape_queue_drained"] = drained
                    cleanup_parser_ok = cleanup_parser_ok and drained
                    os.write(master, b"\x1b")
                    cleanup_model = CleanupPrompt()
                    if drain_until(cleanup_deadline, seek_prompt=True):
                        evidence["cleanup_prompt_stable"] = True
                        evidence["cleanup_post_escape_render"] = True
                        os.write(master, b"/exit\r")
                        evidence["cleanup_exit_sent"] = True
                        drain_until(cleanup_deadline)
                    evidence["graceful_exit"] = child.poll() is not None
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    evidence["terminated"] = True
                    if selector is not None:
                        drain_until(min(time.monotonic() + limits.terminate_seconds, hard_deadline))
                    else:
                        try: child.wait(timeout=min(limits.terminate_seconds, max(0.001, hard_deadline - time.monotonic())))
                        except subprocess.TimeoutExpired: pass
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    evidence["killed"] = True
                child.wait(timeout=max(0.1, hard_deadline - time.monotonic()))
                evidence["child_reaped"] = True
            except (OSError, subprocess.SubprocessError):
                cleanup_ok = False
                # A graceful-write failure must still terminate and reap our exact child.
                try:
                    if child.poll() is None: os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=0.1)
                    evidence["child_reaped"] = True
                except (OSError, subprocess.SubprocessError): pass
        if selector is not None:
            selector.close()
        for descriptor in (master, slave):
            if descriptor is not None:
                try: os.close(descriptor)
                except OSError: cleanup_ok = False
        evidence["pty_closed"] = True
        evidence["captured_bytes"] = total_bytes
        evidence["elapsed_milliseconds"] = int((time.monotonic() - start) * 1000)
    if not cleanup_ok or child is not None and not evidence["child_reaped"]:
        failure = ProbeError("cleanup_failed")
    if failure is not None:
        failure.evidence = evidence
        raise failure from None
    if result is None:
        raise ProbeError("freshness_unverified", evidence)
    result.evidence = {**evidence, **result.evidence}
    return result


def probe():
    return _probe_command(NATIVE_COMMAND)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Official Claude /usage numeric dry run; Linux existing login only.")
    parser.add_argument("--count", type=int, default=1, help="number of probes; 0 continues until interrupted")
    parser.add_argument("--interval", type=float, default=120.0, help="seconds between completed probes, minimum 60")
    args = parser.parse_args(argv)
    if args.count < 0 or not math.isfinite(args.interval) or args.interval < 60:
        parser.error("count must be nonnegative and interval must be finite and at least 60 seconds")
    completed, failed = 0, False
    try:
        while args.count == 0 or completed < args.count:
            try:
                result = probe()
                print(json.dumps(result.snapshot, separators=(",", ":"), allow_nan=False), flush=True)
            except ProbeError as error:
                failed = True
                print("claude_usage: " + error.code, file=sys.stderr, flush=True)
                if error.code == "interrupted":
                    return 130
                # Consent/auth/unknown-command conditions must stop a repeated dry run.
                if error.code in {"setup_required", "login_required", "trust_required", "unknown_command"}:
                    return 1
            completed += 1
            if args.count == 0 or completed < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
