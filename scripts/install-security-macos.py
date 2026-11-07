#!/usr/bin/env python3
"""Administrator-invoked production install/unload; never runs from Toolkit.
No implicit migration, no JSON overwrite, no removal of authoritative state.
"""
import argparse
import hashlib
import grp
import json
import os
from pathlib import Path
import plistlib
import pwd
import shutil
import subprocess
import sys

p=argparse.ArgumentParser()
p.add_argument('action',choices=['install','unload'])
p.add_argument('--tenant',required=True,type=int)
p.add_argument('--bundle')
p.add_argument('--caller-app', help='Root-protected signed Orca Toolkit caller app; not a Node/Electron CLI')
p.add_argument('--service-uid',type=int)
p.add_argument('--python',default='/usr/bin/python3')
a=p.parse_args()
if sys.platform!='darwin' or os.geteuid()!=0:raise SystemExit('Administrator-run macOS installer required')
label='com.orca.toolkit.config.'+str(a.tenant)
plist=Path('/Library/LaunchDaemons')/(label+'.plist')
if a.action=='unload':
    subprocess.run(['launchctl','disable','system/'+label],check=True)
    subprocess.run(['launchctl','bootout','system/'+label],check=True)
    print('Service unloaded. Program, account, database, backups and migration sources retained for manual audit.')
    raise SystemExit(0)
if not a.caller_app:raise SystemExit('--caller-app is required; Bridge execution is not caller authorization')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/toolkit/config-service'))
from caller_policy import enroll
caller_requirement=enroll(a.caller_app)
if not a.bundle or not a.service_uid or a.service_uid in (0,a.tenant):raise SystemExit('Distinct --service-uid and --bundle required')
pwd.getpwuid(a.tenant)
# Neither interpreter nor its ancestors may be replaceable by the desktop UID.
python=Path(a.python).resolve(strict=True)
for file in (python,*python.parents):
    st=file.stat()
    if st.st_uid!=0 or st.st_mode & 0o022:raise SystemExit('Python runtime must be root-owned and protected: '+str(file))
source=Path(a.bundle).resolve(strict=True)
manifest=json.loads((source/'manifest.json').read_text())
expected={'service.py','config_store.py','task_records.py','secure_store.py','ipc.py','recover.py','config-bridge','peer_identity.dylib','requirement.txt'}
if set(manifest)!=expected:raise SystemExit('Unexpected bundle manifest')
for name,sha in manifest.items():
    f=source/name
    if f.is_symlink() or hashlib.sha256(f.read_bytes()).hexdigest()!=sha:raise SystemExit('Bundle integrity mismatch')
requirement=(source/'requirement.txt').read_text().strip()
if 'anchor apple generic' not in requirement or 'subject.OU' not in requirement:
    raise SystemExit('Production requires a Developer ID requirement; ad-hoc test signatures are not accepted')
subprocess.run(['codesign','--verify','--strict','-R','='+requirement,str(source/'config-bridge')],check=True)
subprocess.run(['codesign','--verify','--strict',str(source/'peer_identity.dylib')],check=True)
try:
    account=pwd.getpwnam('_orcatoolkit')
    if account.pw_uid!=a.service_uid:raise SystemExit('Existing service identity differs')
except KeyError:
    try:pwd.getpwuid(a.service_uid)
    except KeyError:pass
    else:raise SystemExit('Requested service UID is in use')
    for field,value in [('UniqueID',str(a.service_uid)),('PrimaryGroupID','-2'),('NFSHomeDirectory','/var/empty'),('UserShell','/usr/bin/false'),('IsHidden','1'),('Password','*')]:
        subprocess.run(['dscl','.','-create','/Users/_orcatoolkit',field,value],check=True)
dest=Path('/Library/PrivilegedHelperTools/OrcaToolkit')
if dest.exists() or plist.exists():raise SystemExit('Existing installation retained; audit before upgrading')
dest.mkdir(mode=0o755)
for name in expected:
    shutil.copyfile(source/name,dest/name);os.chmod(dest/name,0o755 if name=='config-bridge' else 0o644)
state_top=Path('/Library/Application Support/OrcaToolkitState');state_top.mkdir(mode=0o755,exist_ok=True)
if state_top.stat().st_uid!=0 or state_top.stat().st_mode & 0o022:raise SystemExit('Unsafe state parent')
state=state_top/str(a.tenant);state.mkdir(mode=0o700);os.chown(state,a.service_uid,-1)
run=state_top/'run';run.mkdir(mode=0o755,exist_ok=True);os.chown(run,a.service_uid,-1)
args=[str(python),'-I',str(dest/'entry.py'),'--state',str(state),'--socket',str(run/(str(a.tenant)+'.sock')),'--tenant',str(a.tenant),'--requirement',requirement,'--caller-requirement',caller_requirement]
# -I drops source directory, user site-packages and environment injection.
(dest/'entry.py').write_text("import runpy,sys\nsys.path.insert(0,"+repr(str(dest))+")\nrunpy.run_path("+repr(str(dest/'service.py'))+",run_name='__main__')\n")
subprocess.run(args+['--initialize'],user=a.service_uid,group=grp.getgrnam('nobody').gr_gid,extra_groups=[],check=True)
config={'Label':label,'ProgramArguments':args,'UserName':'_orcatoolkit','RunAtLoad':True,'KeepAlive':True,'Umask':63,
        'EnvironmentVariables':{'PATH':'/usr/bin:/bin'},'WorkingDirectory':str(dest),
        'StandardErrorPath':str(state/'service-error.log'),'StandardOutPath':str(state/'service.log')}
with plist.open('xb') as out:plistlib.dump(config,out)
os.chmod(plist,0o644)
subprocess.run(['launchctl','enable','system/'+label],check=True)
subprocess.run(['launchctl','bootstrap','system',str(plist)],check=True)
print('Installed isolated service. Migration still pending; explicitly preview and confirm both sources.')
