#!/usr/bin/env python3
"""Explicit administrator-run temporary test, no launchd/production installation.
Uses the existing non-login _www identity only as a temporary service process.
No credentials, production config, home directories or SSH are read or modified.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--client-uid',required=True,type=int)
    a=p.parse_args()
    if sys.platform!='darwin' or os.geteuid()!=0 or a.client_uid==0:
        raise SystemExit('Run explicitly with sudo on macOS and a non-root --client-uid')
    svc=pwd.getpwnam('_www')
    if svc.pw_uid==a.client_uid:raise SystemExit('Distinct UIDs required')
    repo=Path(__file__).resolve().parents[1]
    root=Path(tempfile.mkdtemp(prefix='orca-security-test-',dir='/private/var/db'));root.chmod(0o755)
    proc=None
    try:
        code=root/'code';code.mkdir(mode=0o755)
        for name in ('secure_store.py','config_store.py','task_records.py','ipc.py','service.py','peer_identity.c','bridge.c'):
            shutil.copyfile(repo/'plugins/toolkit/config-service'/name,code/name)
        state=root/'state';state.mkdir(mode=0o700);os.chown(state,svc.pw_uid,svc.pw_gid)
        run=root/'run';run.mkdir(mode=0o755);os.chown(run,svc.pw_uid,svc.pw_gid)
        subprocess.run(['/usr/bin/cc','-dynamiclib',str(code/'peer_identity.c'),'-framework','Security','-framework','CoreFoundation','-o',str(code/'peer_identity.dylib')],check=True)
        bridge=code/'config-bridge'
        subprocess.run(['/usr/bin/cc','-DORCA_SERVICE_ACCOUNT="_www"','-DORCA_SOCKET_DIR="'+str(run)+'"',str(code/'bridge.c'),'-o',str(bridge)],check=True)
        subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(bridge)],check=True)
        signature=subprocess.run(['/usr/bin/codesign','-d','--verbose=4',str(bridge)],capture_output=True,check=True).stderr.decode()
        requirement='cdhash H\"'+re.search(r'^CDHash=([a-f0-9]+)$',signature,re.M)[1]+'\"'
        subprocess.run(['/usr/bin/codesign','--verify','--strict','-R','='+requirement,str(bridge)],check=True)
        caller=code/'orca-caller'
        subprocess.run(['/usr/bin/cc','-DBRIDGE="'+str(bridge)+'"',str(repo/'plugins/toolkit/config-service/tests/caller.c'),'-o',str(caller)],check=True)
        subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(caller)],check=True)
        signature=subprocess.run(['/usr/bin/codesign','-d','--verbose=4',str(caller)],capture_output=True,check=True).stderr.decode()
        caller_requirement='cdhash H"'+re.search(r'^CDHash=([a-f0-9]+)$',signature,re.M)[1]+'"'
        args=['/usr/bin/python3',str(code/'service.py'),'--state',str(state),'--socket',str(run/(str(a.client_uid)+'.sock')),'--tenant',str(a.client_uid),'--requirement',requirement,'--caller-requirement',caller_requirement]
        result=subprocess.run(args+['--initialize'],user=svc.pw_uid,group=svc.pw_gid,extra_groups=[],capture_output=True)
        assert result.returncode==0,result.stderr
        log=open(root/'test.log','wb')
        proc=subprocess.Popen(args,user=svc.pw_uid,group=svc.pw_gid,extra_groups=[],stdout=log,stderr=log)
        endpoint=run/(str(a.client_uid)+'.sock')
        for _ in range(100):
            if endpoint.exists():break
            if proc.poll() is not None:raise AssertionError((root/'test.log').read_text())
            time.sleep(.05)
        def request(r):
            result=subprocess.run([str(caller)],input=(json.dumps(r)+'\n').encode(),capture_output=True,user=a.client_uid,group=pwd.getpwuid(a.client_uid).pw_gid,extra_groups=[])
            assert result.returncode==0,(result.returncode,result.stderr,(root/'test.log').read_text())
            reply=json.loads(result.stdout);assert reply['ok'],reply;return reply['result']
        denied=subprocess.run([str(bridge)],input=b'{"op":"status"}\n',capture_output=True,user=a.client_uid,group=pwd.getpwuid(a.client_uid).pw_gid,extra_groups=[])
        assert denied.returncode==0 and not json.loads(denied.stdout)['ok'],denied
        status=request({'op':'status'});assert status['state']=='migration-pending'
        preview=request({'op':'preview','request_id':uuid.uuid4().hex,'sources':{'settings':None,'downloads-preferences':None}})
        request({'op':'import','request_id':uuid.uuid4().hex,'preview_id':preview['preview_id'],'hashes':preview['hashes']})
        assert request({'op':'read','namespace':'settings'})['revision']=='1'
        attack="""import os,sys
root=sys.argv[1]
for suffix in ('','-journal','-wal','-shm'):
 try:open(root+'/state.sqlite'+suffix,'wb').write(b'BAD')
 except PermissionError:continue
 raise AssertionError('cross-UID write permitted')
for op in (lambda:os.unlink(root+'/state.sqlite'),lambda:os.rename(root,root+'-moved'),lambda:os.symlink('/etc/passwd',root+'/evil'),lambda:os.link(root+'/state.sqlite',root+'/copy')):
 try:op()
 except PermissionError:continue
 raise AssertionError('cross-UID namespace mutation permitted')
"""
        result=subprocess.run(['/usr/bin/python3','-c',attack,str(state)],user=a.client_uid,group=pwd.getpwuid(a.client_uid).pw_gid,extra_groups=[],capture_output=True)
        assert result.returncode==0,result.stderr
        # Same client UID, unsigned/unapproved executable: rejected by SecCodeCheckValidity.
        spoof="import socket,sys; s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall(b'{\"op\":\"status\"}\\n');print(s.recv(4096).decode())"
        result=subprocess.run(['/usr/bin/python3','-c',spoof,str(endpoint)],user=a.client_uid,group=pwd.getpwuid(a.client_uid).pw_gid,extra_groups=[],capture_output=True)
        assert b'Unauthorized signed IPC client' in result.stdout,result
        print('PASS macOS native peer UID + designated code requirement; approved original caller accepted; direct signed bridge and unsigned client rejected')
        print('PASS macOS real different UID: DB/journal/WAL/SHM write, unlink, rename, hardlink, symlink denied')
        print('PASS explicit migration and service read; no production installation or user data accessed')
    finally:
        if proc is not None:
            proc.terminate();proc.wait(timeout=10)
        shutil.rmtree(root)

if __name__=='__main__':main()
