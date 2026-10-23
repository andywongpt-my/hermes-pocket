# PAUSED: local R3 checkpoint only

Owner prioritized battery repair. No quota staging, native account reads, network operations or deployment are authorized by this checkpoint. Resume remaining steps only when root resumes the quota task. Focused R3 regressions2/2 and the actual executable hash check passed; full70+3 remains unrun.

# Codex-only activation candidate R3

This isolated candidate activates only AW's approved current Codex account. It does not modify the approved parent R3 files. Its backend remains R3 identity 37442dc244c2aeb0075d80f151bfadb0017d4b7b0f310930dca2a34d1b94b419.

Current state: offline Windows qualification passed; no native client/account query, network/SSH, service, deployment, or live publication was performed by this task. Root has supplied a real AW native qualification envelope and this task validated its exact schema and pinned evidence locally. Linux candidate qualification and independent candidate R3 review remain required technical evidence. The account choice is already approved.

## Exact local commands

Use PowerShell with login disabled. Working directory:

C:\Users\example\Documents\Codex\2026-10-14\task-2\pocket-account-quota-020\codex-only-activation-020-r3

All commands below use:

    $quotaPython = 'C:\Users\example\AppData\Local\Programs\Python\Python311\python.exe'

Local fixture qualification:

    & $quotaPython -B -m unittest -v test_quota_ops_020

Code-only Linux staging and fixture qualification (these perform SSH; root owns execution):

    & $quotaPython -B .\operate_quota_020.py stage
    & $quotaPython -B .\operate_quota_020.py test

The existing fixed R3 package must already be at /home/example/.hermes/pocket/gateway-only-20261015/staging/account-quota-020-r3/package. The R3 candidate uses a deliberately new stage; both R1 and accepted R2 stages and receipts remain untouched. Run stage/test at the new path, never delete or rewrite the old receipts. The candidate stage is separate: /home/example/.hermes/pocket/gateway-only-20261015/staging/account-quota-020-codex-ops-r3.

Stage includes pinned backend evidence, eight implementation/test files and four reviewed Codex code/evidence files. The stage and acceptance payloads exclude AW private proof, account binding, executable metadata, helper evidence contents and credentials. Acceptance sends only a strict digest-only native attestation. The test operation produces quotaops-tests.json with Linux platform, zero failures/errors/skips, and exactly the current implementation hashes. A test attempt is exclusive; inspect any uncertain result before taking further action.

## Required evidence before sealing

1. Root's supported native qualification must return actual helper stdout and the actual Windows job supervisor cleanup proof. Write AW-only native-aw-result-private.json as exactly {"qualification": <parsed helper stdout {snapshot, proof}>, "supervision": <actual supervise() proof>}. Preserve those two outputs unchanged; no filling guessed fields. Never upload this file. The old native-acceptance-template.json is superseded and deliberately invalid.
2. private_aw_020.py validates the helper's exact proof schema, including its flat source_files map for native_aw_qualification.py and official_codex_quota.py. It requires approved app context, supported account/read(false), actual before/after binding checks, exact source timestamps, direct-child cleanup, and outer-job assignment/resume/empty-job/closed handles and pipes. The helper and supervisor code/test and their review/report pins remain AW-private evidence. The sealer creates quotaops-native-acceptance.json containing only profile, accepted source hashes and canonical private-proof/evidence digests plus exact operation test/review digests. The sender checks this correspondence before every native read. An unrelated CLI identity never qualifies.
3. Independent review must write quotaops-review.json with approved=true, findings=[], and implementation_hashes exactly matching candidate-manifest.json. This is a review of this changed candidate; the approved parent R3 review alone is insufficient.
4. Local quotaops-tests.json must be the candidate's successful Linux receipt. Parent R3's 56-test receipt belongs to different implementation hashes and cannot substitute.

After those actual files exist:

    & $quotaPython -B .\make_acceptance_020.py --seal
    & $quotaPython -B .\operate_quota_020.py validate
    & $quotaPython -B .\operate_quota_020.py accept
    # Run the required AW predeployment read below before deploy.
    & $quotaPython -B .\operate_quota_020.py deploy

The sealer validates every source, native, backend, test and review binding in memory before exclusively creating the sanitized quotaops-native-acceptance.json and quotaops-acceptance.json. It cannot synthesize missing evidence. It refuses an existing acceptance rather than overwriting one.

Deploy retains the R3 SQLite idle-admission transaction, supervised operation identity, exact original-baseline backup/recovery, health/local-usage/model-contract checks, resident STT, TLS identity and protected runtime/config checks. It does not publish quota.

## Prepare, fresh read, publish, verify

Execute in this order, promptly within the prepare response's 300-second pending window:

    & $quotaPython -B .\operate_quota_020.py prepare
    & $quotaPython -B .\quota_refresh_020.py --count 1
    & $quotaPython -B .\operate_quota_020.py activate
    & $quotaPython -B .\operate_quota_020.py verify

prepare creates a nonsecret, private channel-state record bound to profile and acceptance SHA256 and captures the previous codex.json revision. It does not create any credential or change an auth configuration. The sender must execute a new native read after prepare; do not supply the historical approved-app candidate as a fresh observation.

The sender revalidates the complete accepted local bundle, private proof and executable hash, then uses the unchanged reviewed CodexQuotaClient with the explicit native receipt binding. It calls account/read(false), account/rateLimits/read, account/read(false). It loads the producer by hashing and compiling the same source bytes, bypassing bytecode caches. It sends nothing until the client context closes successfully. The numeric source timestamp must fall inside the new read's bounded wall-clock interval.

Fixed SSH transport:

    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 meow /home/example/.hermes/hermes-agent/venv/bin/python -B /home/example/.hermes/pocket/gateway-only-20261015/staging/account-quota-020-codex-ops-r3/quota_remote_020.py --push-codex

Only a strict <=16384-byte numeric v1 snapshot is supplied on stdin. No raw native metadata, binding, email, tokens, keys, bearer credentials or environment are transported. SSH uses the existing connection; no new auth setup or public write endpoint exists.

The receiver acquires the existing operations lock, revalidates exact stage/acceptance/deployed backend hashes, checks enabled source-specific channel state and rollback absence, rejects stale/future/preactivation/replayed observations, then calls the unchanged private atomic backend publisher. Output is fixed receipt JSON containing provider/profile, acceptance hash, canonical snapshot hash and original fetched_at.

activate proves changed file revision and exact mapped Codex metrics, including original timestamp, remaining percentage, window duration and reset, then runs the inherited TLS/STT/protected gates. Primary may be weekly; secondary may remain unknown.

## Recurring refresh and stop

After activation succeeds, explicitly run the AW sender under the parent's owned supervision:

    & $quotaPython -B .\quota_refresh_020.py --count 0

It is serial, waiting 120 seconds after completed read/publication. No Windows scheduled task, auto-start setting, installed service, config write or separate token is introduced. Parent must retain its owned handle to stop this process with Ctrl-C or an owned-process signal. Every source error stops the sender; it does not log in, refresh credentials, repair accounts or retry uncertain publication. AW sleep/offline naturally makes the existing snapshot stale.

Claude/Ollama are not collected or published. Absent sources stay unknown; historical stale/failed or mixed stale/unknown rows retain original metadata. Any available metric from an inactive source fails this profile's readiness gate. Existing backend stale metric values may remain in JSON as history, labelled stale; no source timestamp is refreshed.

## Uncertain transport, inspection and rollback

Exit 2 / outcome_uncertain / recovery_pending means inspect before retry:

    & $quotaPython -B .\operate_quota_020.py inspect

Inspect reports last Codex push receipt and, for an accepted stage, the actually published canonical digest/original timestamp without claiming freshness. Compare these to the sender's attempted_snapshot_sha256 and attempted_fetched_at. If no conclusive publication state exists, remain blocked; do not restamp, blindly resend or reset phase markers.

Rollback:

    & $quotaPython -B .\operate_quota_020.py rollback
    & $quotaPython -B .\operate_quota_020.py inspect

The accepted rollback acquires the operations lock, checks the original baseline/recovery proof, revokes an existing Codex receive channel before restoration, and retains the inherited R3 admission/recovery/readiness path. A delayed sender is rejected. Last good numeric files and original timestamps are preserved. Stop the owned AW loop too; authorization revocation protects the remote endpoint even if the sender is disconnected.

prepare is exclusive. An expired or uncertain prepare is not silently overwritten. Inspect, then use the accepted rollback/reconciliation path. A new attempt requires deliberate reviewed reconciliation/new staging; do not delete receipts to force a retry.

## Test scope and limitations

See test-mapping.json for every original R3 test case, its retained/replaced status, and replacement evidence. All 16 original ReviewRegressionTests are retained, including healthy-baseline no-op, owned stopped-baseline recovery, completed/restored evidence rejection, live/unknown job/foreign quota/owner work negatives, and both-lock supervision. The existing deployment, admission, baseline, TLS, STT and process-check function bodies are checked unchanged in candidate-manifest.json. Source-specific Claude fixtures are replaced by Codex tests, rather than marked skipped.

Windows fixtures prove behavior at controlled process/transport boundaries. They do not prove native current-app compatibility, actual SSH publication, POSIX permissions or Linux service transitions. Those are the explicit root-owned remaining qualification steps. No action was denied; no live operation was attempted.

R1 review and manifest history are preserved byte-for-byte in r1-history/. split-proof-red.json records the actual R1 acceptance-payload leak. New SplitProofTests inspect the actual stage/accept bootstrap payload and reject missing/tampered private data, extra remote fields, wrong evidence digests and incomplete supervisor cleanup. The sender cache fixture proves an eligible divergent cache is ignored.

Initial qualification requires actual helper R3 and supervisor R2 evidence. Recurring refresh retains the unchanged bounded client context cleanup under an explicitly owned AW terminal/process; this candidate does not add a per-read Windows Job Object. Stop and inspect on any cleanup or transport failure.


## R3 predeployment requirements

R3 is a whitelist copy of final frozen R2. The old builder is not used. No prior acceptance, channel, phase, test, or deployment receipt is copied into the new candidate as current authority. Preserve all prior remote/local receipts.

The executable limit is finite 512 MiB, read in 64 KiB chunks, with the existing exact executable digest and file identity/change guards. The actual 341312816-byte approved executable must pass the real-file digest preflight. Source account binding and successful cleanup remain mandatory.

After new candidate review and Linux fixture qualification, obtain the exact remote quotaops-tests.json bytes before sealing. Do not use PowerShell text redirection, Get-Content/Set-Content, WriteAllText, JSON reserialization, or newline conversion. Existing SSH authentication and strict host checking remain unchanged. In root's already-owned Python context, the read-only fetch shape is:

    receipt_path = '/home/example/.hermes/pocket/gateway-only-20261015/staging/account-quota-020-codex-ops-r3/quotaops-tests.json'
    script = "import pathlib,sys; raw=pathlib.Path(" + repr(receipt_path) + ").read_bytes(); assert len(raw)<=1048576; sys.stdout.buffer.write(raw)"
    done = subprocess.run(operator.ssh_argv(), input=script.encode('ascii'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90)
    assert done.returncode == 0
    raw = done.stdout
    parsed = ops.parse_json(raw)
    assert parsed['ok'] and parsed['platform'] == 'linux'
    local = candidate / 'quotaops-tests.json'
    if local.exists():
        assert local.read_bytes() == raw
    else:
        ops.exclusive_bytes(local, raw)

If an existing local receipt differs, preserve it as rejected evidence and inspect before deliberate reconciliation; never change accepted receipts or remote markers. Seal only after the local file equals the remote bytes and all exact implementation/test/review pins pass.

After local sealing, BEFORE remote backend deployment/restart, root must perform the complete actual AW sender preflight with these exact frozen bytes:

    reader, acceptance_digest = sender.load_reader()
    started = time.time()
    numeric = reader()
    completed = time.time()
    ops.validate_codex_snapshot(sender.canonical(numeric), observed_at=completed, started_at=started)

Keep the account binding and private proof local; print no private metadata. This preflight performs a new supported bound account read and must finish normal client cleanup. It must pass in addition to helper qualification and file hashing. No numeric transport happens in this preflight. Only then continue accept/deploy. After prepare, perform another fresh sender read/publication; never reuse or restamp the predeployment snapshot.

All source, receiver, admission, identity, baseline/recovery, TLS/STT and protected-runtime gates remain the accepted R2 design. R3 needs its own exact Linux qualification and fresh review.

R3 additionally normalizes only synthetic0o111 execute bits for the initial Windows path-stat/open-fstat comparison. File type/dev/ino/link count/size/timestamps and non-execute permissions remain exact; descriptor-before/after and path-before/after comparisons are exact. Linux behavior is unchanged.
