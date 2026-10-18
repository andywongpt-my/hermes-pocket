"""One-shot Hermes resolution. Only pinned, allowlisted STT settings leave here."""
import argparse
import contextlib
import json
import logging
import os
from pathlib import Path
import shlex
import sys

if __package__:
    from .hermes_speech_worker import HermesProviders, _unique
    from .stt_worker import validate_settings
else:
    from hermes_speech_worker import HermesProviders, _unique
    from stt_worker import validate_settings


def settings_from_provider(provider, pins):
    command = provider.stt_command
    if (not isinstance(command, dict) or set(command) != {
            'command', 'type', 'format', 'normalize', 'env_passthrough'}
            or command['type'] != 'command' or command['format'] != 'txt'
            or command['normalize'] is not False or command['env_passthrough'] != []
            or not isinstance(command['command'], str) or len(command['command']) > 8192):
        raise ValueError('approved STT definition required')
    argv = shlex.split(command['command'])
    if (len(argv) != 12 or argv[1:3] != ['-I', '-B']
            or argv[4] != '--model-dir' or argv[6:] != [
                '--input', '{input_path}', '--output', '{output_path}', '--language', 'auto']
            or not Path(argv[0]).is_absolute() or not Path(argv[0]).is_file()):
        raise ValueError('approved STT command required')
    sources = pins.get('source_files')
    if not isinstance(sources, dict) or argv[3] not in sources:
        raise ValueError('pinned STT helper required')
    settings = {'helper_path': argv[3], 'helper_sha256': sources[argv[3]],
                'model_path': argv[5], 'language': 'auto'}
    validate_settings(settings)
    return settings


def resolve(root, pins_file):
    # HermesProviders validates the existing source and provider definition pins.
    # Never call check(): that loads Fish authentication/runtime metadata.
    provider = HermesProviders(root, pins_file)
    raw = Path(pins_file).read_bytes()
    if len(raw) > 16384:
        raise ValueError('pins size')
    pins = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
    return settings_from_provider(provider, pins)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', required=True)
    parser.add_argument('--pins', required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            settings = resolve(Path(args.hermes_root), Path(args.pins))
    except Exception:
        print('{"ok":false}')
        return 1
    print(json.dumps({'ok': True, 'settings': settings}, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
