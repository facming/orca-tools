"""Read-only final-package gate. Never signs, installs, enrolls or submits an app."""
import argparse
import hashlib
import json
import plistlib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/toolkit/config-service'))
from caller_policy import FORBIDDEN, check_fuses

DEVELOPER_ID = '=anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists'


def run(*args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def check(app, bundle_id, team, manifest_hash):
    app = app.resolve(strict=True)
    if bundle_id.startswith('com.stablyai.'):
        raise ValueError('Use the fork publisher identity')
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info['CFBundleIdentifier'] != bundle_id:
        raise ValueError('Unexpected app identifier')
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', '-R', DEVELOPER_ID, str(app))
    resources = app / 'Contents/Resources'
    root = resources / 'toolkit-host'
    manifest_bytes = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_hash:
        raise ValueError('Manifest differs from reviewed build')
    manifest = json.loads(manifest_bytes)
    if manifest['version'] != 1:
        raise ValueError('Unknown seal format')
    files = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if files != set(manifest['files']) | {'manifest.json'} or any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Open or linked Toolkit dependency tree')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Toolkit hash mismatch: ' + name)
    check_fuses((app / 'Contents/Frameworks/Electron Framework.framework/Electron Framework').read_bytes())
    if not (resources / 'app.asar').is_file() or not info.get('ElectronAsarIntegrity'):
        raise ValueError('ASAR integrity metadata required')
    binaries = [app, root / 'worker-node', root / 'socketpair.node']
    evidence = []
    for binary in binaries:
        executable = app / 'Contents/MacOS' / info['CFBundleExecutable'] if binary == app else binary
        arches = run('/usr/bin/lipo', '-archs', str(executable)).decode().split()
        if not arches or any(arch not in ('arm64', 'x86_64') for arch in arches):
            raise ValueError('Unsupported architecture')
        for arch in arches:
            run('/usr/bin/codesign', '--verify', '--strict', '-R', DEVELOPER_ID, str(binary))
            result = subprocess.run(['/usr/bin/codesign', '-d', '--verbose=4', '--arch', arch, str(binary)], check=True, capture_output=True)
            details = result.stderr.decode()
            if 'TeamIdentifier=' + team + '\n' not in details or '(runtime)' not in details or 'Timestamp=' not in details:
                raise ValueError('Expected team, hardened runtime and secure timestamp required')
            raw = run('/usr/bin/codesign', '-d', '--arch', arch, '--entitlements', ':-', str(binary))
            entitlements = plistlib.loads(raw) if raw.strip() else {}
            if any(entitlements.get(key) for key in FORBIDDEN):
                raise ValueError('Unsafe entitlements')
            if binary.name == 'worker-node' and entitlements != {'com.apple.security.cs.allow-jit': True}:
                raise ValueError('Unexpected worker entitlements')
            evidence.append({'binary': str(binary.relative_to(app)) if binary != app else '.', 'arch': arch,
                             'cdhash': re.search(r'^CDHash=([a-f0-9]{40})$', details, re.M)[1], 'entitlements': entitlements})
    run('/usr/bin/xcrun', 'stapler', 'validate', str(app))
    run('/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=4', str(app))
    return {'signature_and_notarization': 'passed', 'manifest_sha256': manifest_hash, 'binaries': evidence,
            'release': 'BLOCKED until production download, recovery and cross-UID acceptance evidence is approved'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('app', type=Path)
    parser.add_argument('--bundle-id', required=True)
    parser.add_argument('--team-id', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.app, args.bundle_id, args.team_id, args.manifest_sha256), indent=2))
    except Exception as error:
        print('RELEASE BLOCKED: ' + str(error), file=sys.stderr)
        sys.exit(1)
