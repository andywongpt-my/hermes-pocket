# GPT-Live source handoff — 31 October 2026

**Implemented and cloud-tested; not an activated or physically qualified Live
release.** This checkpoint publishes the completed source work without merging
main, changing an installed service or operating a device.

## Start here

- Host: `pocket/gpt_live.py`, `live_hermes.py`, `live_websocket.py`,
  `live_device.py`, `live_device_v2.py`, `live_paired_echo.py` and `run_bridge.py`.
- Firmware: `firmware/core/pocket_live_*`, private IDF transport and paired-audio
  lifecycle in `firmware/main/`, and callback capture/render in `components/bsp/`.
- Current integration contracts: [app caller](GPT-LIVE-APP-CALLER.md),
  [host embedding](GPT-LIVE-HOST-WIRING.md),
  [C-worker integration](GPT-LIVE-C-WORKER-INTEGRATION.md),
  [v2 protocol](GPT-LIVE-DEVICE-V2.md), [AEC](GPT-LIVE-AEC.md).
- Latest diagnostic: [numeric observations](OFFLINE-NUMERIC-OBSERVATIONS.md) and
  [pinned codec behavior](PINNED-CODEC-REGISTER-TRACE.md).

The bridge targets `gpt-live-1` with Hermes client delegation, not ordinary
Realtime. Implemented pieces include bounded bidirectional audio/framing,
authenticated same-server WebSockets, paired mic/reference transport, native
SpeexDSP integration, task-journal delegation, Stop/terminal handling and
ownership-aware cleanup. Synthetic integration composes the actual C worker,
WebSocket bridge, AEC and durable journal with fake provider/backend boundaries.

Production `pocket_live_admission.c` still returns readiness false. The optional
normal-app caller fails closed before Live allocation, network or codec work;
there is no supported setting to turn this into a validated release. The latest
built candidate enables offline diagnostic + BSP callbacks, with qualification
and Live caller/paired-worker gates disabled. Default builds keep the existing
voice path. Host opt-in requires an explicitly supplied endpoint/client and fresh
paired echo factory; the existing CLI does not silently activate Live.

## Latest verification, not an aggregate pass

The full cloud suite ran **841 tests: 833 passed, 5 failures, 3 errors, zero skips**
(389.475 s). All eight new numeric/codec/UI checks passed. The eight exceptions
are the historical worker/process-containment checks in `test_hermes_speech`,
`test_segmented_speech` and `test_resident_integration`. This executor lacks
`/proc/<pid>/task/<pid>/children` (ENOENT); containment was not weakened, tests
were not hidden/skipped, and real worker success is not claimed.

Four firmware builds passed with ESP-IDF 5.5.3, GCC 14.2 esp-20251107 and locked
managed components: enabled, separate clean enabled, default-off and original
source default-off. Enabled binaries/configs match exactly. All 14 allocated
ELF sections match between default-off new/original source. Complete default
binaries differ only in embedded ELF hash and resulting image footer checksums.
The historical source-only checkpoint document is not the current build status.

The numeric UI passed actual LVGL 9.5.0 bounds across 120 phase/pane/cleanup
combinations; ten synthetic examples were independently inspected. Fixed-memory
core fixtures distinguish early/late/scattered rail events without discarding
startup or retaining PCM history. Sanitizer checks passed with leak checking
explicitly unavailable in this sandbox. Pinned codec tests run actual 1.6.2
ES8311 code against fake register interfaces, verify all 97 dependency files and
reject register-behavior/identity mutations; they are not device readbacks.

Reproduce using the repository tests and each linked document's explicit native
pin/path requirements. Missing optional dependencies can produce skips on another
host; those are not equivalent to the dependency-complete cloud run. Existing CI
has not been expanded into complete physical or provider qualification.

## Diagnostic device outcomes (operator reports)

An earlier silent run reported 882/1000 frames, MIC RMS 2683, peak 32768,
1030 endpoint samples, REF zero, code 1 (Stopped) and cleanup checked. These
cumulative values cannot locate the clipping in time or diagnose a bad mic.
The source's `set_in_gain(30)` is not solely analog PGA control: pinned driver
writes register 16 from 0x24 to 0x05, also clearing ADC_SYNC. This checkpoint
corrects the comment and characterizes those writes but **does not change gain
or ADC_SYNC**, nor assert that they caused the reported clipping.

The later numeric trial reported code 2, zero samples and cleanup not started.
In this source, code 2 is `PDIAG_EXPIRED`: the ten-second armed/queued window
expired before capture. It supplies no microphone measurement or passing audio
test, and the photos alone cannot distinguish late confirmation from delayed
worker dispatch. The Windows operator task reports original firmware restored
and boot verified at **2026-10-31 06:56:56 UTC**, with the complete recovery window,
bootloader and partition table matching its pinned backup. This publication
performed no independent physical read/write or new device trial.

## Recovery and remaining acceptance

Preserve **[0x10000,0x271000)** recovery coverage. The cloud numeric app was
2,456,272 bytes, version `offline-obs-1`, SHA256
`7bcac4aead7b4b432ec6eadf8598160a8814bbba3ee88474663bd7f6459f5b32`;
its sector end is 0x268000. No app image, bootloader, partition/NVS image, raw
recovery/audio, credentials or runtime owner configuration is committed here.
The source's broader partition-slot guard is not physical layout verification.

Still unverified: physical mic/ref identity and clock, clipping cause, acoustic
AEC/double-talk, actual audible playout, target scheduling/Stop latency and
runtime heap/stack under concurrent TLS/audio/UI, real project/model access,
provider operation, deployed host configuration and end-to-end user voice flow.
DMA completion is not acoustic playback. Software deadlines do not preempt a
stuck SDK/I2C call. Cleanup uncertainty retains ownership instead of freeing
possibly active resources. No silent reconnect, task replay or legacy-provider
fallback should be added.

## Concrete continuation

1. Trace the existing fresh-press, armed/queued expiry and worker dispatch path;
   do not interpret the latest zero-sample expiry as a microphone defect.
2. Finish the normal-app/host integration from these existing components rather
   than restarting them. Keep readiness/cleanup gates and backend task semantics
   explicit; stopping speech must not pretend to cancel Hermes work.
3. Rerun the eight unchanged containment checks on a suitable Linux host.
4. Treat hardware/provider/service activation and legacy retirement as separate
   acceptance/authorization steps. This source publication does not open them.

## Provenance and publication scope

Base: `e36365aee01d6418434faf590db425ac08f92cc1` on
`checkpoint/all-project-work-20261023`, still the remote head at inspection.
Source was taken from
`hermes-pocket-normal-offline-numeric-no-export-cloud-candidate-20261031.zip`,
SHA256 `5d54419c3aad0bdfb407af02c76949359661b923ffe142063726965a986c003f`.
Source bytes were compared to that archive with only line-ending/trailing-whitespace normalization
for Git conventions, plus this handoff/README and documented publication-only
sanitization. Existing main, PR #3, other branches and unrelated files are not
updated. Raw cloud build/test logs, local SDK/dependency caches, private paths
and binary artifacts remain outside the repository; summarized results are
provided above without presenting failed or unrun checks as successes.

Publication-only privacy edits remove three internal Library record identifiers
from historical docs (source hashes retained) and replace a synthetic Windows
fixture username with `Example`. No runtime configuration or audio behavior is
changed by these edits.

Two pre-existing trailing-space findings were removed (a blank C line and a
Python fixture closing delimiter); no executable statement or fixture payload
changed. The publication-only path fixture rerun passed all eight tests.
