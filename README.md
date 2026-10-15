# Hermes Pocket

Open-source FoloToy AI Passport companion for Windows and Linux Hermes Agent.
MIT application code, copyright 2026 Andy Wong. Independent community project,
not an official FoloToy or NousResearch product.

## Current milestone: integrated firmware candidate

Built for ESP32-C3, 8 MB flash, ESP-IDF 5.5.3. This candidate connects the
phone setup, verified HTTPS client, push-to-talk microphone capture, durable
bridge tasks, text replies and speaker playback. The animated cat/battery/screen-standby
firmware has been built, app-only flashed and independently verified on the device.
Boot reached app_main and detected the CW2017 gauge, with no observed panic or
driver errors. Physical UI acceptance, live text tasks and end-to-end voice validation
remain pending. It is not the completed V1 release.

The physical device now reaches the Linux bridge over certificate- and
hostname-validated HTTPS: authenticated status returned HTTP 200 with the backend
available. Idle audio buffers and the recorder task are deferred until a held talk
request passes the STT capability check, preserving heap for TLS verification.
Settings, pairing and TLS configuration were preserved.

Linux text/task interfaces are implemented and tested offline; live text-task
execution remains unverified. Voice is currently off and unverified: the checked
bridge reports STT unavailable. Tailscale is not integrated into the Pocket
firmware. A router gateway is planned, and router setup has not been completed.

- Separate Windows/Linux target selection, chat history and device-scoped tasks
- Phone setup through a temporary password-protected device Wi-Fi network
- Certificate-validated HTTPS and a dedicated Pocket pairing token
- 16 kHz mono PCM, bounded 30-second push-to-talk recording
- Windows Desktop JSON-RPC and Linux gateway adapters
- Linux Hermes dashboard transcription/TTS relay; provider secrets stay on host
- Approval awareness and explicit uncertain outcomes, without automatic resubmission
- English and the bundled LVGL common CJK glyph subset (not every Chinese character)
- Codex, Claude and Ollama usage remain honestly unavailable until sources are connected

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

The combined suite contains 107 offline unittest checks, including a full-size
4096-byte PCM frame through actual local HTTP routes and synthesized playback.
These checks use fake speech/models and do not spend provider allowance. Native C
regressions cover numeric-only TLS diagnostics and idle/capture memory cleanup.
Host C checks require a compiler; UI snapshots require gcc and an LVGL checkout.
CI runs those with the required tools.

The mock server binds loopback only. Its published fixture token is not a real
credential and must never be exposed. Live Desktop transport additionally requires
`websockets`; MP3 speech conversion requires an owner-installed FFmpeg executable.
See [live deployment](docs/LIVE-BRIDGE.md), [firmware](firmware/README.md),
[verification](docs/VERIFICATION.md), and [third-party notices](docs/THIRD-PARTY-NOTICES.md).

## Build and deployment

Use the pinned managed components in `firmware/dependencies.lock` and official
ESP-IDF 5.5.3. Build in the cloud; the owner's computer is needed only for final
bridge configuration and USB flashing. Never include account/provider credentials
in firmware, source, public issues or release archives.

Only split images are provided for the candidate. Preserve the NVS gap; do not
use erase_flash or a merged full-flash image. Read the release's flash instructions
and verify its manifest hashes before deployment.

The protocol and earlier design documents describe intended scope and historical
milestones. This README and the current verification section define actual status.
