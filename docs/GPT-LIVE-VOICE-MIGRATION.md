# Voice direction and retirement gate — 29 October 2026

Andy intends GPT-Live to become the sole voice path. Subsequent integration
should target that replacement, not expand or perpetuate a parallel legacy voice
product. The Hermes backend task/delegation architecture remains in scope.

The existing working device voice path and known-good recovery artifacts must
remain intact until the replacement has passed physical validation. The current
bench-only image is not that replacement. No legacy voice code, endpoint,
installed app, setting or recovery artifact was removed in this continuation.

After successful physical paired-reference/AEC and no-provider runtime acceptance,
implement the explicitly owned GPT-Live task/UI activation and verify lifecycle,
Stop/cleanup, resource limits and failure reporting. Real credentials/provider
activation still requires its own authority. Retire the old voice path only after
the replacement is physically validated and the owner approves the migration
with a preserved, verified recovery option. Do not silently fall back to the old
provider or claim legacy retirement while the Live gate remains closed.

## 30 October inactive integration

The normal-app ownership/configuration and private IDF/audio lifecycle components
have now been developed and tested without waiting for another physical run.
They remain inactive: no normal UI caller or release-gate bypass is installed.
Future activation still requires the physical acceptance above, reviewed app
admission/controls and explicitly authorized host/provider configuration. The
original working app was restored according to parent history; the recalled
silent 1000-frame observation is not recorded as a pass.
