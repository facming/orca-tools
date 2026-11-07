#!/usr/bin/env python3
"""Generate an isolated, new fixture directory and full SHA-256 manifest. No network.
Run as the dedicated workspace owner on the test VPS, or locally for a dry run.
Never accepts an existing output directory; never deletes files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
output = args.output
if not output.is_absolute() or output.exists() or output.is_symlink():
    parser.error('--output must be an absolute, new directory')
parent = output.parent.resolve(strict=True)
# macOS /tmp and /var are system aliases. Emit canonical fixture paths so the
# remote no-symlink policy can use the resulting project root unchanged.
output = parent / output.name
if shutil.disk_usage(parent).free < 3 * 1024**3:
    parser.error('need at least 3 GiB free')
os.umask(0o077)
output.mkdir(mode=0o700)
root = output / 'project'
root.mkdir(mode=0o700)
block = os.urandom(1024 * 1024)
manifest = []
for name, size in [('empty.bin', 0), ('16 B.bin', 16), ('32 MiB.bin', 32 * 1024**2),
                   ('1 GiB.bin', 1024**3), ('中文 文件.bin', 4096), ('.hidden.bin', 256),
                   ('readonly.bin', 1024)]:
    file = root / name
    digest = hashlib.sha256()
    with file.open('xb') as stream:
        remaining = size
        while remaining:
            data = block[:min(remaining, len(block))]
            stream.write(data)
            digest.update(data)
            remaining -= len(data)
    file.chmod(0o400 if name == 'readonly.bin' else 0o600)
    manifest.append({'path': name, 'bytes': size, 'sha256': digest.hexdigest()})
(root / 'empty-directory').mkdir(mode=0o700)
page = root / 'pagination'
page.mkdir(mode=0o700)
for index in range(205):
    (page / f'entry-{index:03d}').touch(mode=0o600, exist_ok=False)
(output / 'outside.txt').write_text('not an authorized project\n')
(root / 'outside-link').symlink_to(output / 'outside.txt')
(output / 'manifest.json').write_text(json.dumps({'schema': 1, 'kind': 'generated nonproduction fixture', 'files': manifest}, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'result': 'CREATED', 'project': str(root), 'manifest': str(output / 'manifest.json'), 'remoteAcceptance': 'NOT RUN'}, ensure_ascii=False))
