"""Local-file preflight only: no serial import, port discovery, network or writes.

File hashes establish bytes, not physical identity, reset behavior or permission.
Windows paths must be absolute normal drive paths, not device/UNC/ADS aliases.
Detected symlinks/reparse points are rejected; trusted local paths must remain
stable during the check (this is not a hostile-filesystem race-proof sandbox).
"""
import hashlib
from importlib import metadata
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import stat
import sys
import zipfile

CANDIDATE_ZIP_SHA256 = '73e14b885f32db723a09fb13880829234e42eb19900d2e5cd1ebd64c644b2501'
CANDIDATE_ZIP_BYTES = 1510745
APP_SHA256 = 'c8f58b769bf6004435da7f8f45bf545f4d6f8cd6cfaedfdcd6819377d05a063d'
APP_BYTES = 2494160
RECOVERY_SHA256 = 'e21556a8b9fef5378ce6658c9e45c6583d6797845944fd57808d8eb8ecf569ad'
RECOVERY_BYTES = 2494464
APP_MEMBER = 'hermes-pocket-qualification-app.bin'

class PreflightError(ValueError):
    """Fixed code only; never include file paths, port names or payload bytes."""


def local_path(value, *, platform=None):
    if not isinstance(value,str) or not 1 <= len(value) <= 1024 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise PreflightError('invalid_local_file_path')
    system = sys.platform if platform is None else platform
    if system == 'win32':
        if not re.match(r'^[A-Za-z]:[\\/]',value):
            raise PreflightError('invalid_local_file_path')
        raw = value[3:].replace('/','\\').split('\\')
        if not raw or any(not c or c in ('.','..') or c[-1] in ' .' or
            any(x in c for x in ':<>"|?*') or PureWindowsPath(c).is_reserved() or
            re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[0-9]+|LPT[0-9]+)',c.split('.')[0],re.I)
            for c in raw):
            raise PreflightError('invalid_local_file_path')
        return PureWindowsPath(value)
    path=Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise PreflightError('invalid_local_file_path')
    return path


def drive_type(root):
    """Windows filesystem classification only; never opens a serial device."""
    try:
        import ctypes
        library=ctypes.WinDLL('kernel32',use_last_error=True)
        function=library.GetDriveTypeW
        function.argtypes=[ctypes.c_wchar_p];function.restype=ctypes.c_uint
        return int(function(root))
    except (AttributeError,OSError,ValueError):
        raise PreflightError('local_drive_classification_unavailable') from None


def require_local_drive(path):
    # Only a fixed drive is supported. No mapped SMB/removable/unknown fallback.
    # Local mount/drive identity must remain trusted and stable during this check.
    if drive_type(path.anchor)!=3:
        raise PreflightError('fixed_local_drive_required')


def validate_binding(port, identity):
    if (not isinstance(port,str) or not re.fullmatch(r'COM[1-9][0-9]{0,2}',port) or
            int(port[3:])>256 or not isinstance(identity,str) or
            not 1<=len(identity)<=128 or any(ord(c)<33 or ord(c)>126 for c in identity)):
        raise PreflightError('explicit_windows_binding_required')


def _open_file(value, expected_size):
    lexical=local_path(value)
    if sys.platform=='win32':require_local_drive(lexical)
    path=Path(lexical)
    if type(expected_size) is not int or not 0<=expected_size<=8*1024*1024:
        raise PreflightError('invalid_file_bound')
    fd=None
    try:
        for part in (*reversed(path.parents),path):
            info=os.lstat(part)
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise PreflightError('reparse_file_path_refused')
        if not stat.S_ISREG(info.st_mode) or info.st_size!=expected_size:
            raise PreflightError('file_size_or_type_mismatch')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
        actual=os.fstat(fd)
        if (not stat.S_ISREG(actual.st_mode) or actual.st_size!=expected_size or
                (actual.st_dev,actual.st_ino)!=(info.st_dev,info.st_ino)):
            raise PreflightError('file_changed_during_check')
        stream=os.fdopen(fd,'rb');fd=None
        return stream
    except (OSError,ValueError):
        raise PreflightError('local_file_unavailable_or_invalid') from None
    finally:
        if fd is not None:os.close(fd)


def _check_stream(stream, expected_size, expected_hash):
    digest=hashlib.sha256();remaining=expected_size
    while remaining:
        data=stream.read(min(65536,remaining))
        if not data:raise PreflightError('file_changed_during_check')
        digest.update(data);remaining-=len(data)
    if stream.read(1) or digest.hexdigest()!=expected_hash:
        raise PreflightError('file_hash_mismatch')


def verified_file(path, expected_size, expected_hash):
    with _open_file(path,expected_size) as stream:
        _check_stream(stream,expected_size,expected_hash)
    return True


def runtime_facts():
    windows=sys.platform=='win32';cpython=platform.python_implementation()=='CPython'
    version=tuple(sys.version_info[:2]);python_ok=cpython and version in ((3,11),(3,12))
    try:serial_ok=metadata.version('pyserial')=='3.5'
    except metadata.PackageNotFoundError:serial_ok=False
    return dict(windows=int(windows),supported_python=int(python_ok),pyserial_3_5=int(serial_ok),
                runtime_compatible=int(windows and python_ok and serial_ok))


def preflight(candidate_zip, recovery_file, port, identity):
    """Rehash exact explicitly supplied files; returned facts never authorize I/O."""
    validate_binding(port,identity)
    with _open_file(candidate_zip,CANDIDATE_ZIP_BYTES) as stream:
        _check_stream(stream,CANDIDATE_ZIP_BYTES,CANDIDATE_ZIP_SHA256)
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                member=archive.getinfo(APP_MEMBER)
                if member.file_size!=APP_BYTES:raise PreflightError('candidate_app_size_mismatch')
                with archive.open(member) as app:
                    _check_stream(app,APP_BYTES,APP_SHA256)
        except (zipfile.BadZipFile,KeyError,OSError):
            raise PreflightError('candidate_archive_invalid') from None
    verified_file(recovery_file,RECOVERY_BYTES,RECOVERY_SHA256)
    return dict(version=1,candidate_verified=1,recovery_verified=1,**runtime_facts(),
                physical_identity_verified=0,platform_behavior_verified=0,
                device_opened=0,flash_authorized=0,physical_acceptance=0)
