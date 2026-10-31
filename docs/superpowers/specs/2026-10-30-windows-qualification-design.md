# Windows qualification host: proposed bounded implementation

## Goal and fixed scope
Make the existing qualification host usable for a future explicitly approved
SILENT Stage A trial on Windows, before proposing any candidate installation.
Cloud code/tests only: no device access, COM enumeration/open, flashing, playback,
real capture, settings changes, package installation, provider or deployment.
Firmware and the exact qualification candidate remain byte-identical.
Input source checkpoint SHA256 is
66c437157b977d0365a37257b70e623d1b9d857e62e9e556ea8ea9b1ac47ba28.

Parent reports AW independently verified the full affected recovery window
[0x10000,0x271000), 2,494,464 bytes, SHA256
e21556a8b9fef5378ce6658c9e45c6583d6797845944fd57808d8eb8ecf569ad.
This closes the old tail-coverage issue as reported history; cloud has not read
those recovery bytes. Local preflight must rehash the supplied exact file.

## Approaches
1. Keep only the programmatic adapter: smallest change, but manual composition
   leaves Ctrl+C, ownership, artifact checks and accidental profile selection open.
2. Add a narrow one-shot Windows SILENT launcher and no-port preflight (recommended).
   Reuse the tested collector/protocol; keep all real I/O behind explicit approvals
   and actual platform evidence. Windows control-line behavior stays unproven until
   separately observed on original firmware.
3. Build a new native Win32 serial stack: larger unreviewed surface, still cannot
   prove reset-free physical behavior from cloud. Not justified here.

## Proposed parts
- Materialize a separate source tree from the verified immutable checkpoint.
- A local-file/environment preflight, no serial import/open/enumeration or network:
  require the exact prepared candidate ZIP/app hash, full recovery-file size/hash,
  explicit canonical COMn port and operator identity; report bounded fixed codes.
  Check interpreter/dependency compatibility without installing anything. Expose
  these as facts, never as physical identity or platform-behavior proof.
- A one-use SILENT-only launcher: no AUDIO, TLS, raw-file retention, token or endpoint
  arguments. Explicit capture/open/reset-risk/recovery/identity/platform approvals
  remain mandatory; never auto-fill a platform attestation from preflight success.
  Use one adapter + one Collector; at most 45 seconds collection; save only numeric
  summary to a fresh explicit file; wipe the owned raw buffer on every exit path.
- Portable signal handling: Ctrl+C requests Stop at a safe operation boundary rather
  than cancelling a healthy in-flight driver call. Never overlap write/close with a
  retained read/open/write. One final STOP attempt only when admissible; otherwise
  device heartbeat/physical Stop is the fallback, not a claimed delivered STOP.
- Explicit cleanup states: clean host close, pending owner, or unknown close. No
  automatic process exit that silently abandons a still-running I/O owner; keep the
  guard process alive until operation settles or a separately explicit operator
  forced-exit decision. No automatic retry/reopen or uncertain close retry.
- Verify and document pinned pyserial 3.5 Win32 behavior. Its open path configures
  DTR/RTS via DCB and purges buffers; close cancels pending I/O/handles. Setting false
  before open does not prove no pulse/reset. Runtime flow controls/8N1 should be
  explicit. Any claim applies to tested wrapper behavior, not physical driver facts.

## Tests and delivery
TDD fake-only tests for no-I/O preflight/argument errors, artifact/recovery mismatch,
unsupported platform/version, fixed-code privacy, successful real-protocol synthetic
collection, Ctrl+C during read, missing terminal, partial write, failed/late open,
unknown/pending close, no overlapping operation, repeated Stop/attempt refusal,
raw-buffer wiping and numeric report semantics. Independent read-only review and
full project regression preserve the eight known procfs baseline failures.
A separate host-source bundle/checkpoint and precise AW no-port checklist are
returned; no replacement firmware binary is necessary.

## Remaining physical/platform boundary
Cloud can test CPython/Win32 call sequences through a fake Win32 backend, but cannot
establish the installed AW driver/port/device identity, control-line transients,
actual OS cancellation timing, reset behavior, USB throughput or device cleanup.
Before any flash proposal: AW can first perform a separately scoped no-port
preflight; any real open/close/control-line trial on restored original firmware
requires explicit reset-risk/recovery approval. Its measured receipt must precede
real qualification use. SILENT capture still needs separate privacy approval and
fresh physical confirmation. This design never lifts production Live admission.
