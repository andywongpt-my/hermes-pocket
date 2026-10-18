"""Credential-free resident local STT. The pinned helper owns all model settings."""
import argparse
import base64
import contextlib
import hashlib
import importlib.util
import io
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import wave

if __package__:
    from .stt_protocol import read_frame, encode_frame, MAX_REQUEST, MAX_RESPONSE
else:
    from stt_protocol import read_frame, encode_frame, MAX_REQUEST, MAX_RESPONSE


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) != {
            'helper_path', 'helper_sha256', 'model_path', 'language'}:
        raise ValueError('settings schema')
    if settings['language'] != 'auto':
        raise ValueError('auto language required')
    helper, model = Path(settings['helper_path']), Path(settings['model_path'])
    digest = settings['helper_sha256']
    if (not helper.is_absolute() or helper.name != 'pocket_local_stt.py'
            or not helper.is_file() or helper.is_symlink()
            or not isinstance(digest, str) or len(digest) != 64
            or hashlib.sha256(helper.read_bytes()).hexdigest() != digest
            or not model.is_absolute() or not model.is_dir()
            or not (model / 'model.bin').is_file()):
        raise ValueError('pinned local model required')
    return helper, model


class ResidentEngine:
    def __init__(self, settings, *, factory=None):
        helper, self.model = validate_settings(settings)
        spec = importlib.util.spec_from_file_location('pocket_pinned_local_stt', helper)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.engine = self.helper.load_model(self.model, factory=factory)

    def transcribe(self, wav):
        with tempfile.TemporaryDirectory(prefix='stt-job-') as directory:
            audio = Path(directory) / 'input.wav'
            fd = os.open(audio, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(wav)
            # The exact helper remains unchanged. Its factory injection supplies
            # our already-loaded engine, preserving every inference/filter option.
            return self.helper.transcribe(audio, self.model, 'auto',
                                           factory=lambda *a, **kw: self.engine)


def _wav(body):
    if (not isinstance(body, dict) or set(body) != {'data_url', 'mime_type'}
            or body['mime_type'] != 'audio/wav' or not isinstance(body['data_url'], str)
            or not body['data_url'].startswith('data:audio/wav;base64,')):
        raise ValueError('invalid audio')
    raw = base64.b64decode(body['data_url'].split(',', 1)[1], validate=True)
    if not 0 < len(raw) <= 964096:
        raise ValueError('invalid audio')
    with wave.open(io.BytesIO(raw), 'rb') as stream:
        frames = stream.getnframes()
        if (stream.getnchannels() != 1 or stream.getsampwidth() != 2
                or stream.getframerate() != 16000 or stream.getcomptype() != 'NONE'
                or not 0 < frames <= 480000 or len(stream.readframes(frames)) != frames * 2):
            raise ValueError('invalid audio')
    return raw


def _parent_guard(parent):
    if sys.platform == 'linux':
        import ctypes
        # Also reap on abrupt bridge death; normal shutdown is owned by the pool.
        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise ValueError('parent guard unavailable')
        if os.getppid() != parent:
            raise ValueError('parent exited')


def serve(input_stream, output_stream, *, factory=None):
    init = read_frame(input_stream, MAX_REQUEST)
    if (not isinstance(init, dict) or set(init) != {'kind', 'generation', 'settings'}
            or init['kind'] != 'init' or type(init['generation']) is not int
            or not 1 <= init['generation'] < 2**31):
        raise ValueError('init schema')
    generation = init['generation']
    started = time.monotonic_ns()
    engine = ResidentEngine(init['settings'], factory=factory)
    output_stream.write(encode_frame({'kind': 'ready', 'generation': generation,
        'model_load_us': (time.monotonic_ns() - started) // 1000}, MAX_RESPONSE))
    output_stream.flush()
    while True:
        request = read_frame(input_stream, MAX_REQUEST)
        if request is None:
            return
        if (not isinstance(request, dict) or set(request) != {'kind', 'generation', 'request_id', 'body'}
                or request['kind'] != 'transcribe' or type(request['generation']) is not int
                or request['generation'] != generation or not isinstance(request['request_id'], str)
                or len(request['request_id']) != 32
                or any(c not in '0123456789abcdef' for c in request['request_id'])):
            raise ValueError('request schema')
        result = {'kind': 'result', 'generation': generation, 'request_id': request['request_id']}
        started = time.monotonic_ns()
        try:
            try:
                raw = _wav(request['body'])
            except Exception:
                result.update(ok=False, reason='stt_invalid_audio')
            else:
                try:
                    text = engine.transcribe(raw)
                    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 4096:
                        raise ValueError('invalid transcript')
                    result.update(ok=True, transcript=text.strip())
                except ValueError as error:
                    reason = ('stt_no_confident_speech' if str(error) == 'no confident speech detected'
                              else 'stt_worker_failed')
                    result.update(ok=False, reason=reason)
                except Exception:
                    result.update(ok=False, reason='stt_worker_failed')
        finally:
            result['inference_us'] = (time.monotonic_ns() - started) // 1000
        output_stream.write(encode_frame(result, MAX_RESPONSE)); output_stream.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-pid', required=True, type=int)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        _parent_guard(args.parent_pid)
        output = sys.stdout.buffer
        with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            serve(sys.stdin.buffer, output)
    except Exception:
        return 1  # No exception, path, input/output or Hermes configuration on wire.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
