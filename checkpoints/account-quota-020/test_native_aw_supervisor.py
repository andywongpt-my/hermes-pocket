"""Offline Windows Job Object tests: synthetic Python subprocesses only."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('native_aw_supervisor', HERE / 'native_aw_supervisor.py')
s = None
if spec and Path(spec.origin).is_file():
    s = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = s
    spec.loader.exec_module(s)


@unittest.skipUnless(sys.platform == 'win32', 'Windows Job Objects required')
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(s, 'supervisor implementation is missing')

    def command(self, script):
        return [sys.executable, '-B', '-u', '-c', script]

    def assert_clean(self, proof):
        for key in ('job_empty', 'job_handle_closed', 'helper_process_handle_closed',
                    'all_pipes_closed', 'io_threads_stopped'):
            self.assertIs(proof[key], True, key)
        self.assertEqual(proof['active_processes'], 0)

    def test_success_waits_for_helper_and_child_and_closes_all_handles(self):
        script = "import subprocess,sys; c=subprocess.Popen([sys.executable,'-B','-c','pass']); c.wait(); print('SYNTHETIC_OK',flush=True)"
        output, proof = s.supervise(self.command(script), timeout=5)
        self.assertEqual(output, b'SYNTHETIC_OK\r\n')
        self.assertEqual(proof['helper_exit_code'], 0)
        self.assertEqual(proof['total_processes'], 2)
        self.assertTrue(proof['assigned_before_resume'])
        self.assertTrue(proof['resumed'])
        self.assertFalse(proof['termination_requested'])
        self.assert_clean(proof)

    def test_forced_timeout_terminates_entire_owned_job_then_verifies_empty(self):
        script = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(300)']); print('PRIVATE_PARTIAL',flush=True); time.sleep(300)"
        with self.assertRaisesRegex(s.SupervisorError, '^supervision_failed$') as caught:
            s.supervise(self.command(script), timeout=3)
        proof = caught.exception.proof
        self.assertTrue(proof['timed_out'])
        self.assertTrue(proof['termination_requested'])
        self.assertGreaterEqual(proof['total_processes'], 2)
        self.assertNotIn('PRIVATE_PARTIAL', str(caught.exception))
        self.assertNotIn('stdout', proof)
        self.assert_clean(proof)

    def test_assignment_failure_terminates_still_suspended_owned_helper(self):
        real_popen = s.subprocess.Popen
        def suspended_spawn(argv, **kwargs):
            self.assertEqual(kwargs['creationflags'], s.CREATE_SUSPENDED)
            self.assertIs(kwargs['shell'], False)
            self.assertIs(kwargs['close_fds'], True)
            return real_popen(argv, **kwargs)
        with patch.object(s.subprocess, 'Popen', side_effect=suspended_spawn), patch.object(s._WindowsJob, 'assign', side_effect=s.SupervisorError()), patch.object(s, '_resume_owned_process') as resume:
            with self.assertRaises(s.SupervisorError) as caught:
                s.supervise(self.command("print('MUST_NOT_RUN',flush=True)"), timeout=3)
        resume.assert_not_called()
        proof = caught.exception.proof
        self.assertFalse(proof['assigned_before_resume'])
        self.assertFalse(proof['resumed'])
        self.assertTrue(proof['terminated_before_resume'])
        self.assertEqual(proof['total_processes'], 0)
        self.assert_clean(proof)

    def test_descendant_outliving_successful_helper_fails_closed(self):
        script = "import subprocess,sys; subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(300)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print('PRIVATE_SUCCESS',flush=True)"
        with self.assertRaises(s.SupervisorError) as caught:
            s.supervise(self.command(script), timeout=5)
        proof = caught.exception.proof
        self.assertEqual(proof['helper_exit_code'], 0)
        self.assertTrue(proof['termination_requested'])
        self.assertGreaterEqual(proof['total_processes'], 2)
        self.assertFalse(proof['timed_out'])
        self.assert_clean(proof)

    def test_resume_failure_cleans_assigned_suspended_helper(self):
        with patch.object(s, '_resume_owned_process', side_effect=s.SupervisorError()):
            with self.assertRaises(s.SupervisorError) as caught:
                s.supervise(self.command("print('MUST_NOT_RUN')"), timeout=3)
        self.assertTrue(caught.exception.proof['assigned_before_resume'])
        self.assertFalse(caught.exception.proof['resumed'])
        self.assertTrue(caught.exception.proof['termination_requested'])
        self.assert_clean(caught.exception.proof)

    def test_job_setup_failure_never_creates_helper(self):
        with patch.object(s, '_WindowsJob', side_effect=s.SupervisorError()), patch.object(s.subprocess, 'Popen') as spawn:
            with self.assertRaises(s.SupervisorError):
                s.supervise(self.command("pass"), timeout=3)
        spawn.assert_not_called()

    def test_nonzero_helper_discards_partial_output(self):
        with self.assertRaises(s.SupervisorError) as caught:
            s.supervise(self.command("import sys; print('PRIVATE_OUTPUT',flush=True); sys.exit(7)"), timeout=5)
        self.assertEqual(caught.exception.proof['helper_exit_code'], 7)
        self.assertNotIn('PRIVATE_OUTPUT', str(caught.exception))
        self.assert_clean(caught.exception.proof)

    def test_input_feeder_and_all_reader_threads_finish(self):
        output, proof = s.supervise(self.command("import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stdout.buffer.flush()"), input_bytes=b'x' * 16384, timeout=5)
        self.assertEqual(output, b'x' * 16384)
        self.assert_clean(proof)

    def test_blocked_input_feeder_cannot_bypass_timeout(self):
        with self.assertRaises(s.SupervisorError) as caught:
            s.supervise(self.command("import time; time.sleep(300)"), input_bytes=b'x' * 65536, timeout=0.3)
        self.assertTrue(caught.exception.proof['timed_out'])
        self.assert_clean(caught.exception.proof)

    def test_bad_arguments_fail_before_job_or_process(self):
        with patch.object(s, '_WindowsJob') as job, patch.object(s.subprocess, 'Popen') as spawn:
            for argv, changes in [([], {}), (['python'], {}), ([sys.executable, 1], {}), ([sys.executable], {'timeout': 0}), ([sys.executable], {'timeout': 56}), ([sys.executable], {'timeout': True}), ([sys.executable], {'input_bytes': 'private'})]:
                with self.subTest(argv=argv, changes=changes), self.assertRaises(s.SupervisorError):
                    s.supervise(argv, **changes)
        job.assert_not_called()
        spawn.assert_not_called()


    def test_success_proof_checks_captured_actual_windows_reader(self):
        owned = []
        real_popen = s.subprocess.Popen
        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            owned.append(process)
            return process
        with patch.object(s.subprocess, 'Popen', side_effect=capture):
            output, proof = s.supervise(self.command("print('reader-finished',flush=True)"), timeout=5)
        self.assertEqual(len(owned), 1)
        self.assertTrue(hasattr(owned[0], 'stdout_thread'))
        self.assertFalse(owned[0].stdout_thread.is_alive())
        self.assertTrue(proof['io_threads_stopped'])
        self.assert_clean(proof)

    def test_living_actual_reader_attribute_cannot_receive_clean_proof(self):
        import threading
        stopped = threading.Event()
        injected_reader = threading.Thread(target=stopped.wait, daemon=True)
        injected_reader.start()
        real_popen = s.subprocess.Popen
        captured = []
        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            communicate = process.communicate
            calls = [0]
            def checked_communicate(*positional, **named):
                result = communicate(*positional, **named)
                calls[0] += 1
                if calls[0] == 2:
                    # Real owned pipe reader has finished. Inject a living
                    # test-owned reader only after communicate's own join,
                    # isolating the explicit final proof gate.
                    captured.append(process.stdout_thread)
                    self.assertFalse(process.stdout_thread.is_alive())
                    process.stdout_thread = injected_reader
                return result
            process.communicate = checked_communicate
            return process
        try:
            with patch.object(s.subprocess, 'Popen', side_effect=capture):
                with self.assertRaisesRegex(s.SupervisorError, '^supervision_failed$') as caught:
                    s.supervise(self.command("print('must-suppress',flush=True)"), timeout=5)
            self.assertFalse(caught.exception.proof['io_threads_stopped'])
            self.assertEqual(len(captured), 1)
            self.assertTrue(all(not thread.is_alive() for thread in captured))
        finally:
            stopped.set()
            injected_reader.join(timeout=2)
        self.assertFalse(injected_reader.is_alive())

    def test_winapi_structure_sizes_and_pointer_width(self):
        import ctypes
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(s._BASIC_LIMIT_INFORMATION), 64)
            self.assertEqual(ctypes.sizeof(s._EXTENDED_LIMIT_INFORMATION), 144)
        self.assertEqual(ctypes.sizeof(s._BASIC_ACCOUNTING_INFORMATION), 48)


if __name__ == '__main__':
    unittest.main()
