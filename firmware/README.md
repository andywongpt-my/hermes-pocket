# Integrated firmware candidate

Target: FoloToy AI Passport, ESP32-C3, 8 MB flash, no PSRAM.
Toolchain: official ESP-IDF 5.5.3 with pinned managed components.
The animated cat/battery/screen-standby firmware has been built and app-only flashed.
Validation status for this increment is listed below.

The current animated UI and memory fix passed flash/readback and boot checks.
Authenticated Linux status returned HTTP 200 with the backend available over
verified HTTPS. Physical display/button acceptance, live text tasks and end-to-end
voice remain unverified. The checked Linux bridge advertises configured STT/TTS;
capabilities alone do not establish microphone or playback success.

UP/DOWN changes Home / Chat / Usage / Diagnostics / Tasks / Tools / Audio /
Alerts / Screen pages. On Home, click OK to select and save Windows/Linux; hold OK for phone setup. On Chat, hold OK until Listening appears,
speak, then release to send. Press OK while waiting or playing to stop waiting or
playback. UP during recording discards capture. This does not cancel a task already running in Hermes. Approval-required
or uncertain outcomes direct the owner to the trusted Hermes interface.

The screen lives in `main/pocket_ui.c`, pure LVGL with no ESP-IDF calls. An
animated cartoon tabby shows the agent's mood: idle (blinks, glances, swishes
its tail), listening (ears up, wide eyes), thinking (thought bubble), speaking
(happy eyes, chattering mouth) and sleepy (Hermes offline or Screen page). It is
drawn with LVGL primitives at about 8 frames per second, only while visible, and
pauses with the backlight. The header shows page dots and a battery icon plus a
percentage read from the board's CW2017 fuel gauge. `N/A` means the gauge could not initialize or its
latest reading failed; there is no voltage-derived or placeholder percentage.
Readings refresh every 30 seconds in a separate low-priority task. Failed gauge
initialization is retried there, without blocking button handling. The first
initialization attempt finishes before button callbacks are enabled. If the
monitor task cannot start, the UI shows `N/A` instead of retaining a stale value.
The gauge uses the BSP's existing battery
profile, so accuracy still depends on the correct cell/profile for this board.

Select Screen with UP/DOWN, then click OK on Screen off. This turns off the
backlight and stops periodic UI redraws while keeping Wi-Fi and existing jobs
running. It is not deep sleep. Press any button to restore the display. The
entire wake hold/release and delayed click are ignored, so waking cannot select
an agent, enter setup, record audio, change page or immediately turn off again.
Release the wake button, then use a fresh press to interact normally.

The diagnostics/target-preference firmware passed ESP-IDF compilation, app-only flashing
at 0x10000 and independent flash verification. Boot reached app_main, detected
CW2017 and showed no observed panic or driver errors. Settings partitions were
not written. This does not establish visual, battery-accuracy, button or voice
acceptance. See `../docs/DEVICE-UI-ACCEPTANCE.md` for that checklist.

First boot without saved Wi-Fi starts phone setup. Join the displayed temporary
Wi-Fi using its random password, open http://192.168.4.1, and enter Wi-Fi settings,
bridge HTTPS addresses, dedicated Pocket token and optionally the bridge CA PEM.
The setup window lasts five minutes, supports one phone, and rejects requests
received via the ordinary station interface. Saving restarts the device.

NVS settings are not encrypted. Keep the device physically secure. Do not put
provider/API/dashboard credentials on the device. Empty pairing token removes
pairing. Earlier Wi-Fi/address settings migrate without erasing NVS.

The client verifies TLS certificates/hostnames and waits for network time. It does
not retry uncertain audio submissions. PCM recording is bounded to 30 seconds;
slow network capture fails visibly instead of silently discarding speech frames.
Audio queues, buffers and the recorder task are allocated only after STT and
held-button checks, then released after capture/use. Status polling keeps its
connection/response buffers without reserving inactive audio memory needed by TLS.
Transcription and reply synthesis happen through the configured host bridge.
Both targets may use the Linux voice service, but chat/task state stays separate.

Tasks shows at most twelve device-scoped Pocket tasks and a selected 768-byte
UTF-8 result preview. Hold OK to refresh; short OK selects the next task. Real
states include approval and unknown outcomes; there is no invented progress
percentage. Tools queues only existing read-only Status, Usage and Task GETs.
Todo remains unavailable without a supported source.

On Audio, OK enters/exits volume editing; UP/DOWN changes 0–100 while editing.
On Alerts, OK cycles 80%, 90% and off for percentages; hold OK toggles the
independent, labeled $1 low-credit threshold. Alerts require fresh available Usage samples and never invent account
data. Reset notices require a fresh next-period sample. Target selection survives
reboot through a separate validated NVS key; volume, alert choices and reminder
deduplication last for the current device session.

Known limits: source-level integration is not proof of live compatibility.
Usage sources are not connected. The bundled font covers common CJK glyphs only.
Approval and full results/transcript history remain on the bridge/Hermes host.
Physical display/buttons, cross-reboot target acceptance and end-to-end voice
remain separate acceptance checks. See ../docs/VERIFICATION.md for build/flash evidence.

Build in an activated official ESP-IDF 5.5.3 environment:

    idf.py set-target esp32c3
    idf.py build

UI snapshots render `pocket_ui.c` on a host computer with the device's LVGL
version and 24 KB memory pool, so layout and memory regressions show up without
hardware. With gcc and an LVGL 9.5.0 checkout:

    LVGL_DIR=/path/to/lvgl POCKET_SNAPSHOT_DIR=/tmp/shots python3 -m unittest discover -s tests -p test_firmware_host.py

CI also builds the firmware with ESP-IDF 5.5.3 and uploads the snapshots.

Do not overwrite a working installation assuming all V1 features are complete.
Use candidate split images only; do not erase flash or write across the NVS gap.

BSP: FoloToy/ai-passport commit a9320ab6eaa21b90759a4a22d1039092385f24dd,
MIT notice retained. Dependencies and font notices are in ../docs/licenses and
../docs/THIRD-PARTY-NOTICES.md.
