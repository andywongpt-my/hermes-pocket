# Pocket setup and companion UI update

Historical setup-UI change notes. Current build/flash status is in VERIFICATION.md;
physical UI/provisioning acceptance remains pending.

- Select scanned 2.4 GHz Wi-Fi, with manual fallback for hidden networks. Scan is AP-interface and nonce gated, capped at 20 records and rate limited.
- Explicit form handlers and inline validation; distinguish saved/restarting from connection loss. Optional Hermes settings are collapsed.
- Owner-approved eight-character random WPA2 setup password, excludes ambiguous characters; five-minute expiry retained.
- The historical winged-cap mascot has since been replaced by the current cartoon cat; connection and unconfigured usage states remain explicit.
- Preserve version 2 NVS schema and existing partition map. App-only deployment at 0x10000.
- Reduce save-handler stack use by moving the JSON receive buffer to checked heap allocation; reduce render stack by retaining voice snapshot in static storage.

Checks already run: 96 existing Python tests and Node validation/initialization tests passed. These do not establish target firmware compilation, Wi-Fi radio behavior, device rendering, successful provisioning or Hermes connection. No browser-based or physical provisioning acceptance is claimed.

The old save failure was reported as an unresponsive button. Native form validation and implicit DOM globals were replaced, but the original runtime cause was not directly observed. Device/phone acceptance remains with Andy.
