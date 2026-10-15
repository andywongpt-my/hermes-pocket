# Hermes Pocket

Open-source FoloToy AI Passport companion for Windows and Linux Hermes Agent.
MIT application code, copyright 2026 Andy Wong. Independent community project,
not an official FoloToy or NousResearch product.

## Current milestone: controls and voice timing candidate

Built for ESP32-C3, 8 MB flash, no PSRAM, and ESP-IDF 5.5.3. The firmware adds
connection diagnostics, saved target selection, bounded Pocket task browsing,
read-only shortcuts, Usage reminders and local audio controls to the animated
cat, battery gauge, phone setup and voice client.

The combined controls, debug and voice transport build passed app-only flash/readback and a
bounded physical boot check: Linux status returned authenticated HTTP 200.
Usage decoded successfully with all three account feeds unconfigured.
No Linux failures, fatal errors or Wi-Fi drops were observed in that window.
NVS pairing, the bootloader and partition table were preserved. Exact source,
hashes and acceptance limits are recorded in [verification](docs/VERIFICATION.md).

The same application retains numeric connection/TLS/heap, voice capture reason,
request stage/timing/queue and Usage decode diagnostics. These messages do not
include credentials, recorded audio, transcription/reply bodies or real quotas.

The checked Linux bridge advertises configured STT and TTS. That capability
report does not establish successful microphone capture or a complete voice
round trip. An owner recording on the preceding build filled the four-frame audio
queue during a slow upload. The candidate uses TCP_NODELAY and an awake Wi-Fi
radio during capture/upload, then restores the prior power mode. Its queue and
memory budget are unchanged. Successful reused audio chunks now skip heap scans
and per-chunk logs; one deferred numeric summary separates request phases and
local diagnostic/JSON costs. The bridge also enables TCP_NODELAY. These changes
passed implementation checks; physical upload improvement, end-to-end voice and
button/display acceptance remain open.
Tests use synthetic providers and do not spend model or speech allowance.
Tailscale and router deployment are outside this firmware increment.

- Separate Windows/Linux target selection, chat history and device-scoped tasks
- Phone setup through a temporary password-protected device Wi-Fi network
- Certificate-validated HTTPS and a dedicated Pocket pairing token
- 16 kHz mono PCM, bounded 30-second push-to-talk recording
- Windows Desktop JSON-RPC and Linux gateway adapters
- Linux Hermes dashboard relay and bounded Hermes command-adapter STT/TTS workers; provider secrets stay on host
- Approval awareness and explicit uncertain outcomes, without automatic resubmission
- English and the bundled LVGL common CJK glyph subset (not every Chinese character)
- Codex, Claude and Ollama usage remain honestly unavailable until sources are connected

## Controls and boundaries

UP/DOWN moves through Home, Chat, Usage, Diagnostics, Tasks, Tools, Audio,
Alerts and Screen. A wake gesture is consumed completely before controls resume.

| Page or gesture | Behavior |
|---|---|
| Home: short OK | Select Windows/Linux and save the target in a separate UI preference. Pairing settings remain separate. |
| Home: hold OK | Open the temporary phone setup window. |
| Chat: hold OK | Wait for Listening, speak, then release to send. UP during capture discards it. |
| OK while voice is busy | Cooperatively stop local capture, waiting or playback. A submitted Hermes task may continue. |
| Usage: short OK | Cycle supplied windows. Missing values stay unknown; a genuine zero remains zero. |
| Diagnostics | Show the selected target's last observed prerequisites, HTTP/transport/TLS codes, capabilities, heap and age. Old or paused observations are labeled. |
| Tasks: short/hold OK | Select the next Pocket task / refresh its list. Show real phases and a bounded result preview, including approval, uncertain and truncated states. |
| Tools: short/hold OK | Choose / request Status, Usage or Pocket Tasks through existing GET routes. Todo stays unavailable because no supported read-only source is configured. |
| Audio: OK, then UP/DOWN | Enter volume editing and adjust 0â€“100; OK exits editing. Only the worker touches the codec. |
| Alerts: short/hold OK | Select 80%, 90% or off for percentages / toggle the independent $1 low-credit threshold. |
| Screen: short OK | Turn off the display while keeping Wi-Fi and existing work alive. Any fresh press wakes it. |

Tasks are the latest twelve device-scoped **Pocket-created** tasks, not a global
Hermes task list. Progress uses actual phases rather than invented percentages.
The selected result preview is at most 768 UTF-8 bytes; full results remain on
Hermes. Approval must be handled through the trusted Hermes interface.

Usage reminders require fresh, available metrics from real configured sources.
Percent-used, percent-remaining and credit balances keep their own units.
Pending reminders mark Home and Usage; opening Alerts acknowledges the indicator.
Reminders are deduplicated during the device session; volume and alert settings
are also session-local. Reset notices require a fresh next-period observation
and do not claim that allowance was replenished. No account feeds are enabled by
default, so the current unconfigured state produces no quota alerts.

Deployment still requires valid host authentication, a trusted bridge TLS identity,
and an enabled transcription service. Configuring `stt_enabled` in Pocket does
not enable it in Hermes; verify the host service separately.
Do not treat a successful compilation as live compatibility or hardware acceptance.

## Cloud/offline checks

Python 3.12+, standard library for the mock/core checks:

```sh
python3 -m unittest discover -s tests -v
python3 -m pocket.simulator
python3 -m pocket.server --demo --two-targets
```

The offline suite includes a full-size 4096-byte PCM frame through actual local
HTTP routes and synthesized playback.
These checks use fake speech/models and do not spend provider allowance. Native C
regressions cover numeric diagnostics, preferences, task parsing, Usage reminder
freshness/units, audio cancellation and idle/capture memory cleanup.
Host C checks require a compiler; UI snapshots require gcc and an LVGL checkout.
CI renders the UI and tests the task client against the SDK's actual cJSON parser.

The mock server binds loopback only. Its published fixture token is not a real
credential and must never be exposed. Live Desktop transport additionally requires
`websockets`; MP3 speech conversion requires an owner-installed FFmpeg executable.
See [live deployment](docs/LIVE-BRIDGE.md), [firmware](firmware/README.md),
[verification](docs/VERIFICATION.md), and [third-party notices](docs/THIRD-PARTY-NOTICES.md).

## Build and deployment

Use the pinned managed components in `firmware/dependencies.lock` and official
ESP-IDF 5.5.3. Never include account/provider credentials
in firmware, source, public issues or release archives.

Only split images are provided for the candidate. Preserve the NVS gap; do not
use erase_flash or a merged full-flash image. Read the release's flash instructions
and verify its manifest hashes before deployment.

The protocol and earlier design documents describe intended scope and historical
milestones. This README and the current verification section define actual status.
