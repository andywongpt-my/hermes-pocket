# Native 24-kHz AEC: implementation and remaining synchronization gate

`pocket/live_echo.py` supplies a real host-side SpeexDSP **1.2.1** acoustic echo
canceller using its supported public C ABI. It does not mute the microphone
during speaker playback. It is optional, has no import-time native load, and
neither installs dependencies nor enables a listener/provider/session.

## Paired-reference continuation

Protocol v2 now has a concrete co-captured source: ES8311 microphone-left and
internal-DAC-reference-right from the same RX stereo frame. `PairedSpeexEcho`
processes 240 pairs / 10 ms directly, using the same native engine with an audited
240-sample frame size. The legacy explicit-reference adapter below remains for
v1; its missing reference-owner limitation is not silently removed or guessed.
See [v2 protocol](GPT-LIVE-DEVICE-V2.md). Physical lane/clock/acoustic acceptance
is still required, and digital DMA credit never becomes audible-playout truth.

## Default 20-ms API and v1 adapter

- `SpeexEchoCanceller(absolute_library_path)` owns one mono state: 24,000 Hz,
  480 samples/20 ms per frame, 4,800 samples/200 ms adaptive echo tail.
- `process(capture_pcm, render_reference_pcm)` accepts two exact 960-byte PCM16
  little-endian frames and returns one processed frame. Invalid lengths/types
  fail before C access; little-endian conversion is explicit.
- Initialization checks the native sample rate and frame size. Allocation,
  missing symbols/library and format errors use bounded fixed error codes.
  Close/reset and native processing are serialized; close is idempotent and
  forgotten native states have a finalizer. No private Speex struct is read.
- `SynchronizedSpeexEcho` adapts the engine to `DeviceAudioBridge` only when
  given an explicit reference owner. Session cleanup calls optional
  `echo.aclose()` with the existing close deadline, including failed startup.
- Source offsets, capture offsets and played acknowledgments are checked for
  continuity. Missing/malformed reference fails; audio is never silently passed
  through as a fallback. No noise gate or automatic gain policy was added.

## The unsolved part is not hidden

An actual DMA-completed source position is necessary but **not sufficient** for
AEC. Cumulative source counts omit where inserted silence occurred. The new BSP
render ledger emits source/silence spans on its TX DMA timeline, but its origin
is not yet tied to the microphone DMA timeline or codec analog latency.

The current v1 binary packet transports a cumulative rendered count, **not**
those aligned spans. There is therefore no built-in production reference owner
for a physical Pocket and no ready-to-enable duplex route. The reference owner
must provide bounded, capture-clock-aligned samples, account for silence and
underruns, and reject unknown/reset epochs. It must not reconstruct timing from
network arrival time, queued byte counts or v1 acknowledgments alone.

The standalone native engine can be used/tested with genuinely synchronized
paired PCM. The adapter requires these asynchronous methods on a fresh owner:

```python
async def playback(pcm: bytes, source_offset: int): ...
async def capture_reference(capture_offset: int, played_samples: int) -> bytes: ...
async def aclose(): ...
```

These signatures specify a required interface, not an example implementation
or an assertion that a reference source exists. Callbacks must cooperate with
cancellation. Native code is trusted code; supplying an arbitrary shared
library is not supported or sandboxed.

## Reproduce the offline library build

Use a private development directory. The official source archive is:

`https://downloads.xiph.org/releases/speex/speexdsp-1.2.1.tar.gz`

Expected SHA-256:
`8c777343e4a6399569c72abc38a95b24db56882c83dbdb6c6424a5f4aeb54d3d`.

Verify it before extraction, then build without system installation:

```sh
./configure --disable-static --enable-shared --disable-examples
make -j2
# From the Hermes Pocket source directory, with an absolute development path:
POCKET_TEST_SPEEXDSP_LIBRARY=/absolute/speexdsp-1.2.1/libspeexdsp/.libs/libspeexdsp.so.1.5.2 \
  python -m unittest tests.test_live_echo -v
```

The test opt-in does not configure production library loading. Without it,
the suite reports native-AEC checks as skipped instead of pretending a mock
proved cancellation. No system/service installation or key is needed.

The real-library synthetic test uses a deterministic delayed FIR echo plus
independent near-end audio. It asserts more than 15 dB echo reduction after
adaptation and preserves near-end correlation during double-talk. This proves
the tested algorithm path is neither a no-op nor a mute. It does **not** prove
physical Pocket acoustics, clipping behavior, delay calibration, noise robustness,
clock stability, real-world double-talk quality or speaker volume safety.

## Source and license

- [Official stable release and downloads](https://www.speex.org/downloads/).
- [Official API manual](https://www.speex.org/docs/manual/speex-manual/node7.html)
  recommends synchronized `speex_echo_cancellation` rather than relying on the
  soundcard buffering assumption of separate capture/playback calls.
- [Pinned public header](https://github.com/xiph/speexdsp/blob/SpeexDSP-1.2.1/include/speex/speex_echo.h).
- [Official release announcement](https://lists.xiph.org/pipermail/speex-dev/2022-July/008662.html).

SpeexDSP is BSD-3-Clause; [its notice](licenses/SpeexDSP-BSD.txt) is retained.
The source checkpoint does not redistribute the native library or SDK binaries.
