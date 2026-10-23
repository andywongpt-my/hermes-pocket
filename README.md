# Hermes Pocket

FoloToy AI Passport companion for Windows and Linux Hermes Agent. MIT application
code, copyright 2026 Andy Wong. Independent community project, not an official
FoloToy or NousResearch product.

## Source checkpoint, 23 October 2026

This branch preserves pending project work without deployment. Root firmware
contains the later Wi-Fi/clock/Funnel and display-power changes plus the tested,
unflashed Connecting early-release correction. The consolidated bridge is retained;
newer quota producers/callbacks and temporary ingress remain separate checkpoints.

The latest separate live check (23 October, 15:59 UTC) reported the Pocket backend
inactive, temporary Funnel absent and public HTTP 502; general Hermes was running.
Publication does not repair that outage or establish a complete voice round trip.

See [checkpoint status](docs/CHECKPOINT-STATUS.md) for scope, provenance, exclusions,
current checks, historical hardware evidence and remaining work.

## Implemented source

- ESP32-C3, 8 MB flash, no PSRAM; ESP-IDF 5.5.3 with pinned components.
- Animated cat, battery gauge, expanded CJK font, standby, selected-target
  preferences, diagnostics, Tasks, Tools, Audio and Alerts.
- Certificate- and hostname-validated HTTPS with dedicated device authentication.
- Bounded push-to-talk capture, host speech adapters, cancellation, playback DMA
  drain, voice memory fixes and numeric transport diagnostics.
- Recorded Pocket-run Usage, bounded full-reply reading and segmented speech.
  Recorded activity is distinct from account quota; missing quota stays unknown.
- Candidate five-profile Wi-Fi setup, test-before-save, separate pairing storage,
  reconnect/fallback and fixed UTC+8 time after current-boot SNTP synchronization.

Capabilities and synthetic tests do not establish a full physical voice round
trip. Corrected Wi-Fi, multi-network fallback and clock hardware checks remain open.

## Offline checks

Python 3.12+ is the CI target. Core tests use standard-library synthetic providers.
Native tests need a C compiler; set `IDF_PATH` and `POCKET_CJSON_DIR` to an existing
ESP-IDF 5.5.3 tree. Set `LVGL_DIR` to LVGL 9.5.0 for snapshots. Node.js runs phone
page checks. No provider allowance is needed.

```sh
python -m unittest discover -s tests -v
python -m pocket.simulator
```

Quota checkpoint checks are [separate](checkpoints/account-quota-020/README.md).
Current results and platform skips are in [checkpoint checks](docs/CHECKPOINT-CHECKS-20261023.json); [earlier verification](docs/VERIFICATION.md) is historical.

See [firmware](firmware/README.md), [bridge guidance](docs/LIVE-BRIDGE.md),
[replies](docs/BOUNDED_REPLY_DELIVERY.md), [Wi-Fi](docs/WIFI_NETWORKS.md) and
[licenses](docs/THIRD-PARTY-NOTICES.md). Owner configuration is supplied separately.
No real credentials, private TLS files, environment files, transcripts/replies,
raw logs, deployment addresses or non-source build artifacts are included.
Host speech secrets stay on the host. Mock credentials are loopback fixtures.

This checkpoint supplies no flash images. A generated partition table is not proof
of the installed layout; any later device operation needs separately approved
timing and reviewed compatibility, preserving settings, TLS and authentication.
