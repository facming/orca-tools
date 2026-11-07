# 7B-13 — macOS engine and workbench corrections

2026-11-06 Asia/Shanghai. **Draft / RELEASE BLOCKED**.
Starting heads: Orca `873616d11faa9a7f075f1855dcb7895253ad8239`, Toolkit `f248c808c13a1c47d4e557b73a760e41240d04d2`.
Draft PRs: [Orca #1](https://github.com/facming/orca/pull/1), [Toolkit #15](https://github.com/facming/orca-tools/pull/15).
macOS is the sole functional development/acceptance target; existing Windows code is retained.

| Feature | Implemented behavior | Validation / boundary |
| --- | --- | --- |
| TextEdit | UI/Orca/Bridge use the same `edit` command. Bridge checks approved project identity, descriptor-based path traversal, no symbolic/hard links, bounded regular file, extension, UTF-8 and no NUL. Host launches fixed `/usr/bin/open -g -b com.apple.TextEdit`; no caller program/arguments/environment. | Real background TextEdit launch from hidden Host test, Unicode/space paths; binary masquerading as text rejected. File remains in the project. External editor is not a sandbox or a race-free filesystem capability. |
| Unity 6000.5.4f1 | Explicit exact-patch admission, conditional EntityId API, decimal uint64 string wire IDs, protocol-2/object-ID heartbeat validation, version-aware tool schema. Existing 6000.0 int32 path retained. | Real compile against installed 6000.5.4f1 assemblies passes. Real batch Editor launch fails licensing (exit 198, missing `com.unity.editor.headless`), before heartbeat. Runtime acceptance is **BLOCKED**, not passed. |
| Unity operations | Disposable integration runner covers start, Metal/heartbeat, scene/hierarchy, create/get/delete object, transform, Camera, scene save, import/list assets, Play transitions, screenshot and console. | Reproducible script, no real project edits. Runtime steps after license failure are **NOT RUN**. 6000.5.4f1 Built-in only; URP/HDRP remain rejected pending real review. No package or project upgrades. |
| Blender / Godot installation | Native file chooser selects an existing application. Bundle/Mach-O/arm64/permissions/version checks precede selection; Godot also checks required CLI capabilities. Shows available, not installed, incompatible, run failed, checks and version. | Selected path applies to this Bridge session; private service config is unchanged. No installation or automatic project mutation. Synthetic application/probe cases pass. Final scan found Blender 5.2.3 LTS; its official LTS version suffix is now accepted. Godot remains absent in standard roots. |
| Blender real integration | Installed 5.2.3 LTS: scene/object/transform, OBJ export/import, blend save/open, CPU and Metal Cycles render, hashed PNG retrieval. Background-mode service loop supports reproducible headless testing. | **PASS**, new disposable project/private settings, real processes. Metal used one device without CPU fallback. No software installed by this task. |
| Native projects/tasks | Reuses discovery, source browsing/editing, per-engine submission, status/results/logs/cancel; adds common Unity object/transform/component entries. Explicit project task authorization remains mandatory. | Actual Host/main/preload/loopback/filesystem, synthetic engine execution. JSON arguments remain available for advanced operation parameters. No arbitrary execution interface added. |
| Assets / downloads | Native file chooser copies an existing downloaded asset to an approved project; task artifacts export to selected project. New paths only, same supported non-script suffix, no overwrite, 32 MB limit; Unity uses Assets-path validation. Source stays unchanged. | Real temporary files and PNG export tested. Larger artifact transfer remains existing MCP functionality. Formats are bounded to png/jpg/jpeg/obj/glb/vrm/blend/unity/wav/mp3/mp4. This is explicit copy/import, never automatic execution. |
| ComfyUI versions | Imported API graphs support update, delete, history and restore, with SHA-256 revision preconditions; history archived before replacing/removing the current graph. Task records retain executed workflow revision. | Stale update rejected; delete/restore tested in real private temporary storage. Existing user workflow directories are read-only. History is content-addressed, not a timestamped audit log. |
| ComfyUI results | Typed parameters, queued/running/terminal/unknown status, errors, PNG preview (validated, <=1 MB), project export. | Synthetic Comfy adapter with actual image rendering in Host. No invented inference percentage; no running-job global interrupt. No real inference environment was supplied. |
| Unknown submissions | Existing request-ID deduplication is preserved. Polling only inspects jobs; unknown acknowledgements never trigger automatic resubmission. | Lost native acknowledgement and same-ID retry remain one job; Comfy lost acknowledgement then retry remains unknown with one backend submit. |

## Unity API review

Unity's [6.5 migration guide](https://docs.unity.com/en-us/engine/6000.5/manual/scripting/fundamental-unity-types/instanceid-to-entityid-migration) documents the move from int InstanceID to 64-bit EntityId and obsolete API errors. The extension now conditionally uses `GetEntityId`, `EntityId.ToULong` / `FromULong`, and `EditorUtility.EntityIdToObject` on 6000.5. Older supported editors retain their original APIs. String transport prevents JavaScript precision loss. Compilation uses the actual local Editor assemblies, not stubs. Exact project/editor version, extension bytes, Metal and pipeline checks are retained. This is not a claim that every Unity 6 release is supported.

Run from Toolkit:

```sh
python3 scripts/test-macos-blender-live.py
python3 scripts/test-macos-blender-live.py --cpu
python3 scripts/test-unity-compile.py
python3 scripts/test-macos-unity-live.py
```

The live runner creates and preserves a new disposable project and Bridge directory,
adds only the reviewed extension to that fixture, and launches a real Editor with
production arguments plus `-batchmode` and a fixture log destination. It uses the
existing process/session transport and stops only its owned process. It does not
activate the UI, acquire a license, modify a real project, or migrate user config.
A license-enabled isolated macOS session must rerun it to complete acceptance.

## Remaining risks

Full Unity Editor/Metal/Play/domain reload/screenshot/import acceptance is license-blocked; only API compilation has passed on 6000.5.4f1. Old 6000.0 protocol regressions pass synthetically; no older real Editor was rerun. URP/HDRP for 6000.5 remain deliberately unsupported. Godot real process behavior and Comfy inference remain unperformed. Blender real CPU/Metal rendering and common project operations pass; visible UI, VRM extensions and broader scene workloads remain unverified. Engine session selection is intentionally not persistent configuration migration. TextEdit document save/return is still manual.

The Host keeps finite commands, existing admission, revocation and closed manifests. Data-image CSP permission is limited to inline previews; no network image permission added. User scripts still run with user permissions and explicit consent, **not in a sandbox**. Local packaged identity/enrollment, production admission, signing/notarization and independent review remain open. No main changes, merge, official upstream push, production installation, real configuration migration or production VPS connection. **RELEASE BLOCKED** remains.
