# Optional existing-runtime speech helper

`pocket_local_stt.py` is a cloud-prepared command-provider helper for an existing
Linux Python environment with faster-whisper already installed. It does not install
packages or download models. Supply an absolute cached CTranslate2 model directory
containing model.bin. CPU inference is limited to two threads and one worker.

The helper accepts `--model-dir`, `--input`, `--output` and optional `--language auto`.
Input is bounded to 30 seconds of 16 kHz mono 16-bit PCM WAV. A nonempty transcript
of at most 4096 UTF-8 bytes is written to a new output file with mode 0600. It never
overwrites an existing output and does not print the transcript or credentials.
`--check --model-dir ...` only loads the cached model; it does not process audio.

Configure it only after verifying the installed Hermes command-provider schema,
its temporary-file output contract and config reload behavior. Use the existing
compatible Python interpreter explicitly. Enabling STT or changing a live Hermes
configuration remains a separate owner-approved deployment step. Preserve the
configured TTS service and do not restart a running agent merely to load this helper.

Five fake-model unit checks pass; no real local model has yet been loaded by the
cloud implementation. The complete current offline suite contains 96 passing tests.
Current firmware verification is documented in ../docs/VERIFICATION.md.
