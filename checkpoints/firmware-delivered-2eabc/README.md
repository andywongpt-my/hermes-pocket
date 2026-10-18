# Delivered firmware source recovery

This source-only reverse patch restores the exact firmware directory recorded at
`2eabc4600d6dc6fc2c2033870ab7121b4e3b0bb4` from this checkpoint. It permits source
review/rebuild without publishing the private local commit history. The manifest
hashes are SHA-256 of the original Git blob bytes. No device image or settings
are included. Its old README describes an earlier milestone; current acceptance
boundaries remain in the root checkpoint-status document.

In a separate clean source checkout of this checkpoint, review the patch, then:

```sh
git apply --check checkpoints/firmware-delivered-2eabc/restore-delivered-firmware.patch
git apply checkpoints/firmware-delivered-2eabc/restore-delivered-firmware.patch
```

These commands change source files only. They are not build or flash commands.
No restored-source or physical-device test was performed during publication.
