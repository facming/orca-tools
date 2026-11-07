# 7B-19 — Toolkit UI and remote Claude accounts

Claude account execution stays on the configured VPS. The Mac is a frontend. This stage extends the existing trusted Host tools IPC and paired Orca account APIs; it adds no RPC listener and does not replace the installed Orca app, system login, download engine, Bridge permissions or native engine implementations.

## Account flow

Accounts always shows **Add Claude Account**, including when System Default is the only real login. The Host calls the VPS's existing `accounts.hostSignInStart/Wait/SubmitCode/Cancel` flow. Orca creates its independent account directory, runs Claude OAuth and publishes the account only after successful authentication. No credentials are copied from System Default, and no placeholder account is added.

1. Click **Add Claude Account**. Wait for the remote sign-in page to become available.
2. **Copy link for VPS browser** puts the provider link on the Mac clipboard. Paste it into Firefox on the VPS through RustDesk and complete authorization there. The Mac browser button and its Host IPC operation have been removed; the Toolkit account flow has no Mac browser opener. Do not run `claude auth login` against the VPS's default directory.
3. Paste the provider's one-time authorization code into Toolkit and click **Complete sign-in**. The field clears immediately. This code is relayed transiently through the trusted IPC and existing paired RPC; OAuth access/refresh tokens and account folders stay on the VPS. Neither the URL nor raw account snapshot is returned by the Host login API to the panel, and no code is persisted in logs, settings or Git.
4. On success the list and usage refresh automatically. Select **Use this account** to change the runtime selection; the active card says **Currently selected**. Existing sessions keep their account; verify a newly created session when accepting a second account.
5. Cancel is available while signing in. Window closure cancels its owned flow; Orca's existing timeout and abandoned-owner cleanup remain in charge of remote process/folder cleanup. Connection interruptions remain visible and are not reported as success.

System Default is protected from deletion/disable in Toolkit and ignores old local disable preferences. A current managed account must first be deselected before disabling or deleting. Disable/enable is the existing per-connection Toolkit preference; it prevents Toolkit selection, not other Orca clients or existing sessions. Delete invokes Orca's managed-account removal on the VPS after an explicit impact prompt. Switch errors attempt restoration through the existing selection API; an unreachable runtime is reported as unconfirmed, never as a successful rollback.

Account emails are masked when the VPS supplies them. The currently running VPS does not include System Default email or Session reset time in its snapshot: those fields remain unreported, rather than invented. Usage provenance checks remain in place; older hosts that cannot attribute managed-account usage display Unknown instead of another account's usage.

## UI and downloads

The actual Host panel now uses a light blue/purple theme, sidebar navigation, cards, consistent form controls, status badges, usage meters and responsive layouts. Project workbench, remote browser, ComfyUI, Blender/Godot/Unity and configuration maintenance retain their existing operations. Download cards show source/destination, size, progress, speed, ETA, retry/checksum state and cleanup warnings, with the existing pause/resume/cancel/reveal actions. Failed or pending re-verification does not show a green completed badge. Cleanup warnings offer **Retry authorization cleanup**.

## Current Mac entry

The previous 7B-18 build and installed `/Applications/Orca.app` are retained. The separate development entry is:

`~/Library/Application Support/OrcaToolkitDevelopment/live-20261107/launch-7b19.command`

Use the existing `stop.command` before changing between entries. Both versions share the existing development lock, configuration and download history; do not run both at once. The new entry uses the same supervised download tunnel. Double-click the new command in Finder; agent verification uses hidden windows only. No Windows UI is included.

The launcher now targets the VPS-browser-only build. Restart the development window to load this update after completing or cancelling any current sign-in. Updating the launcher does not restart an existing window.

## Acceptance — 2026-11-07, Asia/Shanghai

- Real paired VPS: one System Default; zero managed accounts at validation time. Session 0%, Weekly 32% used; weekly reset provided, session reset absent.
- Real OAuth started on the VPS, reached `waiting` with a valid provider prompt, then cancelled. Subsequent reads still returned the same single account and valid usage. The actual hidden Host/preload UI also completed this start/cancel sequence.
- Hidden Electron screenshots inspected at 1120×860, 760×860 and 480×860; no horizontal overflow. Other page navigation also checked at 480px.
- New development entry loaded System Default and all six pre-existing download history records (five complete, one cancelled); no new download or replacement installation was needed.
- Automated remote-login relay/URL privacy/local-runtime rejection tests, account service regressions and download UI/lifecycle checks are separate synthetic evidence, not second-account acceptance.
- Pending human authorization: successful managed sign-in of a second real account, real account switching/new-session identity, and real managed-account disable/enable/delete. These are not claimed as passed. The user has registered the second account; authorization will be completed in the VPS browser through RustDesk.

The original Draft PR branch/base pairs are preserved: `facming/orca#1` (`feature/toolkit-trusted-host` → `baseline/installed-1.4.249`) and `facming/orca-tools#15` (`feature/macos-orca-host-integration` → `feature/macos-security-finalization`). The existing local Orca lockfile modification is excluded.
