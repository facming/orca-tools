"""Production caller enrollment; never accepts a generic interpreter requirement.

The signed opt-in is a publisher contract: only reviewed Toolkit code can issue
configuration requests. Exact release hashes prevent silently widening this
contract on upgrade. Electron additionally needs its executable injection fuses
closed. Existing RUN_AS_NODE plugin workers deliberately cannot enroll.
"""
import plistlib
import re
import subprocess
from pathlib import Path

FUSE = b'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX'
FORBIDDEN = ('com.apple.security.get-task-allow',
             'com.apple.security.cs.disable-library-validation',
             'com.apple.security.cs.allow-dyld-environment-variables',
             'com.apple.security.cs.allow-unsigned-executable-memory',
             'com.apple.security.cs.disable-executable-page-protection')


def check_fuses(data):
    start = data.find(FUSE)
    if start < 0:
        raise ValueError('Missing Electron fuse wire')
    # Universal frameworks contain one wire per architecture. A permissive
    # Rosetta slice must not inherit approval from the arm64 slice.
    while start >= 0:
        wire = data[start + len(FUSE):]
        if len(wire) < 8 or wire[0] != 1 or wire[1] < 6:
            raise ValueError('Unsupported Electron fuses')
        values = wire[2:2 + wire[1]]
        if len(values) != wire[1] or any(values[i] != ord('0') for i in (0, 2, 3)) or any(values[i] != ord('1') for i in (4, 5)):
            raise ValueError('Caller permits Node/script/debug injection or unsealed application code')
        start = data.find(FUSE, start + len(FUSE))


def enroll(app):
    app = Path(app).absolute()
    for path in (app, *app.parents):
        st = path.lstat()
        if path.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('Caller app and ancestors must be root-owned and protected')
        if len(subprocess.check_output(['/bin/ls', '-lde', str(path)]).splitlines()) != 1:
            raise ValueError('Caller ancestor ACL is not permitted')
    for path in app.rglob('*'):
        st = path.lstat()
        if st.st_uid != 0 or (not path.is_symlink() and st.st_mode & 0o022):
            raise ValueError('Caller resources must be root-owned and protected')
        if path.is_symlink() and not path.resolve(strict=True).is_relative_to(app):
            raise ValueError('Caller resource symlink escapes the signed app')
        if len(subprocess.check_output(['/bin/ls', '-lde', str(path)]).splitlines()) != 1:
            raise ValueError('Caller resource ACL is not permitted')
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') not in ('com.stablyai.orca', 'com.stablyai.orca.toolkit') or info.get('OrcaToolkitConfigClient') != 1:
        raise ValueError('A signed, explicitly opted-in Orca Toolkit caller app is required')
    executable = info['CFBundleExecutable']
    if not re.fullmatch(r'[A-Za-z0-9 _.-]+', executable):
        raise ValueError('Invalid caller executable')
    # Validate the whole resource seal and nested code, not only its Mach-O file.
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    result = subprocess.run(['codesign', '-d', '--verbose=4', '-r-', str(app)], capture_output=True, text=True, check=True)
    details = result.stdout + result.stderr
    requirement = next(line.split('designated => ', 1)[1] for line in details.splitlines() if 'designated => ' in line)
    if 'anchor apple generic' not in requirement or 'subject.OU' not in requirement or '(runtime)' not in details:
        raise ValueError('Caller requires Developer ID and hardened runtime')
    entitlements = subprocess.run(['codesign', '-d', '--entitlements', ':-', str(app)], capture_output=True, check=True).stdout
    entitlements = plistlib.loads(entitlements) if entitlements.strip() else {}
    if any(entitlements.get(key) for key in FORBIDDEN):
        raise ValueError('Caller has unsafe runtime entitlements')
    framework = app / 'Contents/Frameworks/Electron Framework.framework/Electron Framework'
    if framework.exists():
        check_fuses(framework.read_bytes())
    # Pin every architecture of this reviewed release (one host architecture may
    # run via Rosetta). No caller-supplied requirement, path or claimed identity.
    hashes = []
    for arch in ('arm64', 'x86_64'):
        result = subprocess.run(['codesign', '-d', '--verbose=4', '--arch', arch, str(app)], capture_output=True, text=True)
        match = re.search(r'^CDHash=([a-f0-9]{40})$', result.stderr, re.M)
        if result.returncode == 0 and match:
            hashes.append('cdhash H"' + match[1] + '"')
    if not hashes:
        raise ValueError('No supported signed caller architecture')
    return '(' + requirement + ') and (' + ' or '.join(hashes) + ')'
