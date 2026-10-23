# Complete source checkpoint — 23 October 2026

Branch: `checkpoint/all-project-work-20261023`.
Base: published `checkpoint/source-20261018`,
`e0e7df6efb3cdcbdf460d23b1928e4b103ccf1c7`.
The existing main branch and PR #3 are preserved. This is a source snapshot,
not a release, deployment, device operation, or proof of a live account feed.

## Included source

* Root bridge remains the consolidated bridge018R2 source already published.
* Firmware advances to the delivered Wi-Fi/clock/Funnel and display-power source
  at `e82f04a5c70384dbf4c99cd88fd9758d06bda1a0`, with the additional one-line
  pre-recording early-release correction and three real-C regression cases.
  The correction restores an actionable prompt after release while audio begin
  is in flight. Cleanup, cancellation feedback and authorization errors remain.
* The quota checkpoint includes the later Codex native producer/supervisor,
  qualification and guarded operations source; Claude passive observer,
  status-line adapter, callback, settings helper and installation controller;
  and their synthetic tests. Private acceptance receipts are intentionally absent.
* The temporary Funnel checkpoint preserves the Python ingress, route/auth/TLS
  tests and inert nginx example. Endpoints and owner paths are examples.
* Unique legacy diagnostic source, draft patches and design plans are retained
  outside the application. Superseded drafts are not silently applied over the
  newer consolidated implementation.
* Serial parsers and the bounded capture script are published for reproduction.
  They were not run as part of this publication. The capture script explicitly
  opens its configured port only when an owner chooses to run it; opening may reset
  a connected device. No raw serial input is saved by its filter.

## Evidence boundaries

Historical delivered application SHA-256:
`06ad762fad032d9590e5a19d10c37ad2a71b51510c6257bf1c963c1a91af4761`.
Historical ELF SHA-256:
`f81182f9b2baddf9728a0a4e734c4512df348c95bdcd02d36119817eedb359f5`.
The owner-authorized 19 October capture observed ELF prefix `f81182f9b`, one USB
reset marker, and the selected Linux view `wifi=1, online=1, speech_ready=1` after
successful status parsing. A nine-hex prefix is not a complete binary readback.
The capture omitted screen text and busy/held-key state and contained no voice
request diagnostics. It does not prove the early-release defect caused that
device's stuck screen. The Connecting correction remains unflashed.

The Claude callback was installed on 19 October in separate authorized work;
61 native Linux fixture tests passed then. These are fixture tests, not evidence
of a usable owner quota snapshot. A normal owner Claude Code response is still
needed for a native sample. Direct-SDK Hermes callback triggering was unverified.
No callback, account client or model is invoked by this checkpoint publication.

## Latest reported service state

A separate read-only check reported at **23 October 2026, 15:59 UTC**:
the Pocket backend was inactive, the temporary Funnel unit was absent, and the
public entry returned HTTP 502. General Hermes remained running. No repair was
made. This later outage supersedes the older successful service observations;
publishing source does not restore services. Private endpoints and raw logs are
not included, and this publication did not repeat that live check.

## Verification and exclusions

Current results and unrun checks are in [CHECKPOINT-CHECKS-20261023.json](CHECKPOINT-CHECKS-20261023.json).
Selected source provenance is in [SOURCE-PROVENANCE-20261023.json](SOURCE-PROVENANCE-20261023.json).
Earlier `VERIFICATION.md`, `CHECKPOINT-CHECKS.json`, and `SOURCE-PROVENANCE.json`
are historical records of the 18 October checkpoint, not current verification.

Excluded: credentials and account identities, owner settings and pairing data,
private keys/certificates, environment files, acceptance/deployment receipts,
raw logs or network captures, real quota observations, audio/chat/transcripts,
build binaries/archives, SDK/dependency caches, and one-off host deployment input
bundles. Inert documentation addresses and synthetic fixture credentials remain.
Local originals and private branch history are preserved; only selected source
is committed on top of the already published checkpoint.
