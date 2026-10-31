# Finite Offline Bench Implementation Plan

> For agentic workers: execute with test-driven implementation and independent review. No commits or device operations are authorized.

**Goal:** Prepare a separately built, no-key, finite bench-only app and exact image plus a strict local sample collector, without flashing or touching a device.

**Architecture:** This implements the approved continuation of `GPT-LIVE-PAIRED-BENCH-PLAN.md`. A separate minimal ESP-IDF project avoids the normal application startup and all network/NVS/key paths. USB admission can only arm; physical confirmation starts one finite run per boot. Same-frame mic/reference data travels through bounded checksummed records, never a provider.

**Tech stack:** Existing pinned ESP-IDF 5.5.3, ES8311 BSP, native USB Serial/JTAG, C safety core and Python offline parser/optional explicit-port collector.

## Decisions and rejected alternatives

- Separate bench project rather than a normal-UI switch: stronger separation from Wi-Fi, pairing, saved credentials and live activation.
- Stream over existing native USB with bounded admission rather than retain seconds in scarce C3 RAM or add hardware. A slow/disconnected host fails, never drops frames silently.
- Physical start plus host arm, not automatic boot/serial-only playback. Any active-run button press stops. One terminal run per boot prevents accidental repeats.
- SILENT: 1,000 capture frames / 10 seconds, volume zero. TONE qualification: 500 frames / 5 seconds, volume at most 5%, three 200-ms ramped 1-kHz bursts with peak 512 at source offsets 24,000, 52,800 and 81,600. These are source-time positions, not measured audible timing. No volume escalation and no calibrated SPL assertion.
- Checked codec volume and zero preload are required before nonzero source admission; mute/restore/sleep errors remain terminal failures. No MCU-controlled PA kill exists, so software cannot guarantee a physical fail-safe.
- Binary records: 28-byte little-endian `<4sBBHIIIII>` header: `HPB1`, kind 1 status / 2 capture, flags 0, reserved 0, epoch, global sequence, capture sample offset (zero for status), payload size <=1,024, CRC32 IEEE. CRC covers first 24 header bytes followed by payload. Capture payload is 960 bytes / 240 pairs. No log interleaving after structured output starts.

## Tasks

- [ ] Checked-volume BSP API: red tests for I/O failure/readback; preserve existing legacy API; test zero/init/recreate/cleanup.
- [ ] Pure bench control, stimulus and wire core: red tests for arm expiry, held/stale input, Stop races, source/time caps, busy retention, overflow, CRC and terminal ownership; sanitizer checks.
- [ ] Minimal separate IDF launcher: USB-only, no NVS/network init, physical confirmation, bounded read/write operations, mute/stop before reporting; compile exact SDK and verify call graph.
- [ ] Python record validator/collector: red tests for literal framing, CRC corruption, gaps, metadata changes, truncated/missing terminal, strict bounds, stale records, duplicate keys and refusal to overwrite; fake serial only in tests.
- [ ] Independent review and source-matched bench/default builds. Inspect exact app image and hashes without using flash or device tools.
- [ ] Preserve source checkpoint separately from candidate app artifact. Provide minimum approval-gated procedure with recovery/installed-partition facts explicitly unresolved.

## Completion boundary

Cloud completion means reviewed source, passing focused checks, exact reproducible build evidence and a candidate image. It does not establish device identity, installed partition compatibility, boot/security state, physical sound level, lanes/clock/acoustics, hardware Stop timing or recovery success. Device access, readback, app-only flash and physical recording require separate explicit approval; bootloader/partition/NVS writes remain excluded.
