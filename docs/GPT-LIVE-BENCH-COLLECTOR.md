> **Superseded experiment:** The current candidate is the normal-firmware SILENT
> diagnostic, not the standalone bench image or raw USB/tone path below. Retained
> for provenance only; do not run these older device instructions for this trial.
> See `NORMAL-DIAGNOSTIC-DEVICE-PROCEDURE.md` and `GPT-LIVE-OFFLINE-IMAGE.md`.

# Offline bench collector — explicit device permission required

The code is prepared and tested with synthetic data and fake serial objects;
**no physical device was opened, reset, flashed, recorded or played here**.
The standalone image in `firmware/bench` is separate from normal Pocket firmware.
Its finite qualification subset is not the full AEC acceptance programme.

## What this image can do

- One attempted run per boot, after both USB `ARM` and a fresh physical OK
  press/release within 10 seconds. A held or stale click cannot start it.
- SILENT: exactly 10 seconds / 1,000 paired capture frames with checked true
  hardware mute and zero submitted source samples.
- TONE: exactly 5 seconds / 500 paired capture frames; three fixed 200-ms ramped
  1-kHz source bursts, peak 512, volume setting no greater than 5. No automatic
  escalation. These limits do not certify a safe acoustic sound-pressure level.
- Any active-run button press or USB STOP terminates. Software observes a 2-second
  setup budget and 250-ms acquisition/export allowance. A blocked MCU/driver is
  not preempted by an independent hardware kill circuit; that limitation remains.
- Corruption, missing frames, USB backpressure, Stop and cleanup failures are
  failures, never synthetic success. Terminal `done` means complete digital
  records and checked codec cleanup, not audibility, room AEC or device safety.

The image does **not** implement the broader plan's 60-second clock qualification,
repeated runs within a boot, three-second speech-like AEC windows, double-talk,
network/TLS load or normal UI coexistence. Those remain later, separately reviewed
experiments. Do not call the initial 1-kHz qualification a complete AEC test.

## Before any physical operation

Read the exact candidate-image/build and recovery checklist. Obtain separate
approval identifying the board, candidate app SHA-256, installed app partition,
compatible chip revision/security state, verified known-good recovery app and
recovery procedure. No flash command is implied by a successful cloud build.
Do not overwrite bootloader, partition table, NVS or security settings. The bench
app itself neither reads pairing/keys nor starts Wi-Fi/network services.

Use the existing USB connection and a locally installed Python with `pyserial`.
No automatic package install is performed. Confirm the port by the owner's
existing device information: this helper does not enumerate or guess a device.
Opening USB serial can cause OS/driver control-line glitches on some systems;
DTR/RTS are configured false before open, but this is not a reset-free guarantee.
Do not attach an ordinary serial monitor that may assert reset/boot controls.

The commands below are **future owner-run examples after approval**, not steps
executed during cloud development. Replace the explicit port with the verified
local port; choose a new private output directory. Do not copy placeholder text
as a real port. No command flashes an image or bypasses physical confirmation.

## Minimum first physical trial: silence only

1. After the approved app-only installation/reboot, place the board on a table
   away from ears. Have its actual power-off and recovery method ready; USB
   removal may not power off a battery-operated board.
2. From the checkpoint's `source/` directory, arm only the silent profile:

   ```sh
   python -m pocket.bench_capture collect \
     --port '<VERIFIED_LOCAL_PORT>' --profile silent \
     --output-dir '<NEW_PRIVATE_DIRECTORY>' \
     --approved-device-test --record-private-microphone
   ```

3. Press and release the device's OK button within 10 seconds. The console is
   intentionally disabled; no LCD feedback is implemented. The host accepts the
   exact `armed`/`running` records. If no recording starts, do not keep pressing or
   send ARM repeatedly: the attempt is one-shot. Review its failed report first.
4. During the finite silent capture, speak a brief nonprivate count near the mic.
   Any unexpected sound or behavior means press any button to Stop; use the known
   physical power-off method if software Stop fails. Do not retry on uncertainty.
5. Require a complete record with `restore_ok:true`, exactly 1,000 frames, source
   samples zero, valid CRC/counters and no clipping. Save the report locally.
   Review channel behavior from the retained paired data; aggregate RMS alone
   cannot certify lane identity. If both lanes track the spoken count similarly,
   lane order is ambiguous, or a terminal is missing, do not proceed to sound.

The collector has a fixed 30-second host deadline, sends at most one ARM, never
reconnects/retries, and attempts one STOP on failure/interruption. A missing
terminal leaves the hardware state unknown; host timeout is not proof of mute.
The report is a record-integrity summary, not automatic physical acceptance.

## Optional low-level tone trial — only after silent acceptance and approval

Reboot only using the separately approved method; a terminal attempt cannot
rearm. Reusing this candidate for a second physical trial requires that the
owner has approved that finite sequence, not an unlimited experiment loop.

```sh
python -m pocket.bench_capture collect \
  --port '<VERIFIED_LOCAL_PORT>' --profile tone \
  --output-dir '<DIFFERENT_NEW_PRIVATE_DIRECTORY>' \
  --approved-device-test --record-private-microphone --allow-low-level-tone
```

Again, physical OK starts it. Do not hold the device near ears and do not increase
volume if the result is weak. Unexpected loudness, clipping, repeated tone or
failed Stop means abort. Require exactly 500 frames, 120,000 accepted mono source
samples, no loss and `restore_ok:true`. Source counts do not mean audible playout.
The paired reference must reproducibly contain the finite electronic stimulus
while the microphone shows the separate acoustic response. No new hardware or
provider is required, but this measurement has **not been performed**.

## Local files and offline inspection

Each explicit collection creates a new directory (0700 on POSIX), with
`capture.hpb` and `report.json` (0600 on POSIX). On Windows, verify the new
directory inherits private user-only ACLs; POSIX mode bits are not an ACL guarantee.
Existing paths are never overwritten.
Raw data contains microphone audio; it stays on the local host and is not
uploaded to Library, a provider or any server. A failed/partial recording remains
marked failed for diagnosis. Delete it locally when no longer needed.

Offline validation requires no serial dependency or device access:

```sh
python -m pocket.bench_capture inspect \
  --input '<LOCAL_CAPTURE_DIRECTORY>/capture.hpb' --profile silent
```

The report includes frame/source counts, lane RMS/mean/peak/clipping and terminal
status. It does not identify the physical lane automatically, measure clock ppm,
estimate a universal delay, certify SPL or calculate physical AEC quality.
No playback command or auto-listening is included.

The parser accepts only the exact HPB1 header/status schema, whole-header/payload
CRC, contiguous sequence/epoch/capture positions, fixed profile limits and valid
terminal state. Only a bounded prefix of pre-record boot noise can be skipped;
after the first frame it never resynchronizes across corruption or logging.
The same parser is tested against full SILENT and TONE records emitted by the
actual C core, without wire translation. These are synthetic compatibility tests.

## End the trial

Restore the exact approved known-good app using the approved recovery procedure,
then verify legacy operation and unchanged user settings. If mute, cleanup or
ownership is uncertain, stop experiments and do not start normal audio on the
assumption that teardown completed. Physical results must be recorded separately
before considering Live activation, keys, service changes or a paid session.
