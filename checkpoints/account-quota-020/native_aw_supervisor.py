"""Scoped Windows supervisor. Owns only one Popen helper and its fresh job.

No process discovery or PID-based termination. All descendants inherit this job;
breakaway is never enabled. Only successful, completely cleaned runs return
stdout. Error evidence contains fixed booleans/counters, never partial output.
"""
import ctypes
from ctypes import wintypes
import math
import os
import subprocess
import sys
import threading
import time

CREATE_SUSPENDED = 0x00000004
KILL_ON_JOB_CLOSE = 0x00002000
MAX_TIMEOUT = 55
CLEANUP_TIMEOUT = 4
MAX_INPUT = 65536
MAX_OUTPUT = 65536


class SupervisorError(ValueError):
    def __init__(self, proof=None):
        super().__init__('supervision_failed')
        self.proof = {} if proof is None else dict(proof)


class _BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD)]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BASIC_LIMIT_INFORMATION),
                ('IoInfo', _IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [('TotalUserTime', ctypes.c_int64),
                ('TotalKernelTime', ctypes.c_int64),
                ('ThisPeriodTotalUserTime', ctypes.c_int64),
                ('ThisPeriodTotalKernelTime', ctypes.c_int64),
                ('TotalPageFaultCount', wintypes.DWORD),
                ('TotalProcesses', wintypes.DWORD),
                ('ActiveProcesses', wintypes.DWORD),
                ('TotalTerminatedProcesses', wintypes.DWORD)]


class _WinAPI:
    def __init__(self):
        if sys.platform != 'win32':
            raise SupervisorError()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        ntdll = ctypes.WinDLL('ntdll')
        self.create = kernel.CreateJobObjectW
        self.create.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.create.restype = wintypes.HANDLE
        self.handle_flags = kernel.SetHandleInformation
        self.handle_flags.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        self.handle_flags.restype = wintypes.BOOL
        self.set_info = kernel.SetInformationJobObject
        self.set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.set_info.restype = wintypes.BOOL
        self.assign = kernel.AssignProcessToJobObject
        self.assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.assign.restype = wintypes.BOOL
        self.query = kernel.QueryInformationJobObject
        self.query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.query.restype = wintypes.BOOL
        self.terminate = kernel.TerminateJobObject
        self.terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.terminate.restype = wintypes.BOOL
        self.close = kernel.CloseHandle
        self.close.argtypes = [wintypes.HANDLE]
        self.close.restype = wintypes.BOOL
        # Popen closes its primary thread handle. Resume only its owned process
        # handle, never enumerate/open threads. NTSTATUS is a signed 32-bit value.
        self.resume = ntdll.NtResumeProcess
        self.resume.argtypes = [wintypes.HANDLE]
        self.resume.restype = ctypes.c_int32


class _WindowsJob:
    def __init__(self):
        self.api = _WinAPI()
        self.handle = self.api.create(None, None)
        if not self.handle:
            raise SupervisorError()
        try:
            if not self.api.handle_flags(self.handle, 1, 0):
                raise SupervisorError()
            limits = _EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
            if not self.api.set_info(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise SupervisorError()
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        if not self.api.assign(self.handle, wintypes.HANDLE(int(process._handle))):
            raise SupervisorError()

    def accounting(self):
        info = _BASIC_ACCOUNTING_INFORMATION()
        returned = wintypes.DWORD()
        if not self.api.query(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)):
            raise SupervisorError()
        if returned.value != ctypes.sizeof(info):
            raise SupervisorError()
        return int(info.ActiveProcesses), int(info.TotalProcesses)

    def terminate(self):
        if not self.api.terminate(self.handle, 1):
            raise SupervisorError()

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            if not self.api.close(handle):
                raise SupervisorError()


def _resume_owned_process(process, job):
    if job.api.resume(wintypes.HANDLE(int(process._handle))) < 0:
        raise SupervisorError()


def _remaining(deadline):
    return max(0, deadline - time.monotonic())


def _feed_input(pipe, data, failures):
    try:
        pipe.write(data)
        pipe.flush()
    except (OSError, ValueError):
        failures.append(True)
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            failures.append(True)


def _wait_empty(job, deadline, proof):
    while True:
        active, total = job.accounting()
        proof['active_processes'] = active
        proof['total_processes'] = total
        if active == 0:
            proof['job_empty'] = True
            return
        remaining = _remaining(deadline)
        if remaining <= 0:
            raise SupervisorError()
        time.sleep(min(0.01, remaining))


def supervise(argv, input_bytes=None, timeout=55):
    """Return (stdout_bytes, cleanup_proof) after exit zero and an empty job.

    Runs only absolute argv[0], shell=False, CREATE_SUSPENDED, no breakaway.
    timeout bounds communicate (0 < timeout <= 55); cleanup has a separate
    four-second budget. OS process creation itself has no stdlib timeout.
    Root must invoke this function directly, preserving helper parent PID.
    """
    proof = {'assigned_before_resume': False, 'resumed': False,
             'terminated_before_resume': False, 'termination_requested': False,
             'timed_out': False, 'helper_exit_code': None,
             'active_processes': None, 'total_processes': None,
             'job_empty': False, 'all_pipes_closed': False,
             'io_threads_stopped': False, 'helper_process_handle_closed': False,
             'job_handle_closed': False}
    if (sys.platform != 'win32' or not isinstance(argv, (list, tuple)) or
            not argv or any(not isinstance(value, str) or '\x00' in value for value in argv) or
            not os.path.isabs(argv[0]) or
            type(timeout) not in (int, float) or not math.isfinite(timeout) or
            not 0 < timeout <= MAX_TIMEOUT or
            input_bytes is not None and (not isinstance(input_bytes, bytes) or len(input_bytes) > MAX_INPUT)):
        raise SupervisorError(proof)
    job, process, stdin, feeder = None, None, None, None
    output, input_failures = b'', []
    failed = False
    try:
        job = _WindowsJob()
        process = subprocess.Popen(list(argv), shell=False, close_fds=True,
                                   creationflags=CREATE_SUSPENDED,
                                   stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        # Detach stdin from communicate: Windows communicate's synchronous input
        # write can otherwise defeat its timeout. A bounded owned feeder closes it.
        stdin, process.stdin = process.stdin, None
        job.assign(process)
        proof['assigned_before_resume'] = True
        _resume_owned_process(process, job)
        proof['resumed'] = True
        if stdin is not None:
            feeder = threading.Thread(target=_feed_input, args=(stdin, input_bytes, input_failures),
                                      name='native-aw-supervisor-input', daemon=True)
            feeder.start()
        output, _ = process.communicate(timeout=timeout)
        proof['helper_exit_code'] = process.returncode
        active, total = job.accounting()
        proof['active_processes'], proof['total_processes'] = active, total
        if process.returncode != 0 or active != 0 or len(output) > MAX_OUTPUT:
            raise SupervisorError()
    except subprocess.TimeoutExpired:
        proof['timed_out'] = True
        failed = True
    except BaseException:
        failed = True
    finally:
        cleanup_deadline = time.monotonic() + CLEANUP_TIMEOUT
        if failed and process is not None:
            try:
                if proof['assigned_before_resume']:
                    proof['termination_requested'] = True
                    job.terminate()
                else:
                    proof['terminated_before_resume'] = True
                    process.terminate()
            except BaseException:
                failed = True
        if process is not None:
            try:
                process.communicate(timeout=_remaining(cleanup_deadline))
                process.wait(timeout=_remaining(cleanup_deadline))
                proof['helper_exit_code'] = process.returncode
                if feeder is not None:
                    feeder.join(timeout=_remaining(cleanup_deadline))
                if feeder is not None and feeder.is_alive():
                    raise SupervisorError()
                if stdin is not None and not stdin.closed:
                    stdin.close()
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None and not pipe.closed:
                        pipe.close()
                threads = [getattr(process, name, None) for name in ('stdout_thread', 'stderr_thread')]
                proof['io_threads_stopped'] = all(thread is None or not thread.is_alive() for thread in threads) and (feeder is None or not feeder.is_alive())
                proof['all_pipes_closed'] = (stdin is None or stdin.closed) and all(pipe is None or pipe.closed for pipe in (process.stdout, process.stderr))
                if not proof['io_threads_stopped'] or not proof['all_pipes_closed']:
                    raise SupervisorError()
            except BaseException:
                failed = True
        if job is not None:
            try:
                _wait_empty(job, cleanup_deadline, proof)
            except BaseException:
                failed = True
            # Query while job remains open; closure is only the final safety net.
        if process is not None:
            try:
                process._handle.Close()
                proof['helper_process_handle_closed'] = True
            except BaseException:
                failed = True
        if job is not None:
            try:
                job.close()
                proof['job_handle_closed'] = True
            except BaseException:
                failed = True
    if (failed or input_failures or not proof['job_empty'] or
            not proof['helper_process_handle_closed'] or not proof['job_handle_closed'] or
            not proof['all_pipes_closed'] or not proof['io_threads_stopped']):
        raise SupervisorError(proof) from None
    return output, proof
