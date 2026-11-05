# PR #14 finalization evidence — 2026-11-05

Base: `feature/macos-security-implementation`, `b66d7d9` (PR #13).
Delivery branch: `feature/macos-security-finalization`. No merge or main changes.
All data used below is synthetic. No VPS, real user configuration, production
service account, LaunchDaemon, application signature or SSH policy was changed.

## Result and release boundary

The Bridge confused-deputy path now requires two independently pinned code
identities: Bridge and original request-channel creator. SCM_RIGHTS transfers the
original socket; the service obtains its kernel audit token, validates live code
with Security.framework, and reads the request from that very socket. A same-UID
program cannot borrow Bridge's signature. Claimed JSON identities are ignored.
Both hops still require the tenant UID; UID alone is never sufficient. Production
service construction still refuses root and the tenant UID.

**Production integration is not complete.** The existing Orca plugin worker uses
Electron RUN_AS_NODE. Its executable signature cannot distinguish Toolkit from an
arbitrary script. The installer now refuses such a caller. Production needs an
opted-in, sealed Orca caller with reviewed Toolkit payload and no untrusted script,
plugin, debug or raw configuration proxy entry points. This repository does not
contain that host build. The positive native tests use synthetic signed caller
executables, not the installed Orca app. Do not interpret them as real Orca host
acceptance. See the [enrollment contract](macos-security-deployment.md).

## Functional review and regression

| Area | Evidence / result |
| --- | --- |
| Caller identity | 8 native/policy tests: authorized fixture migration/read/patch/backup/restore and restart, unapproved same-UID executable with the same signing identifier, direct signed Bridge, direct unsigned socket client, fake PID/UID/token/requirement, malformed/missing/multiple/truncated descriptors, real Node spawnSync channel identity, unsafe Electron fuses, service UID guard. All pass. |
| Config storage | 7 storage tests: independent-field merge, overlap/ABA rejection, atomic multi-document patch, exact unknown JSON numbers, explicit migration snapshot, idempotence, backup/new epoch restore, persistent target leases, SIGKILL journal recovery, allocation failure rollback, corruption and unsafe permissions fail closed. All pass. |
| Multi-Controller | Two real Controller objects against the SQLite fixture merge independent fields and reject stale overlapping updates. Sparse legacy migration now supplies missing UI defaults without changing unknown numeric values. |
| Install/disable | Bundle compiled and ad-hoc signed/verified; production never accepts ad-hoc enrollment. Installer tests cover refusal without an original caller and disable-before-bootout ordering. Fixed unload so the retained RunAtLoad plist cannot silently reactivate at the next boot. Actual system installation remains manual. |
| Downloads v3 | Real SSHSIG registration and Ed25519 receipt verification against persistent authority SQLite; retry, forged/binding/nonce/schema/stale/pending failures; keys retained after persistence/lease failure. Extended manager test pauses with key retained, reconstructs manager, resumes, verifies output, revokes and removes key. Range transport in this manager test is synthetic. |
| SSH roles and reads | Disposable `--network none` Docker: real distinct UIDs and real dedicated loopback sshd. Direct DB/journal/program/key attacks denied; forced ranges work; shell/PTY/invalid range and changed host pin fail; personal keys independent. |
| Revocation/restart | Docker: active lease makes revocation PENDING; new SSH denied; dispatcher ACK permits DRAINED; concurrent registration/revocation, permanent tombstones and authority restart pass. Damaged configuration recovered into a new directory/epoch; original retained. |
| Key deletion | Signed complete receipt is validated and persisted/fsynced before target lease release and local key deletion; forged, pending, stale, unreachable, persistence and lease failures retain key. Existing cryptographic/ordering checks pass. |
| Windows | Node Windows adapters, JSON storage, download transport and mocked platform paths pass. Native Windows execution was not available; existing Windows-only skips retained. |

Executed commands and totals:

- `node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs`: **94 total, 93 pass, 1 existing Windows-only skip**.
- `python3 -m unittest discover -s plugins/toolkit/config-service -p 'test_*.py'`: **15/15 pass** (8 identity/policy + 7 storage).
- `PYTHONPATH=plugins/toolkit/config-service:services/download-authorization python3 -m unittest discover -s services/download-authorization -p test_authority.py`: **5/5 pass**.
- `python3 -m unittest discover -s services/local-gpu-bridge -p 'test_*.py'`: **301 total, 298 pass, 3 Windows-only skips**.
- `python3 -m unittest discover -s plugins/toolkit/modules/downloads -p 'test*.py'`: **13/13 hardening tests**, plus both existing top-level remote reader acceptance scripts pass.
- `python3 -m unittest discover -s scripts -p 'test*.py'`: **8 total, 5 pass, 3 Windows-only skips**.
- `python3 tools/account-switcher/test_panel.py`: live synthetic HTTP checks pass.
- `node --test tools/orca-startup/test-gpu-health.cjs`: **6/6 pass**.
- `docker build -t orca-security-test:finalization -f services/download-authorization/Dockerfile.test .` and `docker run --rm --network none -v "$PWD:/src:ro" orca-security-test:finalization`: six acceptance groups pass.
- Native identity fixture C compiled with `-Wall -Wextra -Werror`; production bundle built with `scripts/build-security-bundle.py --output /tmp/orca-security-finalization-build --signing-identity -` and signed requirements verified.
- `python3 scripts/check.py` and `git diff --check`: pass (final file count recorded in PR).

The native identity suite uses the production relay function and actual macOS
Security.framework, with a same-UID transport-only fixture so it needs no sudo.
It does not alter the production Bridge UID gate or the service UID gate. The
updated `scripts/test-macos-security-isolation.py` exercises the complete Bridge
main and independent service UID, but was **not rerun**: `sudo -n` requires a
password on this machine. Linux Docker cross-UID storage tests are not a substitute
for macOS code identity tests and are not counted as such.

## Remaining manual acceptance

1. Integrate and review a sealed authorized Orca Toolkit host; confirm all normal
   settings and maintenance commands run in that process. Test generic Node,
   Electron RUN_AS_NODE, script/plugin injection and unsigned re-signing attempts
   against the actual production package. Do not weaken enrollment for compatibility.
2. Developer ID chain/team selection, signed resources/entitlements, fuses,
   notarization, Gatekeeper, root installation, exact CDHash pin and upgrade
   re-enrollment; real LaunchDaemon boot/stop/reboot and the updated sudo harness.
3. Real VPS sshd entry points and role/ACL/sudo restrictions, actual network drain,
   whole-host restart and recovery. No production VPS connection was made.
4. User-selected real migration preview/confirmation, unknown fields, backups,
   rollback/new epoch and task reconciliation. No real data was migrated.
5. Electrical power loss, storage-controller faults and full VFS short-write/EIO/
   fsync fault injection remain beyond SIGKILL/SQLite allocation-failure tests.
6. Native Windows acceptance and historical legacy Downloads manual cleanup.

H-02's specific Bridge-only authorization defect is repaired in code. Release
remains blocked on host integration and manual acceptance; H-03's previously
recorded production/legacy risks are not reclassified as resolved. The old
`reproduce-unresolved-races.py` exercises retired helpers and is not used as a
success metric. PR remains Draft.
