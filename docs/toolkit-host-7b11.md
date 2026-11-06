# 7B-11 — product regression and usable Host entry points

2026-11-06 Asia/Shanghai. **RELEASE BLOCKED**. Draft PRs remain [Orca #1](https://github.com/facming/orca/pull/1) and [Toolkit #15](https://github.com/facming/orca-tools/pull/15).
Baselines: Orca `88e8a5f4055493616807918c10a52aa90a8085c7`, Toolkit `61511d7c40f81c8b9aa923eede7f9b4b8af69d55`.

## Feature checklist

| Feature | macOS Host result | Windows / verification boundary |
| --- | --- | --- |
| Entry points | Help → Orca Toolkit offers Accounts, Downloads, GPU & native tools, Settings. Existing window navigates to the requested section. Ctrl+Alt+T/D/G have native menu accelerators. Ordinary plugin commands give the exact Host route; pause no longer reports a false success. | Original manager, shortcuts and GPU startup retained. Previously unregistered configuration command now opens Settings. Physical shortcut conflict testing remains local acceptance. |
| Accounts | Reuses existing account UI, masked identities, usage provenance, stale-selection checks and disable preferences. Reads remain passive until Refresh. Settings changes during preflight abort switching; failures discard cached actionable state. | Existing HTTP account UI retains its transport; failure recovery improved in both views. |
| Downloads | Readable bytes/speed, estimated time, preparing/checksum/stopped states, pause/resume/cancel/pause-all, verified access cleanup, warnings, recovery retry and folder reveal. Cancellation explicitly confirms partial-file deletion. Successful creation resets its request ID; unknown outcomes retain the ID. | Original Windows engine/UI/routes retained. Shared range, checksum and recovery tests pass. |
| Remote browsing | Authorized project roots, path validation, directory memory per connection, default local folder, picker cancellation, Enter-to-filter, hidden files, previous/next page. A preference failure does not hide successfully loaded files. | Existing picker, browsing and preference tests retained. No production environment queried. |
| Settings | Human-readable field labels, visible outcome/error text, collapsible request details; no-change save feedback. Existing field patches, unknown values, version conflict, import/restore approval and leased-target guard remain. | JSON editing and existing Settings view retained. |
| GPU / ComfyUI | Host window displays all four engine states, workflow availability and an explicit ComfyUI manager entry. The original local manager provides ComfyUI start/stop. Fixed missing Blender/Unity/ComfyUI state propagation. | Existing Windows project/workflow UI and automatic bridge startup unchanged. |
| Blender / Godot / Unity | Existing Bridge/MCP implementations and local project authorization retained. Host shows each engine state and explains the MCP operation route. | **Native project editing, execution and task controls are not yet embedded in the macOS maintenance window.** Windows keeps its richer native project UI. This remains a product parity gap, not a claim of complete parity. |

The Host tools channel is scoped to the sealed window's exact WebContents/main frame/URL, validates finite commands, reads configuration internally and closes with the window. It exposes no arbitrary RPC, configuration authority, task ownership or caller-supplied environment. Account and GPU implementations are reused in the closed manifest; Windows modules are not replaced. No Host Authority, transport or configuration-service redesign.

## Regression and remaining risks

See [command evidence](toolkit-host-7b11-validation.txt). Automated checks cover both engine adapters and UI behavior using disposable configuration, synthetic remote accounts/listings/health, actual local filesystem/SQLite/native descriptors, signed proof validation and network-disabled Linux SSH isolation. A hidden Electron window exercises production preload/main and staged resources; Chromium exercises download controls with explicitly synthetic tasks. These are not real-account, GPU, installed-app or Windows hardware acceptance.

Remaining: macOS native project/task GUI parity; real four-engine/Metal/Unity C# execution; packaged app/service cross-UID acceptance; Windows and x64/Rosetta hardware; installer caller-ID allowlist and local fixed-build enrollment; formal signing/notarization. Legacy/orphan/unprovable download authorization continues to require review and keeps leases/keys. No completion state is inferred from UI, and no unverified receipt authorizes key deletion.

## Later local temporary-signing / installation acceptance

Installation remains deferred. The next local-build step should pin both reviewed commits and Node 24.22.1, build the full fork into a separate output directory, stage the closed Toolkit resources, sign nested binaries before regenerating the manifest, then seal the outer app. Record exact code identities, entitlements, fuses and resource hashes. Current `stage.mjs` plus `seal.mjs - --test-only` produces test resources only, not an accepted installed application.

Before installation, implement/review a protected local fixed-build enrollment path and fork identity allowlist without enrolling generic interpreters or weakening the existing service checks. Prepare an isolated test profile, synthetic configuration, disposable remote authorization fixture and rollback steps. Do not replace `/Applications/Orca.app`, enroll a service, migrate user configuration or connect a real VPS in this stage.

At the later explicitly authorized installation step: retain the existing app, install the isolated candidate and verify exact identity admission, all menu/shortcut entry points, settings conflict/reload, account switch confirmation, folder persistence, large/interrupted download recovery, checksum failure, lost revoke reply, restart/orphan inventory, and every native engine on real hardware. Verify rollback removes only the candidate and its isolated test data. Developer ID/notarization may remain deferred for local use; local identity, fuses and proof boundaries may not. **RELEASE BLOCKED** stays until the relevant acceptance is complete.
