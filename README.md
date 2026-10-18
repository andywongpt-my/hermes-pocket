# Hermes Pocket

FoloToy AI Passport companion for Windows and Linux Hermes Agent. MIT application
code, copyright 2026 Andy Wong. Independent community project, not an official
FoloToy or NousResearch product.

## Source checkpoint, 18 October 2026

This branch preserves work in progress. It is **not a verified firmware release**.
Publication does not deploy a bridge, activate quotas, or flash a device.

| Component | Recorded state |
| --- | --- |
| Delivered device | Firmware `2eabc4600d6dc6fc2c2033870ab7121b4e3b0bb4`, restored and verified after the Wi-Fi candidate failed. No device access during publication. |
| Root firmware source | Five saved Wi-Fi profiles and MYT Home clock from `556e897`, plus the idle-disconnect correction and regression tests. Original candidate failed physical reconnect; corrected source has no physical acceptance. |
| Root Python bridge | Frozen **bridge018R2**, previously deployed; bounded full replies, paged reading and segmented speech. Its running service was not probed here. |
| Account quota | **quota020R3**, isolated under `checkpoints/account-quota-020/`; undeployed and unactivated. Producers and operations remain unfinished. |
| Remote access | Tailscale Funnel is **not configured**. |

See [checkpoint status](docs/CHECKPOINT-STATUS.md) for precise provenance and limits.

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
Actual results and platform skips are in [verification](docs/VERIFICATION.md).

See [firmware](firmware/README.md), [bridge guidance](docs/LIVE-BRIDGE.md),
[replies](docs/BOUNDED_REPLY_DELIVERY.md), [Wi-Fi](docs/WIFI_NETWORKS.md) and
[licenses](docs/THIRD-PARTY-NOTICES.md). Owner configuration is supplied separately.
No real credentials, private TLS files, environment files, transcripts/replies,
raw logs, deployment addresses or non-source build artifacts are included.
Host speech secrets stay on the host. Mock credentials are loopback fixtures.

This checkpoint supplies no flash images. A generated partition table is not proof
of the installed layout; any later device operation needs separately approved
timing and reviewed compatibility, preserving settings, TLS and authentication.
