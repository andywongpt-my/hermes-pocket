"""Passive one-shot Claude statusLine stdin quota callback; no renderer or clients.

Deploy with the frozen sibling adapter and backend/pocket modules. Caller supplies
an explicit opaque account binding, an existing private state directory, and an
existing private backend quota directory. Only stdin, fixed state, and claude.json
are used. Events do not authorize access to any of their referenced paths.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

BASE = Path(__file__).absolute().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'backend'))
import claude_statusline_quota_020 as adapter
from pocket.quota_export import _output_path, _publication_lock, publish_snapshot
from pocket.quota_snapshots import (MAX_FILE_BYTES, MAX_INPUT_BYTES, PrivateDirectory,
    QuotaError, _private, _revision, loads_json)

STATE_NAME = 'claude-state.json'


def _read_state(directory):
    directory.verify()
    try:
        before = directory.info(STATE_NAME)
    except FileNotFoundError:
        return None, None
    _private(before)
    if before.st_size > MAX_FILE_BYTES:
        raise QuotaError('invalid_state')
    fd = directory.open(STATE_NAME, os.O_RDONLY)
    try:
        opened = os.fstat(fd)
        _private(opened)
        directory.verify()
        if (_revision(opened) != _revision(before) or
                _revision(directory.info(STATE_NAME)) != _revision(before)):
            raise QuotaError('invalid_state')
        chunks = []
        total = 0
        while total <= MAX_FILE_BYTES:
            chunk = os.read(fd, MAX_FILE_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(fd)
        directory.verify()
        if (total != after.st_size or _revision(after) != _revision(before) or
                _revision(directory.info(STATE_NAME)) != _revision(before)):
            raise QuotaError('invalid_state')
        value = adapter._state(loads_json(b''.join(chunks), maximum=MAX_FILE_BYTES))
        if value is None:
            raise QuotaError('invalid_state')
        return value, before
    finally:
        os.close(fd)


def _write_state(directory, value, previous_info):
    value = adapter._state(value)
    if value is None:
        raise QuotaError('invalid_state')
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=True,
                     allow_nan=False).encode('ascii')
    if len(raw) > MAX_FILE_BYTES:
        raise QuotaError('invalid_state')
    temporary = '.claude-state-' + uuid.uuid4().hex + '.tmp'
    created = False
    try:
        directory.verify()
        fd = directory.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        created = True
        try:
            _private(os.fstat(fd))
            directory.verify()
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
            if _revision(directory.info(temporary)) != _revision(os.fstat(fd)):
                raise QuotaError('invalid_state')
        finally:
            os.close(fd)
        directory.verify()
        try:
            existing = directory.info(STATE_NAME)
            _private(existing)
        except FileNotFoundError:
            existing = None
        if ((existing is None) != (previous_info is None) or
                existing is not None and _revision(existing) != _revision(previous_info)):
            raise QuotaError('invalid_state')
        directory.replace(temporary, STATE_NAME)
        created = False
        if directory.fd is not None:
            os.fsync(directory.fd)
    finally:
        if created:
            directory.unlink(temporary)


def _invalidate(output):
    """Clear only the fixed quota file, under the frozen publisher's lock."""
    with PrivateDirectory(output.parent) as directory, _publication_lock(directory):
        directory.verify()
        try:
            before = directory.info(output.name)
        except FileNotFoundError:
            return
        _private(before)
        directory.verify()
        if _revision(directory.info(output.name)) != _revision(before):
            raise QuotaError('unsafe_output')
        directory.unlink(output.name)
        if directory.fd is not None:
            os.fsync(directory.fd)


def consume(stdin, *, state_directory, output, account_binding, observed_at):
    """Consume one bounded event, commit sanitized high-water state, then publish.

    Failed publication never rolls state back or causes a cached replay to be
    republished. A later real response may publish. Baseline state has no last_good;
    any pending invalidation is completed before processing the next event.
    """
    if (not adapter._opaque(account_binding) or
            not adapter._integer(observed_at, adapter.MAX_EPOCH)):
        raise QuotaError('invalid_input')
    output = _output_path(output, 'claude')
    state_directory = Path(state_directory)
    if (not state_directory.is_absolute() or '..' in state_directory.parts or
            os.path.normcase(os.path.abspath(state_directory)) ==
            os.path.normcase(os.path.abspath(output.parent))):
        raise QuotaError('invalid_input')
    # Parse before file access. Neither metadata nor raw input reaches a file.
    event = loads_json(stdin.read(MAX_INPUT_BYTES + 1), maximum=MAX_INPUT_BYTES)
    if type(event) is not dict:
        raise QuotaError('invalid_input')
    with PrivateDirectory(state_directory) as directory, _publication_lock(directory):
        previous, previous_info = _read_state(directory)
        if previous is not None and previous['last_good'] is None:
            _invalidate(output)
        result = adapter.adapt_statusline(event, previous, observed_at,
                                         account_binding, adapter.SOURCE_CONTRACT)
        if result['status'] == 'invalid' or result['state'] is None:
            raise QuotaError('invalid_input')
        _write_state(directory, result['state'], previous_info)
        if result['state']['last_good'] is None:
            _invalidate(output)
        elif result['status'] == 'fresh':
            publish_snapshot(result['snapshot'], output, clock=lambda: observed_at)
        return {'status': result['status'], 'published': result['status'] == 'fresh'}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise QuotaError('invalid_input')


def main(argv=None, *, stdin=None, stdout=None, stderr=None):
    """No stdout: an existing renderer remains responsible for statusLine text."""
    try:
        parser = _Parser(add_help=False)
        parser.add_argument('--state-directory', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--account-binding', required=True)
        args = parser.parse_args(argv)
        consume(sys.stdin.buffer if stdin is None else stdin,
                state_directory=args.state_directory, output=args.output,
                account_binding=args.account_binding, observed_at=int(time.time()))
        return 0
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        print('claude_quota_callback: invalid_input',
              file=sys.stderr if stderr is None else stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
