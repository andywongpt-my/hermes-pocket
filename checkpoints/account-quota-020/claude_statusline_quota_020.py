"""Passive, offline Claude Code statusline candidate. No I/O or installation.

The caller supplies ordered official-client events, an opaque verified account
binding, and a trusted observation epoch. Token totals are documented as updated
after successful responses. This adapter does not authenticate supplied events,
verify an account, observe Hermes requests, or establish real native freshness.
"""
import hashlib
import math


SOURCE_CONTRACT = "claude_code_statusline_v1"
MAX_EPOCH = 253402300799
MAX_COUNTER = 2**63 - 1
WINDOWS = (("five_hour", "5h", 300), ("seven_day", "7d", 10080))
STATE_KEYS = {"version", "binding_hash", "session_hash", "input_tokens",
              "output_tokens", "observed_at", "last_good"}
SNAPSHOT_KEYS = {"version", "scope", "provider", "source", "fetched_at", "metrics"}
METRIC_KEYS = {"window", "window_minutes", "used_percent", "resets_at"}


def _integer(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def _percentage(value):
    return (type(value) in (int, float) and 0 <= value <= 100
            and math.isfinite(value))


def _opaque(value):
    return type(value) is str and 1 <= len(value) <= 256 and not any(
        ord(char) < 32 for char in value)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _hash(value):
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _metrics(limits, at):
    limits = limits if type(limits) is dict and len(limits) <= 32 else {}
    rows = []
    for source, window, minutes in WINDOWS:
        value = limits.get(source)
        value = value if type(value) is dict and len(value) <= 8 else {}
        used, reset = value.get("used_percentage"), value.get("resets_at")
        valid_reset = _integer(reset, MAX_EPOCH) and reset > at
        rows.append({"window": window, "window_minutes": minutes,
                     "used_percent": used if _percentage(used) and valid_reset else None,
                     "resets_at": reset if valid_reset else None})
    return rows


def _snapshot(rows, at):
    return {"version": 1, "scope": "account", "provider": "claude",
            "source": "claude_code", "fetched_at": at, "metrics": rows}


def _copy_snapshot(value, at):
    """Validate the tiny state snapshot and mask expired windows on output."""
    if (type(value) is not dict or len(value) != len(SNAPSHOT_KEYS) or set(value) != SNAPSHOT_KEYS
            or type(value.get("version")) is not int or value["version"] != 1
            or value["scope"] != "account" or value["provider"] != "claude"
            or value["source"] != "claude_code"
            or not _integer(value["fetched_at"], MAX_EPOCH)
            or value["fetched_at"] > at
            or type(value["metrics"]) is not list or len(value["metrics"]) != 2):
        return None
    rows = []
    for row, (_, window, minutes) in zip(value["metrics"], WINDOWS):
        if (type(row) is not dict or len(row) != len(METRIC_KEYS) or set(row) != METRIC_KEYS
                or row["window"] != window or type(row["window_minutes"]) is not int
                or row["window_minutes"] != minutes
                or row["used_percent"] is not None and not _percentage(row["used_percent"])
                or row["resets_at"] is not None and not _integer(row["resets_at"], MAX_EPOCH)
                or row["used_percent"] is not None and row["resets_at"] is None):
            return None
        current = row["resets_at"] is not None and row["resets_at"] > at
        rows.append({"window": window, "window_minutes": minutes,
                     "used_percent": row["used_percent"] if current else None,
                     "resets_at": row["resets_at"] if current else None})
    return _snapshot(rows, value["fetched_at"])


def _state(value):
    if (type(value) is not dict or len(value) != len(STATE_KEYS) or set(value) != STATE_KEYS
            or type(value["version"]) is not int or value["version"] != 1
            or not _hash(value["binding_hash"]) or not _hash(value["session_hash"])
            or not _integer(value["input_tokens"], MAX_COUNTER)
            or not _integer(value["output_tokens"], MAX_COUNTER)
            or not _integer(value["observed_at"], MAX_EPOCH)):
        return None
    result = {key: value[key] for key in STATE_KEYS}
    if value["last_good"] is not None:
        result["last_good"] = _copy_snapshot(value["last_good"], value["observed_at"])
        if result["last_good"] is None:
            return None
    return result


def _result(status, state):
    cached = None if state is None or state["last_good"] is None else _copy_snapshot(
        state["last_good"], state["observed_at"])
    return {"status": "cached" if cached is not None else status,
            "snapshot": cached, "state": state}


def adapt_statusline(event, state, observed_at, account_binding, source_contract):
    """Return {status, snapshot, state}; all returned objects are independent.

    Pass None state initially. Preserve returned state only within this explicit
    binding. First/session-switch events establish baselines. A later monotonic
    token advance may emit fresh quota; cached/error events consume the counter
    advance without making it fresh, preventing subsequent replay laundering.
    Cached outputs retain their original fetched_at. Absent/malformed events
    never provide response evidence. All metadata and referenced paths are ignored.
    """
    if (source_contract != SOURCE_CONTRACT or not _opaque(account_binding)
            or not _integer(observed_at, MAX_EPOCH)):
        return _result("invalid", None)
    previous = _state(state) if state is not None else None
    if state is not None and previous is None:
        return _result("invalid", None)
    binding = _digest(account_binding)
    if previous is not None and previous["binding_hash"] != binding:
        previous = None
    if previous is not None and observed_at < previous["observed_at"]:
        return _result("invalid", previous)
    if previous is not None:
        previous["observed_at"] = observed_at
    if event is None:
        return _result("unknown", previous)
    if type(event) is not dict or len(event) > 64:
        return _result("invalid", previous)
    session = event.get("session_id")
    counters = event.get("context_window")
    if (not _opaque(session) or type(counters) is not dict or len(counters) > 32
            or not _integer(counters.get("total_input_tokens"), MAX_COUNTER)
            or not _integer(counters.get("total_output_tokens"), MAX_COUNTER)):
        return _result("invalid", previous)
    session_hash = _digest(session)
    inputs, outputs = counters["total_input_tokens"], counters["total_output_tokens"]
    if previous is None or previous["session_hash"] != session_hash:
        baseline = {"version": 1, "binding_hash": binding, "session_hash": session_hash,
                    "input_tokens": inputs, "output_tokens": outputs,
                    "observed_at": observed_at, "last_good": None}
        return _result("baseline", baseline)
    before = previous["input_tokens"], previous["output_tokens"]
    # Preserve independent high-water marks even on mixed advances/regressions.
    previous["input_tokens"] = max(inputs, before[0])
    previous["output_tokens"] = max(outputs, before[1])
    advanced = inputs >= before[0] and outputs >= before[1] and (inputs, outputs) != before
    blocked = (any(event.get(key) not in (None, False)
                   for key in ("cached", "last_known", "error", "is_error"))
               or event.get("type") in ("timer", "error", "cached")
               or event.get("status") in ("error", "failed"))
    if not advanced or blocked or observed_at == state["observed_at"]:
        return _result("unknown", previous)
    rows = _metrics(event.get("rate_limits"), observed_at)
    if not any(row["used_percent"] is not None for row in rows):
        return _result("unknown", previous)
    snapshot = _snapshot(rows, observed_at)
    previous["last_good"] = _copy_snapshot(snapshot, observed_at)
    return {"status": "fresh", "snapshot": snapshot, "state": previous}
