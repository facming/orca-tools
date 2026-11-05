#!/usr/bin/env python3
"""Build an inspectable macOS service bundle; never installs or migrates."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

p=argparse.ArgumentParser()
p.add_argument('--output',required=True)
p.add_argument('--signing-identity',required=True,help='Developer ID Application identity (production)')
a=p.parse_args()
root=Path(__file__).resolve().parents[1];out=Path(a.output)
out.mkdir(mode=0o755)  # no overwrite
source=root/'plugins/toolkit/config-service'
for name in ('service.py','config_store.py','secure_store.py','ipc.py','recover.py'):
    shutil.copyfile(source/name,out/name)
subprocess.run(['cc','-Wall','-Wextra',str(source/'bridge.c'),'-o',str(out/'config-bridge')],check=True)
subprocess.run(['cc','-dynamiclib',str(source/'peer_identity.c'),'-framework','Security','-framework','CoreFoundation','-o',str(out/'peer_identity.dylib')],check=True)
for name in ('config-bridge','peer_identity.dylib'):
    subprocess.run(['codesign','--force','--options','runtime',*(['--timestamp'] if a.signing_identity!='-' else []),'--sign',a.signing_identity,str(out/name)],check=True)
result=subprocess.run(['codesign','-dr','-',str(out/'config-bridge')],capture_output=True,text=True,check=True)
requirement=next(line.split('designated => ',1)[1].strip() for line in (result.stdout+result.stderr).splitlines() if 'designated => ' in line)
subprocess.run(['codesign','--verify','--strict','-R','='+requirement,str(out/'config-bridge')],check=True)
(out/'requirement.txt').write_text(requirement+'\n')
(out/'manifest.json').write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in out.iterdir()},indent=2)+'\n')
print('Built bundle:',out)
print('Designated requirement:',requirement)

if a.signing_identity=='-':print('TEST ONLY: ad-hoc bundle is rejected by the production installer.')
