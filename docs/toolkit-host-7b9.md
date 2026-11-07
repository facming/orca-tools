# 7B-9 — main-owned production download integration

2026-11-06, Asia/Shanghai. **RELEASE BLOCKED**. Separate Draft PRs: [Orca #1](https://github.com/facming/orca/pull/1), [Toolkit #15](https://github.com/facming/orca-tools/pull/15). Starting commits: Orca `efc2d55fd7f9459478cbd9965c1dd7a7f18dfd31`, Toolkit `a4451cee33ae4b86b43e2d278012e7da9c7f5134`.

## Production code integration

The signed-resource staging list now includes the actual download engine, v3 transport and its fixed local dependencies. `toolkit-window.ts` creates `createToolkitDownloads` in main; it reads the isolated configuration service, creates `ToolkitTasks` and injects the private lifecycle into `host-entry.cjs` / `DownloadManager`. This is wired into the production menu/window, not just an authority harness. Production admission still requires the existing packaged app, protected resources, valid signatures and restrictive fuses. No runtime development bypass was added.

The maintenance panel now has source/folder/concurrency inputs, a native folder chooser, progress/bytes/speed, pause/resume/cancel/clear-access controls, recovery explanations and errors. Its dedicated IPC admits only the actual window/mainFrame. It cannot submit owners, handles, arbitrary lifecycle states or lease releases. Configuration status redacts owners. Generic worker APIs and localhost configuration endpoints remain denied. Windows continues using its existing UI/transport; GPU engines are unchanged.

`createToolkitMaintenanceWindow` separates the actual hidden window construction from the user-menu opener's packaged admission and show step. A main-to-preload readiness notification fixes first-load IPC arriving before the authorized frame context exists. Closing/navigating/revoking the window aborts the download session; unfinished cleanup retains its lease. Main serializes UI operations; transfer ranges keep their existing concurrency.

This adds the lifecycle-complete Host controls. It does not yet port the original remote directory browser, remembered download preferences, open-folder action or automatic import of preferences into this panel. Those remain UI parity work, not silently delegated to the ordinary plugin.

## Crash and reconciliation rules

| Situation | Behavior |
| --- | --- |
| New task | Persist and fsync private task intent and parent directory **before** Host creation; only then start registration/transfer. Failed initial file persistence cannot leave an in-memory retry that creates an unjournaled Host task. |
| Host create committed, response lost | Intent has stable task ID. Same-session create retries retain the Host request; next Host session reads service inventory and CAS-claims the record. No remote work until ownership is confirmed. |
| Duplicate create IPC | A caller request UUID is stored in the local intent. Same UUID/same input returns the existing job; changed input is rejected. UI preserves the UUID when retrying unchanged inputs. |
| Host restart / service restart | Acquire the exclusive manager lock before reading files. Use service owner/epoch/revision, then `task-claim` CAS. Old owner updates fail. Previously created/running tasks become paused; no automatic download resumes. Service idempotency and records survive service restart. |
| Completed / cancelled | Service terminal state takes precedence over JSON. A local `complete`, `revoked`, `cleanup_verified` or `verified` field never proves remote revocation. Signed proof must be revalidated before key deletion. |
| Active / paused / error | Preserve lease. Interrupted active work is paused; resume is explicit. Failed lifecycle transitions and uncertain requests retain state for retry. |
| v3 legacy task with service lease | `task-adopt` requires the existing service lease and exact target. It establishes a paused Host record without releasing access. The legacy inventory review barrier stays set. |
| Legacy v1/v2, missing lease, different epoch/target | Do not import disk claims as authority. Show review-required and retain local keys. |
| Host record without local job | Show orphan record, retain lease. No synthesized remote revocation claim. |
| Old database restored | Existing epoch/inventory safeguards remain. An old empty backup cannot establish that current remote authorizations are gone. |
| Registration committed, response lost | Persist registration ID/key first. Reuse the same SSHSIG registration request to recover signed metadata; cleanup can recover this result before revoking. |
| Partial created before metadata save | Reuse only the bound private, no-follow partial with no completed ranges; validate transfer data through the existing checksum path. |
| Revoke committed, response lost | Retain and reuse the same challenge/request ID/nonce. |
| Receipt saved, Host release response lost | Re-verify the saved Ed25519 envelope and all endpoint/task/generation/challenge/drain bindings; retry Host terminal release; only then remove the private key files. No network is required when the saved proof is valid. |
| Host release succeeded, key deletion incomplete | Same verified receipt and already-released Host terminal state permit idempotent local cleanup. Failed proof/Host/persistence checks keep keys. |

The service accepts claim/adopt only over the existing authenticated Host channel; the private main authority whitelist admits them, while panel and public plugin operations do not. CAS prevents two claimants from both receiving the same revision. The manager filesystem lock fences active download engines across Host instances before takeover.

Known conservative cases remain: orphan records without recoverable task metadata, legacy leases without signed proof, and cancellation before any registration identity was persisted require review. They are not auto-released. There is no offline administrator proof-review/unblock tool yet. Very large inventories are still unpaged. A process interruption during file publication may need manual inspection if the local publishing record and destination disagree; existing files are retained.

## Electron validation and identity boundaries

`scripts/test-host-electron.cjs` ran the **actual hidden Electron BrowserWindow, production main window code, sandbox preload and staged download engine**, at 1000×900. It exercised migration preview, Cancel/Allow responses, edit, backup/restore, error display, leased-target disabling, owner redaction and a second WebContents attempting download IPC. The test caught and drove the fix for the first-load readiness race. No window was shown or activated.

This test uses a real disposable SQLite configuration store but **synthetic Host transport and injected native-dialog answers**. It does not attest Security.framework identity, a human-operated native dialog, a Developer ID app or a remote production download. The separate native protocol suite exercises socketpair/FD3/SCM_RIGHTS/Security.framework with ad-hoc fixture identities. The repository-requested `$electron` skill was not present in the local skill catalogs/files; the committed Playwright Electron driver supplies reproducible supplemental evidence, not a claim of that skill's acceptance.

## Formal build gate

`scripts/check-host-release.py APP --bundle-id FORK_ID --team-id TEAM --manifest-sha256 REVIEWED_HASH` is a read-only final-package gate. It verifies Developer ID requirements, full strict/deep signature validation, exact reviewed manifest and file hashes, ASAR integrity metadata, restrictive fuses, main/worker/addon architecture CDHashes, team, timestamp, hardened runtime, forbidden entitlements, worker's narrow allow-jit entitlement, staple and Gatekeeper assessment. It cannot sign, install or enroll. Official upstream bundle IDs are rejected. Positive formal acceptance is **not run**: this machine has **0 valid code-signing identities**.

Build order remains pinned repo revisions/runtime → stage → finish runtime separation and fuse/entitlement work → nested signatures → regenerate manifest → outer app signature → final-package gate → notarization submission/log/staple → repeat final-package gate with the same reviewed manifest. The gate deliberately fails before notarization/stapling; do not weaken it to make an unfinished build green. Credentials remain outside source control. [Electron](https://www.electronjs.org/docs/latest/tutorial/fuses) requires fuse changes before signing; the [Apple workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow) remains the notarization reference.

Ad-hoc nested signing and the real separate-worker smoke ran successfully; these are temporary signing tests only. The new formal checker has only negative local evidence, not a valid Developer ID positive run. Full electron-builder/notarytool integration, fork identity/enrollment policy, all generic Node runtime entry points, restrictive main entitlements and x64/Rosetta need final release-machine validation.

## Evidence and remaining acceptance

See [test evidence](toolkit-host-7b9-validation.txt). The full GPU suite and existing Windows-compatible download tests were retained. Windows-only tests and three platform-dependent GPU tests are not Windows hardware acceptance. A disposable Docker run with network disabled verified actual Linux multi-UID SSH/authorization/configuration isolation; it is **not** macOS signed Host-to-service cross-UID acceptance.

No main changes, merges, upstream pushes, production installation, real configuration migration or production VPS connection were performed. **RELEASE BLOCKED** remains until the signed production download chain, unresolved legacy/orphan recovery, formal signing/notarization, macOS cross-UID and cross-platform acceptance are complete.
