# Isolated configuration and download authorization deployment

This is an administrator runbook, not an automatic installer invocation. The
implementation branch does not deploy services or migrate user data. Keep the PR
Draft until independent security review and human approval.

## macOS configuration service

Requirements: dedicated non-login `_orcatoolkit` UID, root-owned Python runtime
and libraries, Developer ID Application signing identity, root-protected program
and state parents, a local filesystem supporting SQLite locks and synchronization.
Do not use a desktop-owned Homebrew/pyenv interpreter in the LaunchDaemon. The
installer rejects writable interpreter ancestors. The Python runtime and its
stdlib must be maintained as part of the installed trusted bundle.

1. Build outside the repository (the output must not already exist):

   ```sh
   python3 scripts/build-security-bundle.py --output /tmp/orca-security-build --signing-identity 'Developer ID Application: YOUR IDENTITY'
   ```

2. Review the manifest and designated requirement. Choose an unused service UID
   with your administrator. Install explicitly, replacing the example numbers and
   interpreter with your reviewed values:

   ```sh
   sudo /usr/bin/python3 scripts/install-security-macos.py install --tenant 501 --service-uid 490 --bundle /tmp/orca-security-build --caller-app /Applications/OrcaToolkit.app --python /usr/bin/python3
   ```

   This creates a non-login identity only if absent, installs immutable code,
   initializes a *new* database in migration-pending state, and loads a tenant
   LaunchDaemon. It refuses existing installations/databases. There is no
   privileged code in the desktop runtime and no JSON fallback.

3. Stop old Controllers and select both migration sources explicitly. Invoke
   `toolkit.configuration` **inside the enrolled Orca Toolkit host**, with these
   command arguments (save the returned preview JSON to a selected local file):

   ```json
   {"operation":"preview","args":["/SELECTED/settings.json","/SELECTED/downloads-preferences.json"]}
   ```

   `--defaults` may replace either source only when intentionally selecting
   defaults. Inspect `documents.*.raw_json`, hashes and unknown fields. Confirm
   those captured bytes with `{"operation":"confirm","args":["/SELECTED/preview.json"]}`,
   then `{"operation":"status"}`. This command calls the exported
   `config-service/migrate.cjs` API within the authenticated host. Standalone
   `node migrate.cjs` is **not authorized in production**; do not whitelist Node
   to make the old command work. A caller packaging integration must expose this
   in-process command before production acceptance.

   Original bytes remain in SQLite `sources`; original JSON is untouched and no
   longer consulted. Repeat confirmation is safe only with the same request ID;
   a second import otherwise fails. IPC callers must retain request IDs when
   recovering an uncertain result.

4. The same host command with `{"operation":"backup"}` uses the SQLite backup
   API. `{"operation":"restore","args":["<backup-id>"]}` restores
   documents through a new transaction and changes epoch; active target leases
   block restore. It preserves current audit/request history. A structurally
   corrupt active database is deliberately **not** automatically replaced; stop
   the service and run the installed `recover.py --state <old-dir> --backup-id
   <selected-backup> --output <new-dir>` as its service UID. It verifies the
   backup and creates a new epoch in a new directory, keeping the damaged
   original and journal. Before switching the stopped daemon, independently
   reconcile all local download tasks/leases created after the backup. There is no
   command that silently constructs an empty replacement.

5. Unload without removing data:

   ```sh
   sudo /usr/bin/python3 scripts/install-security-macos.py unload --tenant 501
   ```

   The account, code, database, tombstones, backups and source snapshots are
   retained. Final removal or reinstall is a separate administrator decision.
   `launchctl disable` also prevents loading at the next boot; `bootout` stops
   the current instance. Re-enable deliberately with `launchctl enable
   system/com.orca.toolkit.config.<uid>` before bootstrap.
   Unload/rollback does not re-enable desktop JSON writes.

IPC uses two independent code identities. The fixed Bridge verifies the service
UID, then transfers the Host-created Unix socket inherited explicitly as fd 3 using `SCM_RIGHTS`. The service
checks the Bridge's kernel credentials and designated requirement, obtains the
original channel's `LOCAL_PEERTOKEN`, and checks that live caller with
`SecCodeCopyGuestWithAttributes(kSecGuestAttributeAudit)` / `SecCodeCheckValidity`.
It reads the JSON **from that same original channel**, not from a Bridge-supplied
copy. A claimed PID, UID, executable path, requirement, name or audit token in JSON
has no authority. Plain pipes/files, missing/extra/truncated FDs, direct unsigned
clients and signed Bridges launched by unapproved programs fail before dispatch.
The sealed main process must create the socketpair through an in-process native binding,
pass the original endpoint as child fd 3, and retain its endpoint through the reply.
Bridge stdin is ignored. See [the Host API design](toolkit-host-api-design.md).

Production enrollment additionally requires a root-protected, resource-sealed
Developer ID app, hardened runtime, no debugger/library-validation/DYLD/unsigned
executable-memory exemptions, bundle ID `com.stablyai.orca` or
`com.stablyai.orca.toolkit`, and signed Info.plist integer
`OrcaToolkitConfigClient=1`. This opt-in is a publisher assertion that the enrolled
process runs only reviewed Toolkit code: no arbitrary CLI scripts, stdin eval,
external writable plugins, debug ports, or raw configuration proxy exposed to
untrusted programs. Security.framework authenticates code, not a JavaScript
module inside a general interpreter. The administrator must review that contract.
The installer derives the designated requirement and pins the exact CDHashes of
the approved release in the root-owned LaunchDaemon arguments. App upgrades need
explicit re-enrollment. Request arguments/environment cannot override the policy.
For Electron, RunAsNode, NODE_OPTIONS and CLI inspection fuses must be off, and
embedded ASAR integrity and ASAR-only loading must be on before signing.

**Current Orca RUN_AS_NODE plugin workers do not satisfy this contract and cannot
be enrolled.** This repository does not contain the Orca host build. Integrating
Toolkit into a suitably sealed, signed caller process is an outstanding production
prerequisite, not something this installer silently fixes by relaxing identity.
The native acceptance tests use explicitly pinned synthetic signed Orca caller
fixtures; they do not claim that the currently installed Orca package is approved.
Production Developer ID/notarization, host integration and LaunchDaemon acceptance
remain manual release gates. No same-UID or generic interpreter fallback exists.

References: [Apple guest identity API](https://developer.apple.com/documentation/security/seccodecopyguestwithattributes(_:_:_:_:))
and [Electron fuse semantics](https://www.electronjs.org/docs/latest/tutorial/fuses).

State: `/Library/Application Support/OrcaToolkitState/<uid>` (0700); SQLite and
journal 0600; socket directory is the persistent sibling `run` (0755), owned by
service UID under the root-owned state parent. Socket 0666 allows connections,
but every peer is authenticated before parsing. No socket access grants DB access.
One lifetime lock and one serialized connection own writes; DELETE/EXTRA,
fullfsync and foreign_keys are checked. A stale socket is removed only after the
service obtains its exclusive database lock; an unexpected path type/owner fails.

## Dedicated Linux download authority and SSH instance

1. Administrator creates **four distinct roles**: existing workspace owner,
   authority non-login account, lookup non-login account, download account. No role
   is root; reader/lookup/workspace must not be able to modify authority state or
   `/opt/orca-download`. Download account must have no sudo/password capability.
   Its shell is needed to invoke the fixed command; every SSH entrance must deny
   unrestricted access to it. If the existing SSH instance cannot meet that
   requirement, use an isolated container/host. Do not silently edit its config.
2. Install a root-owned Python environment with
   `services/download-authorization/requirements.txt`. Copy and edit
   `policy.example.json`: approve project roots, workspace UIDs, export quota and
   identities. Source files must be owned by the authenticated workspace, regular,
   single-linked, and reachable without following any symlink component.
3. After review, run `install.py --policy <reviewed-file> --authority-user <user>
   --python <protected-python>`. It prepares the bundle, private state and systemd
   unit but does **not** start the authority/sshd. It prints the receipt public key
   and epoch. Existing destinations are never overwritten.
4. Review `sshd_config.example` as a **separate** sshd instance. Set the approved
   Tailscale ListenAddress, port, root-owned host key and reader account. Use
   `sshd -t -f <file>` and `sshd -T -f <file>`; retain `AuthorizedKeysFile none`,
   fixed lookup command, no passwords/PTY/forwarding/rc/environment/SFTP. The
   per-key forced command binds the authority-generated task and generation.
5. The authority service must start before the SSH instance. A deployment's
   `orca-download-sshd.service` should use `Requires=`, `After=` and `BindsTo=` for
   `orca-download-authority.service`, and `KillMode=control-group`. Review restart
   and original personal login behavior before opening the approved interface.
   No installer here changes a firewall or the original sshd service.
6. Pin the independently verified receipt public key, host public key and epoch
   in root-owned `/Library/Application Support/OrcaToolkitState/endpoints.json`:

   ```json
   {
     "YOUR ORCA ENVIRONMENT": {
       "host": "100.64.0.20", "port": 22222,
       "hostKey": "ssh-ed25519 ADMINISTRATOR_VERIFIED_BASE64",
       "receiptKey": "ADMINISTRATOR_VERIFIED_ED25519_PUBLIC_PEM",
       "server_id": "administrator-assigned-server",
       "endpoint_id": "dedicated-download-ssh",
       "account_id": "orca-download", "signing_key_id": "receipt-key-1",
       "authority_epoch": "PRINTED_AND_VERIFIED_EPOCH",
       "worktree": "path:/srv/approved-project"
     }
   }
   ```

   Toolkit settings must match the environment/host/account/port. No trust key is
   learned from terminal output. Host/receipt key or epoch changes require manual
   verification, never trust-on-first-use from an unverified task response.

Registration travels through Orca's authenticated management terminal to the
fixed `/opt/orca-download/manage` program; it does not upload executable code.
The authority authenticates workspace kernel UID, export scope and an SSHSIG
Ed25519 proof of task-key possession. ACTIVE registration metadata is signed.
SQLite stores permanent unique task/key bindings and revocation tombstones.
Staging files are immutable 0400 objects behind the authority's 0700 directory;
readers receive bounded bytes over IPC, never workspace paths or write handles.

Revoke denies new authentication/reads first. Existing reads have durable leases;
the dispatcher ACKs only after flushing output. Linux also records the kernel
peer PID, process start time and boot ID: a confirmed exit, PID replacement or
host reboot can fence that process. Unknown liveness remains pending. It never
clears all leases just because the daemon restarted. Signed DRAINED receipts are
created only after the tombstone/request transaction commits. Bytes already in
network buffers cannot be recalled.

Use fixed management requests `inventory`, `status`, `revoke` for recovery with a
fresh challenge. An unknown task is not proof of cleanup. A lost registration
reply retries the original signed registration/request ID. A lost revoke reply
can be recovered through management after SSH authentication is revoked.

There is deliberately no automatic authority backup restore. Restoring an old
ACTIVE database under its old epoch is unsafe. Stop the entry point, retain the
old database/key/tombstones, initialize a new authority in a new protected state
location and manually pin its new epoch before registering new tasks. Do not
produce historical cleanup receipts from an old backup. To uninstall, stop SSH
and authority units and retain all state/keys until manual audit.

## Test commands and limits

```sh
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
python3 -m unittest discover -s plugins/toolkit/config-service -p 'test_*.py'
PYTHONPATH=plugins/toolkit/config-service:services/download-authorization python3 -m unittest discover -s services/download-authorization -p test_authority.py
docker build -t orca-security-test:7b3 -f services/download-authorization/Dockerfile.test .
docker run --rm --network none -v "$PWD:/src:ro" orca-security-test:7b3
sudo /usr/bin/python3 scripts/test-macos-security-isolation.py --client-uid "$(id -u)"
```

The Docker run creates real separate Linux UIDs and a real loopback sshd, with no
host port, network or host-system mutations. The macOS command creates only a
temporary root-owned directory and uses existing `_www` as a distinct service UID;
it removes temporary processes/files on exit. It installs no LaunchDaemon.
Neither test migrates real data or contacts a VPS. SIGKILL and SQLite allocation
failure tests are not electrical power-loss or storage-controller fault tests.

## Stage 7B-5 follow-up

The Toolkit-side asynchronous Host adapter, bounded main-process integration
component, versioned UI and structured maintenance commands are implemented.
Installed Orca 1.4.249 still lacks the trusted method and cannot enroll. The real
SDK probe confirms fail-closed behavior; positive native tests use a synthetic
signed Host. See [Host integration and required changes](macos-orca-host-integration.md).
This does not supersede the production release boundary above.
