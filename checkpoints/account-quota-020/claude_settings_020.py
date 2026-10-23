"""Reversible statusLine-only settings change; never back up unrelated values."""
import ctypes
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import uuid

MAX_BYTES = 1048576


class SettingsError(ValueError):
    pass


def require(value, code):
    if not value:
        raise SettingsError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _pairs(values):
    result = {}
    for key, value in values:
        require(key not in result, 'duplicate_settings_key')
        result[key] = value
    return result


def parse(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, 'settings_limit')
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(SettingsError('invalid_settings')))
        require(isinstance(value, dict), 'invalid_settings')
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise SettingsError('invalid_settings') from None


def _revision(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _guard(path, *, directory=False):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode)
            and not getattr(info, 'st_file_attributes', 0) & 0x400, 'unsafe_settings_path')
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'unsafe_settings_path')
    if not directory:
        require(info.st_nlink == 1 and info.st_size <= MAX_BYTES, 'unsafe_settings_path')
    return info


def read_raw(path):
    path = Path(os.path.abspath(path))
    for parent in reversed(path.parents):
        _guard(parent, directory=True)
    before = _guard(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    try:
        require(_revision(os.fstat(fd)) == _revision(before), 'settings_changed')
        chunks = []; size = 0
        while size <= MAX_BYTES:
            part = os.read(fd, min(65536, MAX_BYTES + 1 - size))
            if not part:
                break
            chunks.append(part); size += len(part)
        require(size <= MAX_BYTES and _revision(os.fstat(fd)) == _revision(before)
                and _revision(_guard(path)) == _revision(before), 'settings_changed')
        return b''.join(chunks), before
    finally:
        os.close(fd)


def command_for(python, script, config, config_sha256):
    require(isinstance(config_sha256, str) and len(config_sha256) == 64
            and all(x in '0123456789abcdef' for x in config_sha256), 'config_digest')
    values = []
    for path in (python, script, config):
        require(isinstance(path, str) and PureWindowsPath(path).is_absolute()
                and not any(x in path for x in ('\x00', '\r', '\n', '"', '$', '`', '%', '!')),
                'unsafe_command_path')
        values.append(path.replace('\\', '/').replace("'", "''"))
    code = "& '{}' -B '{}' --config '{}' --config-sha256 '{}'".format(*values, config_sha256)
    return 'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoLogo -NoProfile -NonInteractive -Command "' + code + '"'


def write_backup(path, value):
    path = Path(os.path.abspath(path))
    for parent in (*reversed(path.parents), path.parent):
        _guard(parent, directory=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
    except OSError:
        raise SettingsError('backup_collision_or_unavailable') from None


def replace_existing(path, temporary):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.ReplaceFileW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)
        kernel.ReplaceFileW.restype = ctypes.c_int
        # ReplaceFile preserves the existing file's ACL rather than inheriting a
        # new access policy from the temporary file. No whole-file backup exists.
        if not kernel.ReplaceFileW(str(path), str(temporary), None, 0, None, None):
            raise OSError(ctypes.get_last_error(), 'settings_replace_failed')
    else:
        os.replace(temporary, path)


def _replace(path, new_raw, expected_raw, expected_info):
    temporary = path.with_name('.claude-quota-settings-' + uuid.uuid4().hex + '.tmp')
    created = False
    try:
        current, info = read_raw(path)
        require(current == expected_raw and _revision(info) == _revision(expected_info), 'settings_changed')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        created = True
        with os.fdopen(fd, 'wb') as stream:
            if os.name == 'posix':
                os.fchmod(stream.fileno(), stat.S_IMODE(expected_info.st_mode))
            stream.write(new_raw); stream.flush(); os.fsync(stream.fileno())
        current, info = read_raw(path)
        require(current == expected_raw and _revision(info) == _revision(expected_info), 'settings_changed')
        replace_existing(path, temporary); created = False
        after, _ = read_raw(path)
        require(after == new_raw, 'settings_replace_uncertain')
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
    except (OSError, ValueError):
        raise SettingsError('settings_changed_or_replace_failed') from None
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def install_settings(settings_path, backup_path, command):
    require(isinstance(command, str) and 0 < len(command) <= 16384
            and not any(x in command for x in ('\x00', '\n', '\r')), 'invalid_statusline_command')
    path = Path(os.path.abspath(settings_path))
    raw, info = read_raw(path); document = parse(raw)
    require(document.get('statusLine') is None, 'existing_renderer_requires_composition_review')
    installed = {'type': 'command', 'command': command}
    other = dict(document); other.pop('statusLine', None)
    receipt = {'version': 1, 'settings_path': str(path), 'prior_statusline_present': 'statusLine' in document,
        'prior_statusline': document.get('statusLine'), 'installed_statusline': installed,
        'original_file_sha256': hashlib.sha256(raw).hexdigest(),
        'other_fields_sha256': hashlib.sha256(canonical(other)).hexdigest()}
    document['statusLine'] = installed
    new_raw = (json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    receipt['installed_file_sha256'] = hashlib.sha256(new_raw).hexdigest()
    write_backup(backup_path, receipt)
    _replace(path, new_raw, raw, info)
    require(verify_settings(path, backup_path), 'settings_verification_failed')
    return receipt


def _backup(settings_path, backup_path):
    raw, _ = read_raw(backup_path); value = parse(raw)
    require(set(value) == {'version', 'settings_path', 'prior_statusline_present', 'prior_statusline',
        'installed_statusline', 'original_file_sha256', 'other_fields_sha256', 'installed_file_sha256'}
        and value['version'] == 1 and value['settings_path'] == str(Path(os.path.abspath(settings_path)))
        and type(value['prior_statusline_present']) is bool and value['prior_statusline'] is None,
        'invalid_statusline_backup')
    installed = value['installed_statusline']
    require(isinstance(installed, dict) and set(installed) == {'type', 'command'}
        and installed['type'] == 'command' and isinstance(installed['command'], str), 'invalid_statusline_backup')
    return value


def verify_settings(settings_path, backup_path):
    receipt = _backup(settings_path, backup_path)
    raw, _ = read_raw(settings_path); document = parse(raw)
    installed = document.pop('statusLine', None)
    return (installed == receipt['installed_statusline']
            and hashlib.sha256(canonical(document)).hexdigest() == receipt['other_fields_sha256'])


def restore_settings(settings_path, backup_path):
    receipt = _backup(settings_path, backup_path)
    path = Path(os.path.abspath(settings_path)); raw, info = read_raw(path); document = parse(raw)
    require(document.get('statusLine') == receipt['installed_statusline'], 'owner_changed_statusline')
    if receipt['prior_statusline_present']:
        document['statusLine'] = None
    else:
        document.pop('statusLine')
    new_raw = (json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    _replace(path, new_raw, raw, info)
    return {'ok': True, 'statusline_restored': True}
