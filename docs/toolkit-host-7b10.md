# 7B-10 — recovery, verified cleanup and download UI migration

2026-11-06 Asia/Shanghai. **RELEASE BLOCKED**.
Baselines: Orca `531e5684f6f3e1a486fa46d0fbd10d18b30e4589`; Toolkit `f6f90ce1eeb4af1717bbc80e60d9f94a173c7ace`.
Drafts: [Orca #1](https://github.com/facming/orca/pull/1), [Toolkit #15](https://github.com/facming/orca-tools/pull/15).

## Recovery and cleanup

Released is monotonic. A main-owned handle already at the requested terminal state treats repeated cancellation/cleanup as idempotent, preserving released=true. On restart, CAS claim still runs against the authenticated service; the engine takes terminal status from that record and revalidates the signed v3 receipt before deleting remaining local key files. A saved receipt supports offline cleanup after remote revocation. Invalid signatures retain keys even when the Host record is released. Even terminal retries go through the service: only the same released terminal state is idempotent, after current owner/epoch/revision checks. A former owner cannot use a cached terminal result to delete keys.

The local task intent is written before Host create. If Host create commits and the next local write fails, the in-memory intent remains retryable; the existing main handle and task ID are reused. Concurrent duplicate Host creates return the same handle; competing claimants still have only one CAS winner. Pending update request IDs survive response loss within the session, and persisted inventory supports takeover after restart.

Recovery overwrites local complete/verified claims with Host state. Unknown/legacy/mismatched tasks display review, never a forged local completion. Resuming a Host task with saved transfer metadata first recovers signed registration metadata and compares size/hash; altered JSON cannot choose its own expected checksum. Missing registered private keys are not silently regenerated. Completed ranges still use existing partial checks and final full-file checksum.

Released tasks with leftover local key directories block target edits and restores until cleanup. Unleased local files are not used as evidence of remote revocation. Orphan Host records, legacy protocols, changed epochs/targets, unknown registrations, and invalid files remain conservative review cases; leases and keys are retained. Empty task-key directories left by interruption also remain visible to the maintenance guard.

## Reused functionality and UI

The closed Host resource manifest now includes the existing RemoteBrowser, download-preferences and remote runtime modules through a small host-browser adapter. The main process supplies the packaged runtime SDK and scoped configuration client. Browsing keeps project-root, confirmed remote runtime, POSIX path, pagination, symlink and traversal checks. Successful directory browsing remembers the location per environment. Folder choice restores/persists the default directory; cancelled selection leaves it unchanged. A preference-save failure after task creation reports a warning without creating a duplicate task on retry.

The maintenance window offers remote projects/directories, filtering, hidden entries, parent/next-page navigation, file selection, native folder choice, progress, speed, retry count, explicit resume, cancel, clear access, and Finder reveal. Recovery errors disable task mutations. Retry recovery reconstructs the manager and rechecks the Host inventory after transient faults; active transfers must first be paused. Initialization failures release the manager lock so a subsequent request can retry. Task changes refresh maintenance restrictions without discarding edited configuration fields.

macOS ordinary Toolkit controllers no longer create download engines. Authenticated localhost tokens and Origin headers cannot invoke any download mutation route, including browse/preferences/picker/cleanup. The old macOS engine path without a Host lifecycle is rejected. Main's dedicated window/frame IPC retains strict command schemas; no owner, handle, arbitrary state or lease-release operation is exposed. Windows keeps its original controller/routes/transport. GPU, Blender, Godot, Unity and ComfyUI production implementations are unchanged.

## Acceptance boundaries

See [command evidence and fault matrix](toolkit-host-7b10-validation.txt). Real local filesystem, checksum, SQLite, SSHSIG, Ed25519 and native descriptor tests are distinguished from injected identity/range/RPC/dialog fixtures. The hidden Electron check uses the production window/preload and staged code, but synthetic Host identity and remote listings. No production VPS was contacted. Linux Docker SSH and cross-UID checks are real within a network-disabled disposable container; they are not macOS packaged Host-to-service acceptance.

## Local-use decision and signing

The user's current target is a working build on this Mac; paid Developer ID/notarization is deferred. No signing credentials need to be sent through chat. This decision does not authorize relaxing authentication, root protection, fuses, the reviewed manifest or task ownership. A local fixed-build enrollment path must bind exact code identity and reviewed resources. Removing quarantine only affects launch approval and does not satisfy Host/service authentication.

This stage produces ad-hoc signed nested Host resources and an ad-hoc service bundle for tests, **not an installable accepted local app**. Production enrollment deliberately still rejects ad-hoc identities. A complete local app, explicit protected enrollment/install workflow, restrictive main entitlements/fuses and generic runtime-entry-point verification remain necessary before claiming this Mac can run real downloads. The present installer also has an upstream-only caller-ID allowlist; fork/local identity enrollment must be resolved explicitly, not bypassed.

Read-only installed-app audit: `/Applications/Orca.app` version 1.4.249, bundle `com.stablyai.orca`, Developer ID publisher Lovecast LLC / team `6CX3WHS9HZ`, arm64, hardened runtime and valid stapled ticket. This is the existing upstream app, **not this fork's build**. It has no Toolkit opt-in or manifest, fuse wire `101100011`, and allows DYLD environment variables and unsigned executable memory. Its signature cannot be reused to sign our modified build. No installed file was changed.

Current keychain: 0 valid signing identities. No new Developer ID signing or notarization attempts. The formal release checker retains its strict gates and rejects the non-app staging directory. Final main/worker/addon CDHashes, restrictive main entitlements, complete app manifest/signature, notarization and macOS cross-UID acceptance are unperformed for this revision. x64/Rosetta and Windows hardware acceptance remain open. **RELEASE BLOCKED** remains.

No main changes, upstream pushes, merges, actual configuration migration, or production VPS connections. The user deferred local installation to a later step. No installed app or service was modified; installation-specific runtime experiments were removed from this phase.
