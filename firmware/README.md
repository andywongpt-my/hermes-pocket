# Firmware source checkpoint

Target: FoloToy AI Passport, ESP32-C3, 8 MB flash, no PSRAM.
Toolchain: ESP-IDF 5.5.3 and the pinned `dependencies.lock` components.

This source contains five-network Wi-Fi and the MYT clock plus an initial-connect
correction. It is **not the delivered device firmware**. The device was restored
to `2eabc4600d6dc6fc2c2033870ab7121b4e3b0bb4` after candidate `556e897` failed
physical reconnect. Publication performs no serial access, reset or flash.

The old adapter waited for a callback after an idle station's successful
`esp_wifi_disconnect()`, but that callback is not guaranteed. It now checks
association first: idle can proceed, a live association still requires its fresh
callback, and an unexpected query error fails closed. The fake now defaults to
idle `ESP_OK` without an event and retains the explicit disconnected-code case.
The corrected source has no physical acceptance yet.

Candidate setup requires the physical owner gesture; it does not start
automatically at boot. Its protected hotspot accepts one client for five minutes.
Fresh association/IP is required before saving one of five profiles; Wi-Fi and
Hermes pairing storage remain separate. No automatic eviction occurs.
See [candidate Wi-Fi behavior](../docs/WIFI_NETWORKS.md).

Candidate Home time uses fixed MYT (UTC+08:00) after current-boot SNTP sync.
Until then it shows `Waiting for time (MYT)`. It reuses the existing time source,
continues offline after sync and renders only while awake. Physical checks remain
pending. Existing controls, diagnostics, full replies, recorded Usage, CJK font,
voice memory/transport corrections and standby lifecycle are preserved in source.

In an existing ESP-IDF 5.5.3 environment:

```sh
idf.py build
idf.py size
```

Host checks are in the root README. Build, synthetic boundary tests and physical
acceptance are separate evidence; see [verification](../docs/VERIFICATION.md).
This checkpoint includes no images or flash command. TLS certificate/hostname
verification remains enabled; uncertain work is not automatically resubmitted.
