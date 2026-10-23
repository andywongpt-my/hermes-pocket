"""Offline callback boundary tests. All events and filesystem roots are synthetic."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TARGET = Path(__file__).with_name('claude_callback_020.py')
NOW = 1800000000
BINDING = 'synthetic-opaque-binding'


def event(i=10, o=20, session='synthetic-session', **extra):
    result = {'session_id': session, 'context_window': {
        'total_input_tokens': i, 'total_output_tokens': o},
        'rate_limits': {'five_hour': {'used_percentage': 0, 'resets_at': NOW + 3600},
                        'seven_day': {'used_percentage': 23.5, 'resets_at': NOW + 86400}}}
    result.update(extra)
    return result


class CallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TARGET.exists():
            spec = importlib.util.spec_from_file_location('callback020', TARGET)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.assertTrue(TARGET.exists(), 'callback has not been implemented')
        self.tmp = tempfile.TemporaryDirectory(dir=TARGET.parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / '.callback-state'
        self.output_dir = self.root / '.fixture.sqlite.account-quota-v1'
        self.state_dir.mkdir(mode=0o700)
        self.output_dir.mkdir(mode=0o700)
        self.state = self.state_dir / 'claude-state.json'
        self.output = self.output_dir / 'claude.json'

    def call(self, value=None, at=NOW, binding=BINDING, raw=None):
        if raw is None:
            raw = json.dumps(event() if value is None else value).encode()
        return self.module.consume(io.BytesIO(raw), state_directory=self.state_dir,
                                   output=self.output, account_binding=binding,
                                   observed_at=at)

    def baseline(self):
        self.assertEqual(self.call()['status'], 'baseline')
        self.assertFalse(self.output.exists())

    def fresh(self):
        self.baseline()
        result = self.call(event(11, 22), NOW + 1)
        self.assertEqual(result['status'], 'fresh')
        return json.loads(self.output.read_bytes())

    def test_baseline_then_response_publishes_numeric_original_timestamp(self):
        snap = self.fresh()
        self.assertEqual(snap['fetched_at'], NOW + 1)
        self.assertEqual(snap['metrics'][0]['used_percent'], 0)
        self.assertEqual(snap['metrics'][1]['used_percent'], 23.5)
        self.assertEqual(set(snap), {'version', 'scope', 'provider', 'source', 'fetched_at', 'metrics'})
        self.assertEqual([x.name for x in self.output_dir.iterdir()], ['claude.json'])
        self.assertEqual([x.name for x in self.state_dir.iterdir()], ['claude-state.json'])
        if os.name == 'posix':
            self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_replay_timer_duration_error_and_absent_quota_do_not_restamp(self):
        before = self.fresh()
        for at, value in enumerate((event(11, 22), event(12, 23, type='timer'),
                                   event(12, 23, cost={'total_duration_ms': 4000}),
                                   event(13, 24, error={'message': 'synthetic-private'}),
                                   event(13, 24), event(14, 25, rate_limits=None),
                                   event(14, 25)), NOW + 2):
            self.assertNotEqual(self.call(value, at)['status'], 'fresh')
            self.assertEqual(json.loads(self.output.read_bytes()), before)

    def test_metadata_paths_session_and_binding_never_persist_or_open(self):
        marker = 'synthetic-DO-NOT-PERSIST'
        forbidden = self.root / 'never-read-transcript'
        forbidden.write_text(marker)
        base = event(transcript_path=str(forbidden), cwd=marker, model={'id': marker})
        self.call(base)
        self.call(event(11, 21, transcript_path=str(forbidden), workspace={'path': marker}), NOW + 1)
        state_raw, output_raw = self.state.read_bytes(), self.output.read_bytes()
        for value in (marker, str(forbidden), BINDING, 'synthetic-session'):
            self.assertNotIn(value.encode(), state_raw + output_raw)
        state = json.loads(state_raw)
        self.assertEqual(set(state), self.module.adapter.STATE_KEYS)
        self.assertEqual(len(state['session_hash']), 64)
        # Any attempted path access to input metadata is an immediate failure.
        original = self.module.os.open
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(str(path), str(forbidden))
            return original(path, *args, **kwargs)
        with patch.object(self.module.os, 'open', guarded):
            self.call(event(12, 22, transcript_path=str(forbidden)), NOW + 2)

    def test_strict_bounded_input_rejects_without_touching_state_or_output(self):
        self.fresh()
        previous = self.state.read_bytes(), self.output.read_bytes()
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'\xff',
                    b'{}{}', b'[]', b'null', b'x' * 65537, b'[' * 2000 + b']' * 2000):
            with self.subTest(raw=raw[:24]), self.assertRaises(ValueError):
                self.call(raw=raw, at=NOW + 2)
            self.assertEqual((self.state.read_bytes(), self.output.read_bytes()), previous)
        class Bounded(io.BytesIO):
            def read(inner, count=-1):
                self.assertEqual(count, 65537)
                return super().read(count)
        with self.assertRaises(ValueError):
            self.module.consume(Bounded(b'x' * 70000), state_directory=self.state_dir,
                output=self.output, account_binding=BINDING, observed_at=NOW + 2)

    def test_invalid_explicit_binding_and_paths_fail_before_input_or_writes(self):
        class Unreadable:
            def read(self, count):
                raise AssertionError('input was read before argument validation')
        for binding in ('', 'x' * 257, 'bad\nvalue', None):
            with self.assertRaises(ValueError):
                self.module.consume(Unreadable(), state_directory=self.state_dir,
                    output=self.output, account_binding=binding, observed_at=NOW)
        with self.assertRaises(ValueError):
            self.module.consume(Unreadable(), state_directory=self.output_dir,
                output=self.output, account_binding=BINDING, observed_at=NOW)
        self.assertEqual(list(self.state_dir.iterdir()), [])

    def test_corrupt_or_oversized_state_fails_closed_without_publication(self):
        self.fresh()
        before = self.output.read_bytes()
        for raw in (b'{"private":"do-not-echo"}', b'{}', b'x' * 16385,
                    b'{"version":1,"version":1}'):
            self.state.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.call(event(12, 23), NOW + 2)
            self.assertEqual(self.state.read_bytes(), raw)
            self.assertEqual(self.output.read_bytes(), before)

    def test_state_replace_failure_keeps_old_state_snapshot_and_cleans_temp(self):
        self.fresh()
        before = self.state.read_bytes(), self.output.read_bytes()
        original = self.module.os.replace
        def fail_state(src, dst, *args, **kwargs):
            if str(dst).endswith('claude-state.json'):
                raise OSError('synthetic-private-error')
            return original(src, dst, *args, **kwargs)
        with patch.object(self.module.os, 'replace', fail_state), self.assertRaises(OSError):
            self.call(event(12, 23), NOW + 2)
        self.assertEqual((self.state.read_bytes(), self.output.read_bytes()), before)
        self.assertEqual([x.name for x in self.state_dir.iterdir()], ['claude-state.json'])

    def test_failed_publication_consumes_advance_without_replay_retry(self):
        before = self.fresh()
        original = self.module.os.replace
        def fail_output(src, dst, *args, **kwargs):
            if str(dst).endswith('claude.json'):
                raise OSError('synthetic-private-publish-error')
            return original(src, dst, *args, **kwargs)
        with patch.object(self.module.os, 'replace', fail_output), self.assertRaises(OSError):
            self.call(event(12, 23), NOW + 2)
        self.assertEqual(json.loads(self.state.read_bytes())['input_tokens'], 12)
        self.assertEqual(json.loads(self.output.read_bytes()), before)
        self.assertEqual(self.call(event(12, 23), NOW + 3)['status'], 'cached')
        self.assertEqual(json.loads(self.output.read_bytes()), before)
        self.call(event(13, 24), NOW + 4)
        self.assertEqual(json.loads(self.output.read_bytes())['fetched_at'], NOW + 4)
        self.assertEqual([x.name for x in self.output_dir.iterdir()], ['claude.json'])

    def test_session_and_binding_switch_baseline_clear_old_snapshot(self):
        self.fresh()
        self.assertEqual(self.call(event(100, 200, session='new-session'), NOW + 2)['status'], 'baseline')
        self.assertFalse(self.output.exists())
        self.call(event(101, 201, session='new-session'), NOW + 3)
        self.assertTrue(self.output.exists())
        self.assertEqual(self.call(event(200, 300), NOW + 4, binding='other-opaque')['status'], 'baseline')
        self.assertFalse(self.output.exists())

    def test_invalidation_failure_is_retried_before_new_response(self):
        self.fresh()
        original = self.module.os.unlink
        def fail_output(path, *args, **kwargs):
            if str(path).endswith('claude.json'):
                raise OSError('synthetic-unlink-failure')
            return original(path, *args, **kwargs)
        with patch.object(self.module.os, 'unlink', fail_output), self.assertRaises(OSError):
            self.call(event(100, 200), NOW + 2, binding='other-opaque')
        self.assertIsNone(json.loads(self.state.read_bytes())['last_good'])
        self.assertTrue(self.output.exists())
        self.assertEqual(self.call(event(101, 201), NOW + 3, binding='other-opaque')['status'], 'fresh')
        self.assertEqual(json.loads(self.output.read_bytes())['fetched_at'], NOW + 3)

    def test_regression_high_water_same_tick_and_quota_change_never_launder(self):
        self.fresh()
        for value, at in ((event(20, 1), NOW + 2), (event(19, 23), NOW + 3),
                          (event(20, 23), NOW + 4), (event(21, 24), NOW + 4),
                          (event(21, 24), NOW + 5)):
            self.assertNotEqual(self.call(value, at)['status'], 'fresh')
        self.assertEqual(json.loads(self.output.read_bytes())['fetched_at'], NOW + 1)
        self.assertEqual(self.call(event(22, 25), NOW + 6)['status'], 'fresh')

    def test_cli_quiet_success_and_fixed_private_failure(self):
        args = ['--state-directory', str(self.state_dir), '--output', str(self.output),
                '--account-binding', BINDING]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.module.time, 'time', return_value=NOW):
            result = self.module.main(args, stdin=io.BytesIO(json.dumps(event()).encode()),
                                      stdout=stdout, stderr=stderr)
        self.assertEqual((result, stdout.getvalue(), stderr.getvalue()), (0, '', ''))
        result = self.module.main(args, stdin=io.BytesIO(b'{"private":"secret"}'),
                                  stdout=stdout, stderr=stderr)
        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), 'claude_quota_callback: invalid_input\n')
        self.assertEqual(stdout.getvalue(), '')


    def test_later_response_replaces_snapshot_and_missing_window_stays_unknown(self):
        self.fresh()
        value = event(12, 23, rate_limits={'seven_day': {
            'used_percentage': 40, 'resets_at': NOW + 86400}},
            fetched_at=NOW + 999, account_binding='ignored-event-identity')
        self.assertEqual(self.call(value, NOW + 2)['status'], 'fresh')
        snap = json.loads(self.output.read_bytes())
        self.assertEqual(snap['fetched_at'], NOW + 2)
        self.assertIsNone(snap['metrics'][0]['used_percent'])
        self.assertIsNone(snap['metrics'][0]['resets_at'])
        self.assertEqual(snap['metrics'][1]['used_percent'], 40)

    def test_older_callback_does_not_displace_newer_published_snapshot(self):
        before = self.fresh()
        before['fetched_at'] = NOW + 10
        self.module.publish_snapshot(before, self.output, clock=lambda: NOW + 10)
        with self.assertRaises(ValueError):
            self.call(event(12, 23), NOW + 2)
        self.assertEqual(json.loads(self.state.read_bytes())['input_tokens'], 12)
        self.assertEqual(json.loads(self.output.read_bytes())['fetched_at'], NOW + 10)
        self.assertEqual(self.call(event(12, 23), NOW + 3)['status'], 'cached')
        self.assertEqual(self.call(event(13, 24), NOW + 11)['status'], 'fresh')

    def test_state_flush_failure_leaves_original_files_and_no_temporary(self):
        self.fresh()
        before = self.state.read_bytes(), self.output.read_bytes()
        with patch.object(self.module.os, 'fsync', side_effect=OSError('fixture-fsync')):
            with self.assertRaises(OSError):
                self.call(event(12, 23), NOW + 2)
        self.assertEqual((self.state.read_bytes(), self.output.read_bytes()), before)
        self.assertEqual([x.name for x in self.state_dir.iterdir()], ['claude-state.json'])

    def test_clock_regression_preserves_original_snapshot_and_high_water(self):
        self.fresh()
        self.assertNotEqual(self.call(event(100, 200), NOW)['status'], 'fresh')
        self.assertEqual(json.loads(self.state.read_bytes())['input_tokens'], 11)
        self.assertEqual(json.loads(self.output.read_bytes())['fetched_at'], NOW + 1)

    def test_real_busy_output_lock_consumes_response_without_publishing_replay(self):
        import threading
        self.fresh()
        acquired, release = threading.Event(), threading.Event()
        failures = []
        def hold():
            try:
                with self.module.PrivateDirectory(self.output_dir) as directory:
                    with self.module._publication_lock(directory):
                        acquired.set()
                        if not release.wait(5):
                            raise AssertionError('fixture lock release timeout')
            except BaseException as error:
                failures.append(error)
                acquired.set()
        worker = threading.Thread(target=hold)
        worker.start()
        self.assertTrue(acquired.wait(5))
        try:
            if failures:
                raise failures[0]
            with self.assertRaises(ValueError):
                self.call(event(12, 23), NOW + 2)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(json.loads(self.state.read_bytes())['input_tokens'], 12)
        self.assertEqual(self.call(event(12, 23), NOW + 3)['status'], 'cached')
        self.assertEqual(json.loads(self.output.read_bytes())['fetched_at'], NOW + 1)

    @unittest.skipUnless(os.name == 'posix', 'POSIX uid/mode/symlink enforcement needs native Linux')
    def test_native_private_modes_and_symlink_rejected(self):
        self.baseline()
        self.state.chmod(0o644)
        with self.assertRaises(ValueError):
            self.call(event(11, 21), NOW + 1)
        self.state.chmod(0o600)
        self.state.unlink()
        elsewhere = self.root / 'elsewhere'
        elsewhere.write_text('{}')
        self.state.symlink_to(elsewhere)
        with self.assertRaises(ValueError):
            self.call(event(11, 21), NOW + 1)


if __name__ == '__main__':
    unittest.main()
