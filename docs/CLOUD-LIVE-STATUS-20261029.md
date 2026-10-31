# GPT-Live cloud source checkpoint — 29 October 2026

## Result

Implemented and offline-tested genuine **gpt-live-1 + Hermes client delegation**
components. This is **not ready-to-flash or a verified physical duplex release**.
Current Pocket voice remains the existing 16 kHz half-duplex implementation.

- Fixed Python 3.12 capture-loop starvation and Stop during SDK/startup waits.
- Explicit Live reconnect controls: `max_retries=0, on_reconnecting=None`.
  SDK 3.41.0 already gated reconnect on its default absent callback; the five-retry
  budget alone did not demonstrate automatic reconnection.
- Added native-24-kHz bounded device framing, playout acknowledgment validation,
  actual pending-playback reporting, and existing durable Hermes integration.
- Added an opt-in same-server WebSocket route with existing bearer/TLS boundaries,
  no new listener/default enablement, device-target admission, message/fragment
  bounds, shutdown, cancellation-safe writes and honest terminal/error statuses.
- Added no-allocation C framing and an **inactive** firmware handshake/FIFO core;
  four speaker frames consume at most a 4,200-byte client struct.
- Added actual-BSP/fake-I2S tests for native24k, format reuse/sleep/revert-to16k and
  an idle-DMA counter regression; these are not hardware acoustic tests.
- Fixed four GCC-only embedded-test-harness indentation errors without weakening
  warnings or changing the associated legacy production code.

## Verification

| Check | Observed result |
| --- | --- |
| GPT-Live, device adapter, WebSocket and firmware Live prerequisites | **93 passed** in the final complete run |
| Complete repository unittest discovery | **499 tests; 5 failures, 3 errors, 11 skips**, 84.871 seconds — not an aggregate pass |
| Simulator | Passed, synthetic providers only; zero duplicate dispatches |
| Independent reviews | Delivered host/route/client scope reviewed; important lifecycle/accounting/terminal findings fixed with red→green regressions |
| C wire memory-safety checks | Ten wire cases plus 40,000 bounded malformed/random decoder cases passed ASan/UBSan; LSan unavailable under runtime ptrace |
| Actual ESP-IDF build/hardware/provider | Not performed or implied |

Some unavailable native test classes skip at setup, so total test counts differ
from the earlier Windows run. Do not interpret a smaller count as removing tests.

### Eight blocked checks (still reported failed/error)

The current cloud sandbox lacks `/proc/<pid>/task/<pid>/children`, including
self/thread-self aliases. `PR_SET_CHILD_SUBREAPER` succeeds, but the unchanged
speech supervisor preflight fails before starting the synthetic worker. This
explains the following five failures and three errors. The containment boundary
was not weakened, the checks were not skipped, and no worker success was invented.

- **ERROR:** `test_existing_audio_adapter_works_through_worker_without_network (test_hermes_speech.TransportTests.test_existing_audio_adapter_works_through_worker_without_network)`
- **ERROR:** `test_transport_strips_parent_credentials_and_keeps_provider_calls_out_of_check (test_hermes_speech.TransportTests.test_transport_strips_parent_credentials_and_keeps_provider_calls_out_of_check)`
- **ERROR:** `test_resident_initialization_checks_once_and_routes_only_stt_to_pool (test_resident_integration.TransportResidentTests.test_resident_initialization_checks_once_and_routes_only_stt_to_pool)`
- **FAIL:** `test_exited_worker_cannot_leave_provider_session_alive (test_hermes_speech.TransportTests.test_exited_worker_cannot_leave_provider_session_alive)`
- **FAIL:** `test_output_limit_stops_noisy_worker_before_its_timeout (test_hermes_speech.TransportTests.test_output_limit_stops_noisy_worker_before_its_timeout)`
- **FAIL:** `test_timeout_kills_nested_provider_session (test_hermes_speech.TransportTests.test_timeout_kills_nested_provider_session)`
- **FAIL:** `test_timeout_reaps_an_actively_forking_provider_tree (test_hermes_speech.TransportTests.test_timeout_reaps_an_actively_forking_provider_tree)`
- **FAIL:** `test_cancelled_tts_worker_is_reaped_without_closing_resident_stt (test_segmented_speech.V2TransportTests.test_cancelled_tts_worker_is_reaped_without_closing_resident_stt)`

The exact dependency skips are recorded in `verification/final-summary.json`.
They require unavailable pinned cJSON/mbedTLS/codec/LVGL sources; they are not
replaced with mock claims about a full firmware build.

## Concrete remaining blockers

1. **Actual rendered-audio position:** `bsp_audio.c:41–56` discards DMA descriptor
   identity and counts idle completions; lines 587–590 expose write acceptance,
   while lines 605–607 guarantee final-ring drain only. A test proves DMA counts
   can advance with zero speech writes. Implementing a real worker needs an audited
   public API tying mono source offsets to actual completed descriptors, including
   underrun/cancel/format epochs and bounded stop/join semantics. Do not fabricate
   `played_samples` from queued bytes, elapsed time or raw completion count.
2. **Supported AEC and playout synchronization:** the mandatory callback boundary
   has synthetic fixtures only. No usable supported AEC bindings/development
   package are installed here. A microphone mute gate is not full duplex.
3. **Exact pinned dependencies and physical acceptance:** codec-dev1.6.2 inspection,
   complete firmware build, concurrent TLS/audio heap headroom, clock stability,
   echo/double-talk, underruns/Wi-Fi jitter and buttons/teardown remain unverified.
4. **Complete Linux security-suite verification:** run unchanged tests on a Linux
   executor exposing the procfs children interface; current status is blocked.
5. **Separately authorized activation:** real project/model access, dedicated host
   key configuration, a bounded paid test, service changes and hardware flash still
   require the owner's applicable confirmation. No such action was attempted.

## Scope and safety

No key was received/configured, no API client/provider request made, no service or
network/security setting changed, no firmware flashed, and no commit/push/merge or
public publication performed. No ordinary Realtime substitution exists. Existing
Hermes journal/history/tools/approval/unknown semantics remain; stopping speech
never claims to cancel a backend task. The optional route is off by default and
requires explicit caller-provided client plus echo processor. New firmware core
files are not wired into CMake or the current UI/worker.

Native24k is the prospective no-new-hardware choice, supported by BSP boundary
characterization, not a proven physical codec clock. No 16k audio is relabeled.
All physical hardware, AEC and provider limitations remain explicit release gates.

## Source provenance and continuation

Input Library snapshot: `private-source-checkpoint (identifier omitted for public publication)`.
Input ZIP SHA-256: `4e5576b2801aa5a834d666ec26591d6780f2641e6d7d6ff25173b274f48cd500`.
All 345 input source entries were hash/size verified before work. This checkpoint
retains the original source and pending work, with intentional changes enumerated
in `cloud-manifest.json` and `cloud-changes.patch`. There is no `.git` directory.
Base commit remains `e36365aee01d6418434faf590db425ac08f92cc1`; no new commit exists.
Original historical evidence/patch/handoff are included separately, unchanged.

See `docs/GPT-LIVE.md` and `docs/GPT-LIVE-DEVICE-PROTOCOL.md` for interfaces. Read
this status before continuing. Keep the current physical release blocked until
render/AEC/hardware/provider gates are genuinely verified.
