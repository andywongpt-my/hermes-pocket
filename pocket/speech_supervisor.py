"""Linux-only per-request subreaper; no provider/config/credential access."""
import argparse
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _drain_children():
    # All children were created/adopted by this single-threaded supervisor.
    # Killing direct parents adopts their independently-sessioned children here.
    # Repeat until the kernel confirms there are no children left to reap.
    children = Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children')
    while True:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            return
        for value in children.read_text().split():
            try:
                os.kill(int(value), signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(.005)


def supervise(command, timeout):
    if sys.platform != 'linux' or not command or not 0 < timeout <= 180:
        raise ValueError('bounded Linux speech supervision required')
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
    prctl.restype = ctypes.c_int
    if prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'speech containment unavailable')
    Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children').read_text()
    cancelled = False

    def cancel(signum, frame):
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    proc = None
    try:
        proc = subprocess.Popen(command)
        deadline = time.monotonic() + timeout
        while not cancelled:
            status = proc.poll()
            if status is not None:
                return status
            if time.monotonic() >= deadline:
                return 124
            time.sleep(.02)
        return 125
    finally:
        # Never abandon adopted provider children when the worker exits early.
        _drain_children()
        if proc is not None:
            proc.poll()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    return supervise(command, args.timeout)


if __name__ == '__main__':
    raise SystemExit(main())
