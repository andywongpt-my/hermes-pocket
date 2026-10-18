"""Fixed-operation worker in the existing Hermes environment; never emits keys/logs."""
import argparse
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import shlex
import shutil
import sys
import wave

MAX_PCM = 960000
MAX_AUDIO = 3 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False).encode()).hexdigest()


def validate_provider_pins(pins, stt, tts):
    if (pins.get('version') != 1 or pins.get('stt_provider') != 'pocket_local'
            or pins.get('tts_provider') != 'fishaudio'
            or pins.get('stt_config_sha256') != _digest(stt)
            or pins.get('tts_config_sha256') != _digest(tts)):
        raise ValueError('approved speech provider definition changed')
    sources = pins.get('source_files')
    if not isinstance(sources, dict) or not sources:
        raise ValueError('approved source pins required')
    for filename, digest in sources.items():
        path = Path(filename)
        if (not path.is_absolute() or path.suffix not in ('.py', '.sh')
                or not path.is_file() or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('approved speech helper changed')


def _wav(data):
    if not isinstance(data, str) or not data.startswith('data:audio/wav;base64,'):
        raise ValueError('bounded WAV required')
    raw = base64.b64decode(data.split(',', 1)[1], validate=True)
    if not 0 < len(raw) <= MAX_PCM + 4096:
        raise ValueError('bounded WAV required')
    with wave.open(io.BytesIO(raw), 'rb') as audio:
        frames = audio.getnframes()
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                or audio.getframerate() != 16000 or audio.getcomptype() != 'NONE'
                or not 0 < frames <= 480000 or len(audio.readframes(frames)) != frames * 2):
            raise ValueError('16 kHz mono PCM up to 30 seconds required')
    return raw


def handle_request(request, hermes, workdir):
    if not isinstance(request, dict):
        raise ValueError('invalid speech request')
    operation = request.get('operation')
    if operation == 'check':
        if set(request) != {'operation'}:
            raise ValueError('invalid readiness request')
        return {'ok': True, 'ready': True, **hermes.check()}
    if operation not in ('transcribe', 'speak') or set(request) != {'operation', 'body'}:
        raise ValueError('invalid speech operation')
    body = request['body']
    if not isinstance(body, dict):
        raise ValueError('invalid speech body')
    if operation == 'transcribe':
        if set(body) != {'data_url', 'mime_type'} or body['mime_type'] != 'audio/wav':
            raise ValueError('invalid transcription body')
        raw = _wav(body['data_url'])
        audio = Path(workdir) / 'input.wav'
        try:
            fd = os.open(audio, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
            text = hermes.transcribe(audio)
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 4096:
                raise ValueError('transcript unavailable')
            return {'ok': True, 'transcript': text.strip(), 'provider': 'pocket_local'}
        finally:
            audio.unlink(missing_ok=True)
    if set(body) != {'text'} or not isinstance(body['text'], str) or not body['text'].strip() or len(body['text'].encode()) > 4096:
        raise ValueError('bounded speech text required')
    output = Path(workdir) / 'output.mp3'
    try:
        hermes.speak(body['text'], output)
        if output.is_symlink() or not output.is_file() or not 0 < output.stat().st_size <= MAX_AUDIO:
            raise ValueError('bounded speech audio required')
        data = output.read_bytes()
        return {'ok': True, 'provider': 'fishaudio', 'mime_type': 'audio/mpeg',
            'data_url': 'data:audio/mpeg;base64,' + base64.b64encode(data).decode('ascii')}
    finally:
        output.unlink(missing_ok=True)


class HermesProviders:
    def __init__(self, root, pins_file):
        sys.path.insert(0, str(root))
        from hermes_cli.config import load_config
        from tools.transcription_command import _resolve_command_stt_provider_config
        from tools.tts_command_provider import _resolve_command_provider_config
        config = load_config()
        self.stt = config.get('stt') or {}
        self.tts = config.get('tts') or {}
        if (self.stt.get('enabled') is not True or self.stt.get('provider') != 'pocket_local'
                or self.tts.get('provider') != 'fishaudio'):
            raise ValueError('approved speech providers not selected')
        self.stt_command = _resolve_command_stt_provider_config('pocket_local', self.stt)
        self.tts_command = _resolve_command_provider_config('fishaudio', self.tts)
        if not self.stt_command or not self.tts_command:
            raise ValueError('configured command speech providers required')
        raw = Path(pins_file).read_bytes()
        if len(raw) > 16384:
            raise ValueError('invalid speech pins')
        pins = json.loads(raw, object_pairs_hook=_unique)
        if {Path(x).name for x in pins.get('source_files', {})} != {'pocket_local_stt.py', 'fish-audio-tts.sh'}:
            raise ValueError('approved existing helper pins required')
        validate_provider_pins(pins, self.stt_command, self.tts_command)

    def check(self):
        from hermes_cli.auth import _env_secret
        if (importlib.util.find_spec('faster_whisper') is None
                or not all(shutil.which(x) for x in ('bash', 'curl', 'ffmpeg', 'python3'))
                or not _env_secret('FISH_AUDIO_API_KEY')):
            raise ValueError('existing speech runtime unavailable')
        argv = shlex.split(self.stt_command['command'])
        model = next((argv[i + 1] for i, value in enumerate(argv[:-1]) if value == '--model-dir'), None)
        if not model or not Path(model).is_absolute() or not (Path(model) / 'model.bin').is_file():
            raise ValueError('existing local STT cache required')
        return {'stt_provider': 'pocket_local', 'tts_provider': 'fishaudio'}

    def transcribe(self, path):
        from tools.transcription_command import _transcribe_command_stt
        result = _transcribe_command_stt(str(path), 'pocket_local', self.stt_command, self.stt)
        if result.get('success') is not True or result.get('provider') != 'pocket_local':
            raise ValueError('local transcription unavailable')
        return result.get('transcript')

    def speak(self, text, path):
        if self.tts.get('provider') != 'fishaudio':
            raise ValueError('approved speech provider required')
        from tools.tts_command_provider import _generate_command_tts
        from tools.tts_text_normalize import prepare_spoken_text
        from hermes_cli.config import get_env_value_prefer_dotenv
        spoken = prepare_spoken_text(text, max_chars=4096)
        if not spoken.strip():
            raise ValueError('empty speech text')
        name = 'FISH_AUDIO_API_KEY'
        key = get_env_value_prefer_dotenv(name)
        if not isinstance(key, str) or not key.strip():
            raise ValueError('existing Fish credential unavailable')
        previous = os.environ.get(name)
        try:
            # This fixed-operation worker has one request. Only the Fish invocation
            # receives the current profile credential; never return or log it.
            os.environ[name] = key
            _generate_command_tts(spoken, str(path), 'fishaudio', self.tts_command, self.tts)
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', required=True)
    parser.add_argument('--pins', required=True)
    parser.add_argument('--work-dir', required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            raw = sys.stdin.buffer.read(1400001)
            if len(raw) > 1400000:
                raise ValueError('speech request too large')
            request = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            hermes = HermesProviders(Path(args.hermes_root), Path(args.pins))
            result = handle_request(request, hermes, Path(args.work_dir))
    except Exception:
        print(json.dumps({'ok': False, 'error': 'Hermes speech unavailable'}))
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
