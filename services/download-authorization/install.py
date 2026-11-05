#!/usr/bin/env python3
"""Explicit root-run Linux bundle/service generator; does NOT start sshd or edit
sshd_config, firewall, personal authorization, source data, or existing accounts.
Requires administrator-created distinct accounts and approved policy file.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess

p=argparse.ArgumentParser()
p.add_argument('--policy',required=True)
p.add_argument('--authority-user',required=True)
p.add_argument('--python',required=True,help='Root-owned Python environment with cryptography installed')
a=p.parse_args()
if os.geteuid()!=0:raise SystemExit('Administrator installation only')
account=pwd.getpwnam(a.authority_user);policy=json.loads(Path(a.policy).read_text())
if policy['socket']!='/run/orca-download/authority.sock' or policy['dispatcher']!='/opt/orca-download/dispatch':raise SystemExit('Use the fixed v3 socket and dispatcher')
if account.pw_uid in [0,policy['reader_uid'],policy['lookup_uid'],*map(int,policy['owners'])]:raise SystemExit('Independent non-root authority required')
python=Path(a.python).resolve(strict=True)
for f in (python,*python.parents):
    s=f.stat()
    if s.st_uid!=0 or s.st_mode & 0o022:raise SystemExit('Unprotected Python runtime')
root=Path(__file__).resolve().parents[2];dest=Path('/opt/orca-download');dest.mkdir(mode=0o755)
for name in ('authority.py','service.py','client.py'):
    shutil.copyfile(Path(__file__).with_name(name),dest/name)
for name in ('secure_store.py','ipc.py'):
    shutil.copyfile(root/'plugins/toolkit/config-service'/name,dest/name)
(dest/'entry.py').write_text("import sys\nsys.path.insert(0,'/opt/orca-download')\nfrom client import main\nmain()\n")
for mode in ('lookup','manage','dispatch'):
    file=dest/mode;file.write_text('#!/bin/sh\nexec '+str(python)+' -I /opt/orca-download/entry.py '+mode+' "$@"\n');file.chmod(0o755)
state=Path(policy['state']);state.mkdir(mode=0o700);os.chown(state,account.pw_uid,account.pw_gid)
socket_parent=Path(policy['socket']).parent;socket_parent.mkdir(mode=0o755,exist_ok=True);os.chown(socket_parent,account.pw_uid,account.pw_gid)
config=Path('/etc/orca-download-policy.json')
with config.open('x') as out:json.dump(policy,out)
config.chmod(0o644)
subprocess.run([str(python),'-I',str(dest/'service.py'),'--policy',str(config),'--initialize'],user=account.pw_uid,group=account.pw_gid,extra_groups=[],check=True)
unit=Path('/etc/systemd/system/orca-download-authority.service')
with unit.open('x') as out:
    out.write('[Unit]\nDescription=Orca download authorization v3\nBefore=orca-download-sshd.service\n\n[Service]\nType=simple\nUser='+a.authority_user+'\nUMask=0077\nExecStart='+str(python)+' -I /opt/orca-download/service.py --policy '+str(config)+'\nRuntimeDirectory=orca-download\nRuntimeDirectoryMode=0755\nRestart=on-failure\nKillMode=control-group\nNoNewPrivileges=true\nProtectSystem=strict\nReadWritePaths='+str(state)+' '+str(socket_parent)+'\n\n[Install]\nWantedBy=multi-user.target\n')
print('Bundle and unit prepared. Administrator must inspect permissions, pin keys/epoch, restrict all other SSH entries, and start services explicitly.')
