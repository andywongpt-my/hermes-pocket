# Voice session reliability implementation plan

**Goal:** Keep voice sessions recoverable after short gestures, cancellation,
transport failures and late recorder completion, using the existing worker and
resource ownership design.

**Architecture:** Preserve one voice/network worker, the bounded recorder queue,
manual retry admission and the existing submission handoff. Exercise production C
with the current hardware/HTTP boundary harness. No automatic POST resends,
background workers, account requests or network/security configuration changes.

**Base:** `e36365aee01d6418434faf590db425ac08f92cc1`, reconciled against current
GitHub refs and 25 local checkouts on 25 October. Existing dirty worktrees remain
untouched. All changes stay local and uncommitted for review.

## Transition checks

- [x] Verify the existing voice/Connecting suite before changes (19 passed).
- [x] Reproduce zero-frame release after recorder admission: abort the accepted
  upload, submit no finish/model work, release resources and show a terminal prompt.
- [x] Reproduce a recorder completing after the bounded cleanup wait: retain its
  queue/lease while it owns them; recover after actual completion without reset.
- [x] Verify active status eligibility against unavailable/malformed backend
  metadata, consistent with idle polling, before audio admission.
- [x] Exercise manual retry/reconnect in the same worker without resetting global
  state, including pre-submission cancellation and playback/transport failures.
- [x] Preserve recording-to-playback release ordering and cancellation after the
  submission handoff; never claim remote work was cancelled when only waiting stops.
- [ ] Run the project test command, report skips/failures by name, review the exact
  diff and attempt an offline firmware build with the existing pinned toolchain.

## Evidence limits

The owner subsequently authorized this candidate's app-only flash and the serial
access/reset needed to verify it, conditional on completed tests, independent review,
a successful build and exact target/rollback checks. Preserve Wi-Fi, pairing, NVS,
bootloader, partition table and security settings. Service repair, new publication,
Tailscale upgrade, paid requests and unrelated main-Hermes changes remain outside scope.
Source tests and a build do not establish physical display, microphone, network or
playback acceptance. The late-recorder regression covers delayed task completion;
an already in-flight physical codec read still needs device acceptance.
Live checks on this date found Pocket down/public502; this is recorded separately
from the client defects and is not repaired in this iteration.
