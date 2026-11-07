# 7B-12 — macOS AI/GPU project workbench

2026-11-06 Asia/Shanghai. **Draft / RELEASE BLOCKED**.
Baselines: Orca `f020c44215888df0d0d178c6559b4a86f9f37fcf`, Toolkit `0a6fcf475564f18da063b53da247b05ade15554d`.
PRs: [Orca #1](https://github.com/facming/orca/pull/1), [Toolkit #15](https://github.com/facming/orca-tools/pull/15).

macOS is the actual product target. Windows code remains, but Windows UI parity,
special skip remediation and hardware acceptance are no longer development goals
or release criteria for this macOS workbench. This supersedes earlier parity plans.

## Feature checklist

| Area | Delivered | Boundary |
| --- | --- | --- |
| Host entry | Help → Orca Toolkit → Project workbench; navigation alongside Accounts, Downloads, GPU status and Settings | Only the sealed Host window/main frame can call the fixed tools channel. Ordinary plugin RPC permissions unchanged. |
| Projects | Discover and select locally approved Blender/Godot/Unity projects, filter by engine | Reuses each engine's project identity and authorization checks. New root approval remains local Bridge setup; no automatic filesystem enrollment. |
| Files | Browse folders, select assets, preview bounded UTF-8 source, edit source in TextEdit | Descriptor-based reads reuse the existing no-link bounded-file reader. Binary assets go through engine operations. External editor entry, not an embedded save engine; refresh after saving. |
| Blender | Scene inspection, open/save blend, import/export asset, create cube, Cycles render, run approved Python file | Existing Bridge operations and argument validators. Python requires local script permission plus explicit consent. Outputs cannot replace existing files. Persistent Blender sessions retain their existing Bridge lifecycle. |
| Godot | Import/open/run project, run GDScript, capture scene, stop owned session | Existing approved roots, script consent, timeout and task cancellation; no adoption of external processes. |
| Unity | Start/stop managed Editor, scene/hierarchy inspection, create object, save new scene, Play start/stop, Game view capture, console, assets/import | Existing finite C# API and exact version checks. Cancelling pending Unity work may end its managed session. |
| Tasks | Submit with request ID, refresh saved tasks, inspect result/artifact metadata/logs, cancel, display backend state/progress/error | Lost acknowledgement retries retain the same ID. No automatic resubmit; no invented percentage when engine has no numeric progress. Latest 100 tasks shown. Artifact binary export remains via existing MCP. |
| ComfyUI | Start/stop managed instance, refresh workflow catalog, availability/reason, typed parameter controls, import new API workflow, submit/inspect/cancel pending task | Reuses workflow validation, library and Bridge job recovery. Duplicate import ID refuses overwrite. Unknown outcomes remain visible; running jobs cannot be interrupted through this Bridge. |
| Downloads → project | Set approved project root as download destination; verified completed resources offer a project-workbench entry | No silent asset copy, project permission grant or auto-execution. Existing files downloaded outside an approved root must be moved by the user before engine import. |
| Accounts/remote/settings | Regression covers account switch, remote project browsing, saved folder/cancel, settings import/patch/backup/restore, target lease restrictions; explicit Reload invalidates stale module selections | Synthetic accounts, authorization identity and remote endpoint. No real account/VPS accessed. |
| Actual discovery | Read-only local qualification script; malformed unrelated application plist no longer aborts discovery | Metadata-only scan of application directories, never reads or modifies real Bridge configuration. |

The Host schema rejects arbitrary actions, caller environments, paths outside project
operations, and unknown envelope fields. The Toolkit transport uses the fixed
loopback Bridge endpoint, private existing credentials, bounded bodies/responses and
no redirects. The new admin dispatcher repeats security/token checks after body
read, then delegates to existing engine stores. No Host Authority or execution
engine replacement. The new module is included in all three closed manifest lists
(stage, seal, runtime) and future installer resources.

## Verification and remaining risks

See [reproducible evidence](toolkit-host-7b12-validation.txt). Actual macOS hidden
Electron/preload/main, real loopback HTTP, temporary project files, SQLite and
native task persistence are tested. Engine processes, remote accounts, dialog
answers, TextEdit launch and ComfyUI execution are synthetic in that test.

The read-only hardware qualification found no qualified Blender or Godot in the
standard application roots. Installed Unity **6000.5.4f1** is outside the existing
**6000.0** adapter qualification. No disposable ComfyUI environment was supplied.
Thus real Metal/GPU rendering, Unity C# execution and managed ComfyUI inference
remain **NOT RUN**. This is not engine hardware acceptance. No engine was installed
and no production app, profile, service or configuration was changed.

Remaining product work: qualify the actual Unity series separately; real engine
acceptance; richer native task parameter forms, artifact previews/export in Host,
workflow update/removal and direct resource placement for downloads outside approved
roots. Large-directory browsing currently refuses more than 2000 visible entries;
text preview is bounded to 256 KB. These limits are displayed as actionable errors.
Local signing/enrollment, packaged Host/service admission and independent security
review remain release blockers. Existing conservative download lease/proof rules
are unchanged. Windows hardware and UI alignment are intentionally out of scope.

## Deferred local installation procedure

Installation belongs to a separate, explicitly authorized stage:

1. Pin the two reviewed commits and repository-pinned Node 24.22.1. Build the fork
   into a new output directory. Run the changed gate and local engine qualification.
2. Stage Toolkit resources with `native/toolkit-host/stage.mjs`; sign nested Node
   and addon, regenerate/verify the closed manifest, then seal the outer app. The
   disposable `seal.mjs … - --test-only` route used here is not installed admission.
3. Prepare/review fixed-build local enrollment and caller allowlist, actual fuses,
   entitlements and resource hashes. Never enroll a generic interpreter. Prepare a
   candidate-only profile and test service, synthetic config, test project roots,
   version-matched Unity extension and disposable download authorization endpoint.
4. In the later authorized stage, retain the current application and install only
   the isolated candidate. Configure/approve test projects locally. Verify menus,
   settings conflicts, account switching, remote browse, interrupted downloads,
   verified cleanup, four engines, external editor return, task cancellation,
   Bridge restart/unknown outcomes and project resource handoff on actual hardware.
5. Roll back only candidate app/service/profile/test data. Do not replace or migrate
   the current user's configuration until a separate migration is approved.

No production VPS, main changes, upstream writes or PR merge. **RELEASE BLOCKED**.
