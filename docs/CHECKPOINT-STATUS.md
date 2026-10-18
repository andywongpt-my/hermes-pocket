# Consolidated source checkpoint

18 October 2026; branch `checkpoint/source-20261018`.
Base public main: `dbf526a1a3a9bc1e17b082125e1dff463895213e`.
Selected source is consolidated in a new commit; private worktree histories are
not uploaded. Original worktrees and unrelated scratch files are preserved.

## Firmware evidence boundary

Delivered/restored source: `2eabc4600d6dc6fc2c2033870ab7121b4e3b0bb4`, including
recorded Pocket-run Usage, bounded full text, segmented speech, CJK font,
playback drain and standby work. Its previous application was restored and
verified after the later Wi-Fi failure. No new device observation occurs here.

Root firmware: `556e897543517aebd517ea8621767f9f3cd28ef2` plus the local
initial-disconnect patch and tests. Candidate `556e897` booted and imported the
legacy network read-only but failed to connect. Its physical trace showed a
pre-connect barrier failure, without a numeric driver return. The boundary
defect was reproduced in real C using an SDK-faithful fake.

`esp_wifi_disconnect()` may succeed while unassociated without promising a
disconnect event. The old adapter interpreted all successes as future callbacks.
The old fake returned the explicit disconnected error whenever idle, bypassing
the faulty barrier in ordinary boot tests. The correction queries association
before disconnect, keeps the callback barrier for live associations and fails
closed on unexpected query errors. The fake now defaults to idle success with
no event. Corrected hardware acceptance, multi-network fallback and clock checks
remain open. A host pass does not prove the physical Wi-Fi defect fixed.

## Backend evidence boundary

Root `pocket/`, `helpers/` and backend tests come from frozen **bridge018R2**,
previously deployed. Original sorted source-map SHA-256:
`a957beb7bab172d9b85f46ce77c57b285fa5eeffe5cd8388ae4b9fbef5ad0d71`.
Device firmware and `test_firmware_host.py` come from the firmware owner, not the
backend's small firmware smoke-test copy. No running service is probed or changed.

**quota020R3** is isolated in `checkpoints/account-quota-020/backend/`.
Original sorted source-map SHA-256:
`37442dc244c2aeb0075d80f151bfadb0017d4b7b0f310930dca2a34d1b94b419`.
Account snapshots/export and HTTP integration are undeployed, with no automatic
refresh activated. Claude/Ollama collectors and guarded operations are unfinished
source. No fresh usable quota feed or account binding is claimed. The Codex
producer is missing; pending contract tests are kept outside passing suites.

Operations paths, hostnames and endpoints use inert example values. Private
acceptance records, runtime settings and deployment receipts are excluded;
operations are not a deployable bundle. No account client, provider, SSH,
service manager or paid request is invoked by publication. Funnel is unconfigured.

## Publication contents

Included: application/BSP/font source and licenses, bridge code, synthetic tests,
unfinished quota source, and public protocol/design/status documentation.
Generated C font tables are application source, not flash images.

Excluded: secrets and pairing/owner configuration, private TLS material,
environment files, real audio/transcripts/replies, raw logs, numeric account
observations, deployment identities/addresses, SDK/dependency caches, build
images/archives, host-specific upload/staging harnesses and untracked scratch
plans. Placeholder and fixture strings are synthetic. Original histories are
left local rather than published with their private deployment notes.

Actual results and unrun checks: [verification](VERIFICATION.md).
