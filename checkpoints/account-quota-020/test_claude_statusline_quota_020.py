"""Offline contract tests; synthetic data establishes no native freshness."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import unittest


TARGET = Path(__file__).with_name("claude_statusline_quota_020.py")
CONTRACT = "claude_code_statusline_v1"
NOW = 1_800_000_000


def event(i=10, o=20, session="synthetic-session", **extra):
    value = {
        "session_id": session,
        "context_window": {"total_input_tokens": i, "total_output_tokens": o},
        "rate_limits": {
            "five_hour": {"used_percentage": 0, "resets_at": NOW + 3600},
            "seven_day": {"used_percentage": 23.5, "resets_at": NOW + 86400},
        },
    }
    value.update(extra)
    return value


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Missing implementation is an explicit RED assertion, not an import error.
        if TARGET.exists():
            spec = importlib.util.spec_from_file_location("candidate", TARGET)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def adapt(self, value, state=None, at=NOW, binding="opaque-test-binding", contract=CONTRACT):
        self.assertTrue(TARGET.exists(), "passive statusline adapter has not been implemented")
        return self.module.adapt_statusline(value, state, at, binding, contract)

    def baseline(self):
        result = self.adapt(event())
        self.assertEqual(result["status"], "baseline")
        self.assertIsNone(result["snapshot"])
        return result["state"]

    def fresh(self):
        result = self.adapt(event(11, 22), self.baseline(), NOW + 1)
        self.assertEqual(result["status"], "fresh")
        return result

    def test_first_event_is_only_baseline(self):
        self.baseline()

    def test_response_advance_normalizes_two_windows_and_zero(self):
        snap = self.fresh()["snapshot"]
        self.assertEqual({k: snap[k] for k in ("version", "scope", "provider", "source", "fetched_at")},
                         dict(version=1, scope="account", provider="claude", source="claude_code", fetched_at=NOW + 1))
        self.assertEqual(len(snap["metrics"]), 2)
        self.assertEqual(snap["metrics"][0]["used_percent"], 0)
        self.assertEqual(snap["metrics"][1]["used_percent"], 23.5)

    def test_input_or_output_counter_advance_is_sufficient(self):
        for i, o in ((11, 20), (10, 21)):
            with self.subTest(i=i, o=o):
                self.assertEqual(self.adapt(event(i, o), self.baseline(), NOW + 1)["status"], "fresh")

    def test_replay_timer_duration_quota_change_never_restamp(self):
        good = self.fresh()
        for value in (event(11, 22), event(11, 22, cost={"total_duration_ms": 999999}),
                      event(11, 22, rate_limits={"five_hour": {"used_percentage": 99, "resets_at": NOW + 7200}}),
                      None, {"type": "timer"}, event(12, 23, cached=True),
                      event(12, 23, last_known=True), event(12, 23, error=True),
                      event(12, 23, is_error=True), event(12, 23, type="error")):
            with self.subTest(value=value):
                result = self.adapt(value, good["state"], NOW + 20)
                self.assertNotEqual(result["status"], "fresh")
                if result["snapshot"] is not None:
                    self.assertEqual(result["status"], "cached")
                    self.assertEqual(result["snapshot"]["fetched_at"], NOW + 1)

    def test_regression_does_not_lower_high_water(self):
        good = self.fresh()
        regressed = self.adapt(event(5, 6), good["state"], NOW + 2)
        repeated = self.adapt(event(10, 20), regressed["state"], NOW + 3)
        self.assertNotEqual(regressed["status"], "fresh")
        self.assertNotEqual(repeated["status"], "fresh")

    def test_error_payload_is_not_successful_response_evidence(self):
        for marker in ({"error": {"message": "private-error"}}, {"error": "failure"},
                       {"status": "error"}, {"status": "failed"}):
            result = self.adapt(event(11, 21, **marker), self.baseline(), NOW + 1)
            self.assertNotEqual(result["status"], "fresh", marker)

    def test_nonfresh_counter_advance_cannot_be_replayed_as_new_response(self):
        for marker in ({"cached": True}, {"last_known": True}, {"type": "timer"},
                       {"error": True}, {"rate_limits": {}}, {"rate_limits": None}):
            blocked = self.adapt(event(11, 21, **marker), self.baseline(), NOW + 1)
            replay = self.adapt(event(11, 21), blocked["state"], NOW + 2)
            self.assertNotEqual(blocked["status"], "fresh", marker)
            self.assertNotEqual(replay["status"], "fresh", marker)

    def test_missing_and_malformed_state_cannot_return_private_metadata(self):
        base = self.baseline()
        for value in (None, {}, [], "private", {"unexpected": "PRIVATE_DATA"}):
            result = self.adapt(event(11, 21), value, NOW + 1)
            self.assertNotEqual(result["status"], "fresh")
            self.assertNotIn("PRIVATE_DATA", json.dumps(result))
        corrupted = copy.deepcopy(base)
        corrupted["last_good"] = {"PRIVATE_DATA": "never return"}
        self.assertEqual(self.adapt(event(11, 21), corrupted, NOW + 1)["status"], "invalid")

    def test_session_and_binding_switch_require_new_baseline(self):
        good = self.fresh()
        for value, binding in ((event(100, 200, "another-session"), "opaque-test-binding"),
                               (event(100, 200), "another-binding")):
            with self.subTest(binding=binding):
                result = self.adapt(value, good["state"], NOW + 2, binding)
                self.assertEqual(result["status"], "baseline")
                self.assertIsNone(result["snapshot"])

    def test_missing_windows_are_independently_unknown(self):
        for missing, kept in (("five_hour", 1), ("seven_day", 0)):
            value = event(11, 21)
            del value["rate_limits"][missing]
            result = self.adapt(value, self.baseline(), NOW + 1)
            metrics = result["snapshot"]["metrics"]
            self.assertIsNone(metrics[1 - kept]["used_percent"])
            self.assertIsNotNone(metrics[kept]["used_percent"])

    def test_invalid_percentage_and_reset_remain_unknown(self):
        for bad in (True, False, "0", -1, 101, math.nan, math.inf, -math.inf, None, [], {}):
            for field in ("used_percentage", "resets_at"):
                value = event(11, 21)
                value["rate_limits"]["five_hour"][field] = bad
                metrics = self.adapt(value, self.baseline(), NOW + 1)["snapshot"]["metrics"]
                self.assertIsNone(metrics[0]["used_percent"], (field, bad))
                self.assertEqual(metrics[1]["used_percent"], 23.5)
        for bad in (NOW, NOW - 1, NOW + 1, NOW + 1.5):
            value = event(11, 21)
            value["rate_limits"]["five_hour"]["resets_at"] = bad
            self.assertIsNone(self.adapt(value, self.baseline(), NOW + 1)["snapshot"]["metrics"][0]["used_percent"])

    def test_cached_expired_window_is_unknown_without_restamp(self):
        good = self.fresh()
        result = self.adapt(event(11, 22), good["state"], NOW + 4000)
        self.assertEqual(result["status"], "cached")
        self.assertEqual(result["snapshot"]["fetched_at"], NOW + 1)
        self.assertIsNone(result["snapshot"]["metrics"][0]["used_percent"])
        self.assertEqual(result["snapshot"]["metrics"][1]["used_percent"], 23.5)

    def test_invalid_counter_timestamp_binding_contract_and_bounds(self):
        base = self.baseline()
        for bad in (-1, True, 1.5, "11", None, math.inf, 2**63):
            self.assertNotEqual(self.adapt(event(bad, 21), base, NOW + 1)["status"], "fresh")
        for at in (True, -1, math.nan, NOW - 1, "1800000000"):
            self.assertNotEqual(self.adapt(event(11, 21), base, at)["status"], "fresh")
        for binding in (None, "", True, "x" * 257):
            self.assertEqual(self.adapt(event(), binding=binding)["status"], "invalid")
        self.assertEqual(self.adapt(event(), contract="other-source")["status"], "invalid")
        self.assertEqual(self.adapt(event(session="x" * 257))["status"], "invalid")
        self.assertEqual(self.adapt({str(i): i for i in range(65)})["status"], "invalid")
        self.assertEqual(self.adapt(event(), {str(i): i for i in range(65)})["status"], "invalid")

    def test_metadata_discard_bounded_state_and_no_mutation(self):
        value = event(11, 21, transcript_path="NEVER_READ_TRANSCRIPT", cwd="PRIVATE_PATH",
                      workspace={"secret": "PRIVATE_WORKSPACE"}, prompt="PRIVATE_PROMPT",
                      model={"id": "PRIVATE_MODEL"}, account="PRIVATE_ACCOUNT")
        original = copy.deepcopy(value)
        baseline = self.baseline()
        oldstate = copy.deepcopy(baseline)
        result = self.adapt(value, baseline, NOW + 1)
        serialized = json.dumps(result, allow_nan=False)
        for private in ("synthetic-session", "opaque-test-binding", "NEVER_READ_TRANSCRIPT", "PRIVATE_"):
            self.assertNotIn(private, serialized)
        self.assertLess(len(serialized), 3000)
        self.assertEqual(value, original)
        self.assertEqual(baseline, oldstate)
        self.assertEqual(set(result), {"status", "snapshot", "state"})
        self.assertEqual(set(result["snapshot"]), {"version", "scope", "provider", "source", "fetched_at", "metrics"})
        for metric in result["snapshot"]["metrics"]:
            self.assertEqual(set(metric), {"window", "window_minutes", "used_percent", "resets_at"})
        for _ in range(100):
            result = self.adapt(value, result["state"], NOW + 2)
        self.assertLess(len(json.dumps(result["state"])), 2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
