# Account quota 020: unfinished source checkpoint

This directory is isolated from the root bridge. Nothing here is deployed or
activated by the root application. It is not a verified account feed.

`backend/` preserves frozen quota020R3 source and synthetic tests. Account
snapshots/export distinguish account quotas from recorded Pocket activity and
preserve unknown/stale states. The root bridge remains bridge018R2.

The Claude collector has no usable fresh native snapshot. PTY tests require
Linux. The managed Ollama adapter is not wired into backend percent snapshots.
Account binding and activation remain unresolved. The Codex producer is missing;
its contract tests are preserved in `pending_tests/`, outside passing suites.

Guarded operations/refresh source use placeholder paths, host aliases and endpoints.
Private acceptance records and deployment receipts referenced by their pins are
omitted. These files are review material, not an executable deployment bundle.

Synthetic backend checks, from `backend/`:

```sh
python -m unittest discover -s tests -v
python -m pocket.simulator
```

Claude mock-boundary checks, from this directory:

```sh
python -m unittest test_official_claude_usage -v
```

The archived `test_ollama_managed_balance_020` suite also needs `httpx`, which was
not installed for publication. `test_quota_ops_020` depends on intentionally omitted
private acceptance/manifest fixtures and does not pass as published. The attempted
combined run and exact limitations are disclosed in the root verification document.
No missing receipt is fabricated to make a suite pass.

No check requires real models, speech, native-account, SSH or deployment calls.
`pending_tests/test_official_codex_quota.py` is unimplemented acceptance work,
not a passing suite. Actual Windows results and Linux omissions are in the root
verification document.
