# Account quota source checkpoint

This directory remains isolated from the root bridge and is not a usable account
feed or deployable installation bundle. `backend/` retains frozen quota020R3 HTTP
integration; the root Python bridge remains bridge018R2.

The current source now includes the Codex producer (it was missing in the earlier
checkpoint), native qualification/supervisor and guarded operations, Claude passive
observer/receiver/settings handling, status-line metadata adapter, callback and
installation controller. Ollama managed balance source is retained. Owner paths,
SSH aliases and network addresses use inert examples. Settings, account bindings,
private freezes, receipts, identity files and runtime observations are excluded.
The source contains safeguards tied to those omitted receipts and must fail closed
without a separately reviewed owner configuration. No receipt is fabricated here.

Safe synthetic producer/callback checks, from this directory:

```sh
python -m unittest test_official_codex_quota test_official_claude_usage test_claude_statusline_quota_020 test_claude_callback_020 -v
```

Native observer/qualification and Unix process/permission checks require Linux and
their controlled fixtures. Operations tests referencing private acceptance records
are not represented as passing. The older contract in `pending_tests/` is retained
as historical acceptance material. See the root checkpoint checks for exact current
results, platform skips, failures and checks not run.

The 19 October installation and 61 Linux fixture passes are historical evidence;
they do not establish a native owner quota sample. Normal owner Claude Code activity
is required for that sample. Direct-SDK callback triggering remains unverified.
Publication does not invoke native clients, SSH, providers, account refresh or
service-management actions. `NATIVE-RUNBOOK.md` is sanitized review material.
