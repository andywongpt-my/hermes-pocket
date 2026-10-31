> **Superseded experiment:** The current candidate is the normal-firmware SILENT
> diagnostic, not the standalone bench image or raw USB/tone path below. Retained
> for provenance only; do not run these older device instructions for this trial.
> See `NORMAL-DIAGNOSTIC-DEVICE-PROCEDURE.md` and `GPT-LIVE-OFFLINE-IMAGE.md`.

# Offline bench app candidate — not installed or device-validated

This is a **separate temporary application**, not the normal Pocket UI, a Live
client or an activated service. Build and offline checks passed; no device was
connected, flashed, read back, recorded or acoustically tested.

## Exact application image

| Field | Verified value |
| --- | --- |
| Candidate file | `hermes-pocket-bench-20261029.bin` (may be packaged as `app.bin`; bytes/hash must match) |
| Full-file SHA-256 | `b25bdff434c8d79739bd72b45f47874942dc4236d10ae4a235fcc62f629ea9e1` |
| Length | 239,632 bytes (`0x3a810`) |
| Project / version | `hermes-pocket-bench` / `bench-1` |
| Target | ESP32-C3, image chip ID 5 |
| Supported chip revisions in header | 0.3 through 1.99 |
| Flash header | DIO, 80 MHz, 8 MB |
| Intended application slot | `0x10000`; source layout reserves `0x7f0000` bytes |
| Image checksum / appended validation hash | Both verified valid by esptool 4.12.0 |
| SDK / secure version | ESP-IDF 5.5.3 / secure version 0 |

The intended slot is **not evidence of the currently installed layout**. The
image is not supplied as a signed/encrypted release. Secure Boot, flash
encryption, anti-rollback or an incompatible chip/bootloader/layout may make it
unsuitable. Never override a security or compatibility rejection.

Only the application image is a candidate deliverable. Do not install generated
bootloader/partition images, erase flash, modify NVS, or use a combined image.
The original source partition CSV and dependency lock are unchanged.

## Build and boundary verification

- Official ESP-IDF v5.5.3 at `2c211b236707889e8400c4dc5644dd5c4ee071e0`,
  RISC-V GCC 14.2.0 (`esp-14.2.0_20251107`), CMake 3.30.2, fixed project version,
  and `CONFIG_APP_REPRODUCIBLE_BUILD=y`. The compile timestamp is blank.
- Final build and a fresh build from an empty build directory produced
  **byte-identical application images**. This is same-environment clean-build
  reproducibility, not a tested cross-machine claim.
- All 126 firmware source files matched the build snapshot. All five managed
  dependency content hashes match the unchanged lock, including codec 1.6.2.
- Bench `main` contains only `bench_main.c` and `bench_core.c`, using the existing
  BSP. The normal firmware main/core/UI/Live sources are not compilation inputs.
- Minimal component closure excludes Wi-Fi, Bluetooth, lwIP, NVS and TCP
  transport. Final executable/undefined-symbol scans found no network/TLS/socket,
  NVS-write, OTA-write, partition-write, normal UI or Live functions. No bearer,
  authorization, provider-key or Live-route markers were found in image bytes.
  The linked OTA helper is read-only `esp_ota_get_running_partition`.
- These are static build/source boundary checks, not a formal whole-program
  security proof or a substitute for hardware validation.

Static D/IRAM use is **80,708 bytes**, with **240,588 bytes** remaining in the
linker layout. Main task stack is configured to 8,192 bytes. Compiler direct
frames include `bench_step` 1,008 bytes, `audio_capture` 992 bytes,
`report` 672 bytes and `app_main` 48 bytes. The nested main→step→capture path
already accounts for 2,048 bytes before lower-layer calls. This is not a complete
worst-case stack bound. USB buffers, queues, I2S DMA, task stacks and allocator
overhead consume runtime memory; no heap/stack high-water measurement occurred.

RX/TX/overflow callbacks, capture/render completion cores and their audited timer
and critical-section helpers are in IRAM; callback state is internal DRAM. The
capture/render ISR cores contain no function calls in target disassembly. Actual
interrupt duration, cache-off behavior and Stop latency remain unmeasured.

The normal default firmware was separately rebuilt after BSP safety changes:
2,427,760-byte image; static D/IRAM 182,484 used / 138,812 remaining. That build
is **not a device-proven recovery image** and is not part of this candidate bundle.

## Finite behavior, not acoustic acceptance

The application is idle at boot: it does not capture or emit periodic records.
Raw USB must receive one exact `ARM SILENT` or `ARM TONE` command, and a fresh
physical OK press followed by a click must occur within ten seconds. USB alone
cannot start audio. A pre-held button cannot grant consent. One attempt is allowed
per boot; Stop/failure cannot silently rearm it.

- Startup has a two-second software budget. Hardware operations are serialized;
  this is not an independent hardware watchdog or a proven hard real-time bound.
- **SILENT:** checked hardware mute, no source samples submitted, at most 1,000
  paired 240-frame captures; acquisition budget 10.25 seconds.
- **TONE:** at most 500 paired captures and 120,000 source samples; acquisition
  budget 5.25 seconds. The fixed source schedule contains three 200 ms 1 kHz
  bursts, each with 10 ms ramps, peak at most 512/32768 and checked volume at
  most 5. Initial/interburst/final silence is included; no automatic volume rise.
- Any active physical button press, USB `STOP`, invalid input, deadline/queue/I/O
  failure or clock regression terminates the attempt. Cleanup checks mute,
  paired-mode stop, legacy restore and sleep before terminal status.

These numeric sound limits are **not a calibrated SPL or hearing-safety claim**.
Source offsets are not acoustic playback timestamps. A clean `done` status proves
only the finite software/export/restoration conditions, not correct lanes,
clock accuracy, echo cancellation or audible drain.

## Minimum approval and recovery checklist

**Do not install this merely because the build passed.** Before a physical
operation, the owner and operator must establish all of the following:

1. Identify the exact existing Pocket/connection and confirm its chip revision,
   flash/security state, bootloader compatibility and actual application slot.
   Any required device query/readback needs its own applicable authorization;
   none was performed while producing this image.
2. Have a hashed, known-good **device-proven** recovery application and the
   owner's documented recovery/bootloader method ready. A newly compiled normal
   image is not sufficient evidence. If recovery material is missing, stop and
   obtain it through separately authorized steps; do not read/export NVS or
   perform an unapproved full-flash backup.
3. Record user-visible target/volume/settings without copying secrets. Confirm
   stable power and the actual power-off method; unplugging USB may not stop a
   battery-powered device.
4. Obtain explicit approval naming this SHA-256, that device, a **temporary
   application-only write to the verified slot**, and the intended profiles.
   Default to SILENT only unless TONE is also expressly approved. Approval must
   also cover the planned restoration of the known-good app. It does not permit
   bootloader/partition/NVS changes, erase, forced compatibility/security bypass,
   service changes, provider calls or unattended repeated trials.
5. Recheck the local file's full hash immediately before the approved operation.
   Use the existing trusted application-only installation workflow. If readback
   verification is authorized, restrict it to the application bytes just written;
   do not expand it to secret-bearing settings or the entire flash.
6. Reboot to the idle bench. Prepare the local collector using its separate
   instructions. Run the authorized SILENT profile first with fresh physical
   consent. Missing/corrupt/incomplete terminal records are a failure. TONE may
   follow only within the approved scope and after the silent run is acceptable;
   the one-shot latch requires a deliberate reboot/new arm.
7. Keep the device on a table away from ears. Unexpected/harsh sound, discomfort,
   repeated output, invalid lanes, lost data or failed Stop means abort. Try local
   Stop; if software does not respond, use the previously confirmed power-off
   method. Do not improvise new hardware, a different flash layout or a force flag.
8. Restore the approved known-good application in the same verified slot and
   check user-visible settings and legacy operation. Absence of NVS writes is a
   design boundary, not proof that restoration succeeded. Report failures or
   uncertain ownership; do not continue testing through them.

No runnable flash script or combined-flash command is included. This candidate
does not authorize its own installation, nor any GPT-Live/account test afterward.
Keep microphone captures private/local; follow the collector's retention guidance.

Detailed build/header/hash, stack, ISR, dependency and source receipts are in the
source checkpoint's `verification/bench/` directory. The broader future acoustic
acceptance plan is separate; this finite image does not implement every future
stress, AEC or heap-measurement experiment.
