# Native C worker / host bridge integration (cloud-only)

The opt-in `tests/test_live_c_worker_integration.py` composes the **actual**
`pocket_live_v2_worker`, strict v2 cJSON control parser, v2 wire codec and bounded
speaker FIFO with the existing authenticated loopback WebSocket server, Python
`run_device_gpt_live`, paired-device adapter, native SpeexDSP AEC and durable
Hermes task journal. The provider is a fake `gpt-live-1` connection; no SDK client,
provider credential or paid API is used. No device, firmware activation or
production listener is involved.

The test-only C fixture substitutes pipe transport and audio callbacks. Python
relays the pipes through real WebSockets, deliberately fragmenting both pipe
records and WebSocket messages. The C fixture keeps bounded framing storage,
a 1,920-sample synthetic source FIFO, and 1,024-sample echo history. Its 240-sample
co-captured blocks are paced at least 10 ms apart. This is synthetic audio, **not
an ESP32 scheduling, DMA, codec-lane or acoustic acceptance test**.

## What is checked

* 1,300 contiguous C capture packets traverse the complete path and native AEC.
  Each right-channel source sample is checked against the fake provider's
  deterministic output and digital-consumption counter, including underrun
  silence; the left channel contains a delayed FIR echo and later near speech.
* After adaptation, echo-only energy reduction exceeds 15 dB; later independent
  near speech retains correlation above 0.5. Neither muting all capture nor
  passing through only the reference can satisfy both assertions.
* The host requests exactly `gpt-live-1` with client delegation. One delegated
  request becomes one durable Hermes task, and its completed result returns as
  Live commentary. The fake backend does not test real tools or memory access.
* Initial busy output fills all four C speaker slots. Provider output exceeds
  the host's 1,920-source-sample credit window; source admission remains bounded.
* Stop while output cannot submit releases the host credit wait and receives a
  finalized terminal without claiming that any sound was actually played.
* A separate Stop case leaves a still-running Hermes task intact in the
  reopened journal and reports `backend_pending=true`; its fake backend fails
  immediately if voice Stop attempts backend cancellation.
* A C capture callback reporting consumption beyond copied source is rejected
  before its invalid packet can upload. A tampered stale credit is rejected by
  the real host and becomes a fixed unconfirmed-session error.
* The actual C parser rejects a terminal altered to claim known physical
  playout. Successful v2 terminals explicitly preserve `actual_playout=unknown`
  and conservative `playback_pending=true` after output.
* The owned C subprocess, per-session provider context, native echo processor,
  loopback server and journal are cleaned up. No endpoint client ownership is
  transferred to the host constructor.

## Reproducible opt-in

Tests never download or install dependencies. Supply already verified local
native/source dependencies explicitly:

```sh
POCKET_TEST_SPEEXDSP_LIBRARY=/absolute/path/to/libspeexdsp.so.1.5.2 \
POCKET_CJSON_DIR=/absolute/path/to/pinned/cjson \
python -m unittest tests.test_live_c_worker_integration -v
```

GCC compiles the real C sources using C11, `-Wall -Wextra -Werror -Wconversion
-Wshadow -pedantic`. `POCKET_CJSON_DIR` must contain `cJSON.c` and `cJSON.h`.
Without explicit native Speex/GCC/cJSON availability the tests report a skip,
not simulated AEC success. The audited dependency versions used in cloud are:

* SpeexDSP 1.2.1, official source tarball SHA-256
  `8c777343e4a6399569c72abc38a95b24db56882c83dbdb6c6424a5f4aeb54d3d`.
* cJSON commit `c859b25da02955fef659d658b8f324b5cde87be3`, the submodule pinned
  by ESP-IDF 5.5.3 commit `2c211b236707889e8400c4dc5644dd5c4ee071e0`.

The dependency source/build logs and test evidence are separate from the source
checkpoint; native binaries are not needed or bundled in the project source.

## Boundaries that remain

This closes the previous **C worker versus Python fake-device test separation**.
It does not join the ESP-IDF transport/BSP implementation to a physical device.
It does not establish physical acoustic quality, real provider availability,
real Hermes tool execution, user-facing Live activation or deployment approval.
The public device-validation gate remains closed. The normal-app lifecycle is
separate, inactive work, and host embedding remains explicit/default-off; see
[host wiring](GPT-LIVE-HOST-WIRING.md) and
[paired worker](GPT-LIVE-PAIRED-WORKER.md).
