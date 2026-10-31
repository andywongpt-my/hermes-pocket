# Windows qualification host — prepared source, not device permission

This adds a no-port preflight, an explicitly scoped **open/close-only platform
probe**, and a one-shot **SILENT Stage A** launcher. It does not flash, install
packages, enumerate ports, change settings, send a tone, run Stage B/TLS, retain
raw audio files, contact providers or enable production Live. Cloud tests use
fake drivers and synthetic records; no actual Windows driver or Pocket was opened.
The accepted interpreter range is CPython3.11/3.12 with pyserial3.5; cloud tests
used3.12. The actual AW interpreter/driver still needs the checks below.

## Current recovery and immutable candidate

Parent reports independently verified AW recovery bytes for the **entire** interval
[0x10000,0x271000), 2,494,464 bytes, SHA256
`e21556a8b9fef5378ce6658c9e45c6583d6797845944fd57808d8eb8ecf569ad`.
Cloud has not received or rehashed those real bytes. Preflight rehashes the exact
local file supplied on AW; it will reject synthetic or merely similarly named
recovery files. This new history supersedes only the old40KiB coverage gap, not
other physical/identity/recovery prerequisites or installation authority.

The existing candidate ZIP stays byte-identical:
`hermes-pocket-normal-qualification-app-only-candidate-20261030.zip`, SHA256
`73e14b885f32db723a09fb13880829234e42eb19900d2e5cd1ebd64c644b2501`.
Its sole app SHA256 is
`c8f58b769bf6004435da7f8f45bf545f4d6f8cd6cfaedfdcd6819377d05a063d`.
No replacement firmware is needed for this host-only change. Historical candidate
read-first text remains immutable; this note records the newer recovery evidence
without silently changing that ZIP. Source firmware/gates remain unchanged.

## Step1: AW no-port preflight, before any flash proposal

Use an already available supported interpreter and pyserial installation. Do not
install a dependency or substitute a version automatically if absent. Materialize
this source through a supported file-transfer path; do not bypass transfer errors.
Use the source root as working directory and set `$Python` to the exact existing
interpreter executable (not a Store alias or installer). `--help` opens nothing:

```powershell
& $Python -m pocket.qualification_windows --help
```

An operator must set `$Candidate`, `$Recovery`, `$Port`, `$Identity` to the actual
approved absolute local candidate ZIP path, full recovery-file path, exact known
canonical COM port, and independently attested device identity. No port guessing
or discovery is performed. With those explicit values, the file-only check is:

```powershell
& $Python -m pocket.qualification_windows preflight --candidate "$Candidate" --recovery "$Recovery" --port "$Port" --identity "$Identity"
```

This hashes bounded regular files, validates the exact app inside the ZIP without
extracting it, and reads interpreter/distribution metadata without importing serial.
Windows paths must be normal absolute fixed-drive paths. Device-name aliases,
including COM¹/LPT², UNC/extended namespaces, alternate streams, traversal,
trailing-space/dot ambiguity and detected reparse/symlink paths are refused.
GetDriveTypeW must classify the drive as fixed before file inspection. Local path/
mount mappings must remain trusted and stable: this is not a hostile-filesystem
race-proof sandbox, an ACL audit or proof of physical disk/recovery availability.

The numeric report separately states file verification and runtime compatibility.
`device_opened`, `physical_identity_verified`, `platform_behavior_verified`,
`physical_acceptance` and `flash_authorized` remain0. Preflight success is **not**
permission to open a COM port, capture audio or flash. COM1..COM256 syntax only
validates spelling, not which device is attached.

The fake-only unit groups can also run under the exact AW interpreter without
opening a port. Set `POCKET_QUALIFICATION_CANDIDATE_ZIP` to the explicit candidate
path for the preflight fixture, then run these separate fake-only groups:

```powershell
& $Python -m unittest discover -s tests -p test_qualification_preflight.py -v
& $Python -m unittest discover -s tests -p test_qualification_windows.py -v
& $Python -m unittest discover -s tests -p test_qualification_serial.py -v
```

No symbolic-link creation privilege is needed. Synthetic recovery substitutions exist only inside
tests; production constants are never rewritten. Tests do not establish driver
behavior, and passing fake tests must not populate a platform attestation.

## Step2: separately approved original-firmware open/close probe

If existing evidence does not cover this exact path, obtain approval for one
`probe-open-close` attempt on the **restored, boot-verified original firmware**,
with an independently available recovery/reset method and exact port binding.
Required acknowledgements are open, close, reset risk, recovery and identity; no
claim of previously verified platform behavior or microphone permission is needed.
The distinct probe purpose is enforced by the adapter: **read and write are always
refused**, not just omitted by the launcher. There is no ARM, capture, heartbeat,
reset, retry or reconnect. One open is followed by a nominal up-to2-second cooperative
observation hold and one close; scheduler/OS stalls can extend the observed hold,
whose actual task-observed milliseconds are reported. Ctrl+C shortens the hold; it does not cancel a
healthy OS call. Unknown/pending outcomes retain the owner as described below.

An operator must independently observe whether opening/closing causes a reboot,
disconnect, unexpected UI/power behavior, or loss of normal controls, and record
the exact AW OS/USB driver/interpreter/pyserial/port/device identity plus the probe
numeric report. Normal firmware may execute its usual boot/background behavior if
an OS-triggered reset occurs; the probe does not promise otherwise. No new hardware
measurement is assumed. Observed acceptable behavior is not proof that no brief
control-line pulse exists. Review those measured facts before declaring this
platform suitable for the later SILENT capture. The probe **never** sets
`platform_behavior_verified`; `probe_api_completed=1` means API completion only.
Do not install a candidate merely because this command returned0.

## Step3: only later, explicitly approved SILENT capture

`run-silent` requires separate capture and verified-platform acknowledgements in
addition to the same open/close/reset/recovery/identity scope. It requires the exact
candidate already installed under separate authority and the normal Diagnostics
page, host ARM, then fresh physical hold-OK/release/new-OK confirmation. No AUDIO,
TLS, token, endpoint, raw-retention or flash flag exists. The original app’s other
Chat/background/settings behavior is retained: stay on Diagnostics.

SILENT still exports microphone/reference data. Maximum1,000 frames/10s nominal,
45s host collection; raw paired data stays in bounded RAM and is wiped on owned
capture exit. A fresh explicit numeric JSON summary is the only file written.
No overwrite, transcript, upload or playback. Wiping does not erase all Python,
OS, swap or crash-dump copies. No raw-file privacy/Windows ACL claim is made.

Ctrl+C/Ctrl+Break latch Stop without cancelling healthy I/O. Before ARM, Stop sends
nothing. After ARM, finish the current bounded read, preserve received bytes, send
STOP at most once if admissible, and observe a terminal for at most3s **inside**
the original45s budget. No later heartbeat or new ARM follows Stop. Missing or
invalid terminal is incomplete, not a pass; physical Stop/device heartbeat remain
fallbacks if a driver call is stuck. Operator Stop always makes completion false.

## Ownership, reporting and unmeasured limits

All serial operations are serialized with retained in-flight ownership. A write
or close never races an unfinished read/open. Failed Win32 open can leave native
handle release uncertain: pinned pyserial can suppress cleanup exceptions and
clear its handle before public close would do anything. The adapter therefore
quarantines failed opens; it does not call private handles or retry. Even normal
`host_close_completed=1` means public driver API completion, not proven native
handle release, electrical state or device cleanup. Explicit8N1 and all flow
controls disabled are configured before open, with DTR/RTS false.

If an operation/close is pending or unknown, collection stops but the guard process
stays alive. It only re-observes cleanup after the pending operation actually
finishes; it never retries an uncertain finished close. Broken stdout/stderr cannot
bypass this guard. A hung operation or irrecoverably unknown close may leave the
process waiting indefinitely. **Do not automatically close its console or kill
it:** process termination may close handles/reset the board and is a separate
operator recovery decision. Software cannot preempt an arbitrary driver/SDK hang.
The finite capture deadline is not a guaranteed finite resource-release deadline.

Official [pyserial documentation](https://pyserial.readthedocs.io/en/latest/pyserial_api.html)
warns that OS/driver behavior can alter DTR/RTS during open. The pinned Win32 source
also configures/purges buffers during open and cancels I/O during close. These are
not reset-free guarantees. Windows path handling follows the documented
[reserved names](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)
and [drive classifications](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdrivetypew).
No actual electrical, USB throughput, device audio/cleanup, acoustic, TLS or
production GPT-Live acceptance follows from this cloud host work.
