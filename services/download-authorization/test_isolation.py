"""Real UID/sshd acceptance harness. ROOT, disposable Linux container ONLY.
Run via Dockerfile.test, read-only repository bind mount. No host accounts/SSH.
"""
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

assert os.getuid()==0 and Path('/.dockerenv').exists(), 'Disposable Docker container required'
ROOT=Path('/src')
sys.path[:0]=[str(ROOT/'plugins/toolkit/config-service'),str(ROOT/'services/download-authorization')]
from secure_store import dumps, loads
from ipc import call, send, receive
from authority import proof_payload

USERS={'authority':21001,'workspace':21002,'orca-download':21003,'orca-lookup':21004,'stranger':21005,'configuration':21006}
for name,uid in USERS.items():
    subprocess.run(['useradd','-u',str(uid),'-m','-s','/bin/sh',name],check=True)
# Unlock only the disposable reader; password authentication remains disabled.
subprocess.run(['usermod','-p','x','orca-download'],check=True)
code=Path('/opt/orca-download');code.mkdir()
for base in ('services/download-authorization','plugins/toolkit/config-service'):
    for name in ('authority.py','client.py','secure_store.py','ipc.py','config_store.py','task_records.py','recover.py'):
        source=ROOT/base/name
        if source.exists():shutil.copyfile(source,code/name)
shutil.copyfile(ROOT/'services/download-authorization/service.py',code/'service.py')
for mode in ('lookup','manage','dispatch'):
    (code/mode).write_text('#!/bin/sh\nexec /usr/local/bin/python3 -I /opt/orca-download/entry.py '+mode+' "$@"\n')
    (code/mode).chmod(0o755)
(code/'entry.py').write_text("import sys\nsys.path.insert(0,'/opt/orca-download')\nfrom client import main\nmain()\n")
state=Path('/var/lib/orca-authority');state.mkdir(mode=0o700);os.chown(state,21001,21001)
run=Path('/run/orca-download');run.mkdir();os.chown(run,21001,21001)
Path('/run/sshd').mkdir(exist_ok=True)
source=Path('/home/workspace/export');source.mkdir();os.chown(source,21002,21002)
file=source/'data';file.write_bytes(b'0123456789'*10000);os.chown(file,21002,21002)
policy={'state':str(state),'socket':str(run/'authority.sock'),'owners':{'21002':[str(source)]},'reader_uid':21003,'lookup_uid':21004,
        'server_id':'synthetic','endpoint_id':'loopback','account_id':'orca-download','signing_key_id':'test','dispatcher':str(code/'dispatch')}
policy_file=Path('/etc/orca-test-policy.json');policy_file.write_text(dumps(policy))

def as_uid(uid,args,**kw):
    return subprocess.run(args,user=uid,group=uid,extra_groups=[],capture_output=True,**kw)

def rpc(uid,r):
    result=as_uid(uid,[sys.executable,'-c',"import sys;sys.path.insert(0,'/opt/orca-download');from ipc import call;from secure_store import loads,dumps;print(dumps(call('/run/orca-download/authority.sock',loads(sys.stdin.buffer.read()))))"],input=dumps(r).encode())
    if result.returncode:raise RuntimeError(result.stderr.decode())
    return loads(result.stdout)

init=as_uid(21001,[sys.executable,str(code/'service.py'),'--policy',str(policy_file),'--initialize']);assert init.returncode==0,init.stderr
log=open('/tmp/authority.log','wb')

def start():
    proc=subprocess.Popen([sys.executable,str(code/'service.py'),'--policy',str(policy_file)],user=21001,group=21001,extra_groups=[],stdout=log,stderr=log)
    for _ in range(100):
        if (run/'authority.sock').exists():
            try:
                with socket.socket(socket.AF_UNIX) as s:s.connect(str(run/'authority.sock'))
                return proc
            except OSError:pass
        if proc.poll() is not None:raise AssertionError(Path('/tmp/authority.log').read_text())
        time.sleep(.05)
    raise AssertionError('Authority did not start')
proc=start()
try:
    # Real independent processes with wrong UID cannot mutate DB, journal, parent,
    # stage, receipt key or managed program. No mocked chmod/access results.
    for uid in (21002,21003,21004,21005):
        attack="""import os
from pathlib import Path
paths=['/var/lib/orca-authority/state.sqlite','/var/lib/orca-authority/state.sqlite-journal','/var/lib/orca-authority/receipt-key','/opt/orca-download/dispatch']
for p in paths:
 for op in ('write','unlink','rename','link','symlink'):
  try:
   if op=='write':open(p,'wb').write(b'BAD')
   elif op=='unlink':os.unlink(p)
   elif op=='rename':os.rename(p,p+'.moved')
   elif op=='link':os.link(p,p+'.hard')
   else:os.symlink('/etc/passwd',p+'.fake')
  except PermissionError:continue
  raise AssertionError((p,op,'not denied'))
"""
        result=as_uid(uid,[sys.executable,'-c',attack]);assert result.returncode==0,result.stderr
    try:rpc(21005,{'op':'status','task_id':'a'*32});raise AssertionError('Unauthorized peer accepted')
    except RuntimeError:pass
    print('PASS real UID direct DB/journal/program/signing-key isolation and unauthorized IPC',flush=True)
    host=Path('/etc/orca-test-host');subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(host)],check=True)
    sshconf=Path('/etc/orca-test-sshd.conf')
    text=(ROOT/'services/download-authorization/sshd_config.example').read_text().replace('/etc/orca-download/ssh_host_ed25519_key',str(host))
    sshconf.write_text(text)
    check=subprocess.run(['/usr/sbin/sshd','-t','-f',str(sshconf)],capture_output=True);assert check.returncode==0,check.stderr
    sshlog=open('/tmp/sshd.log','wb');sshd=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(sshconf)],stderr=sshlog)
    time.sleep(.3)
    def register():
        task=uuid.uuid4().hex;key=Path('/tmp')/task
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
        r={'op':'register','protocol':3,'request_id':uuid.uuid4().hex,'task_id':task,'source':str(file),'public_key':' '.join(Path(str(key)+'.pub').read_text().split()[:2])}
        signature=subprocess.run(['ssh-keygen','-Y','sign','-f',str(key),'-n','orca-download-registration-v3'],input=proof_payload(r),capture_output=True,check=True).stdout
        r['proof']=base64.b64encode(signature).decode();envelope=rpc(21002,r);meta=loads(base64.b64decode(envelope['payload']))
        return r,key,meta
    with concurrent.futures.ThreadPoolExecutor() as pool:jobs=list(pool.map(lambda _:register(),range(2)))
    known=Path('/tmp/known');known.write_text('[127.0.0.1]:22222 '+' '.join(Path(str(host)+'.pub').read_text().split()[:2])+'\n')
    def ssh(job,cmd,options=()):
        return subprocess.run(['ssh','-F','/dev/null','-p','22222','-i',str(job[1]),'-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),'-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','BatchMode=yes',*options,'orca-download@127.0.0.1',cmd],capture_output=True,timeout=10)
    for job in jobs:
        result=ssh(job,'read 0 10');assert result.returncode==0,(result.stderr,Path('/tmp/sshd.log').read_text());assert result.stdout==b'0123456789'
        for command in ('id','sh','read -1 4','read 0 999999999','sftp'):
            assert ssh(job,command).returncode!=0,command
    assert b'PTY allocation request failed' in ssh(jobs[0],'read 0 10',('-tt',)).stderr
    assert ssh(jobs[1],'sftp',('-s',)).returncode!=0
    assert ssh(jobs[1],'read 0 10',('-o','ExitOnForwardFailure=yes','-R','0:127.0.0.1:22')).returncode!=0
    # Personal authorization edits are independent of this entry point.
    personal=Path('/home/workspace/.ssh');personal.mkdir();os.chown(personal,21002,21002)
    auth=personal/'authorized_keys';auth.write_text('PERSONAL SYNTHETIC\n');os.chown(auth,21002,21002)
    assert ssh(jobs[0],'read 0 10').returncode==0
    assert auth.read_text()=='PERSONAL SYNTHETIC\n'
    original=known.read_text();known.write_text('[127.0.0.1]:22222 '+jobs[0][0]['public_key']+'\n')
    assert ssh(jobs[0],'read 0 10').returncode!=0
    known.write_text(original)
    print('PASS actual dedicated sshd: reads, forced command, shell/PTY/range rejection, host pin change, personal keys independent',flush=True)
    def revoke(job):
        meta=job[2];bindings={k:meta[k] for k in ('protocol','server_id','endpoint_id','account_id','authority_epoch','task_id','task_public_key_sha256','auth_generation','signing_key_id')}
        return rpc(21002,{**bindings,'op':'revoke','request_id':uuid.uuid4().hex,'client_nonce':os.urandom(32).hex()})
    held=register()
    reader_code="""import sys,socket,json
sys.path.insert(0,'/opt/orca-download')
from ipc import send,receive
s=socket.socket(socket.AF_UNIX);s.connect('/run/orca-download/authority.sock')
f=s.makefile('rwb',buffering=0)
send(f,{'op':'read-range','task_id':sys.argv[1],'generation':sys.argv[2],'offset':0,'length':10})
r=receive(f);assert r['ok'];data=f.read(10);assert data==b'0123456789'
print('HELD',flush=True);sys.stdin.readline()
send(f,{'ack':r['result']['lease']});assert receive(f)['ok']
"""
    reader=subprocess.Popen([sys.executable,'-c',reader_code,held[2]['task_id'],held[2]['auth_generation']],user=21003,group=21003,extra_groups=[],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert reader.stdout.readline().strip()==b'HELD'
    pending=revoke(held);assert pending['state']=='REVOKING' and 'signature' not in pending
    assert ssh(held,'read 0 10').returncode!=0
    reader.stdin.write(b'ACK\n');reader.stdin.flush();reader.wait(timeout=10);assert reader.returncode==0,reader.stderr.read()
    drained=revoke(held);assert loads(base64.b64decode(drained['payload']))['read_barrier']=='DRAINED'
    print('PASS real live read lease: revoke stays PENDING, new SSH denied, dispatcher ACK permits DRAINED',flush=True)
    with concurrent.futures.ThreadPoolExecutor() as pool:receipts=list(pool.map(revoke,jobs))
    for result in receipts:assert 'payload' in result, (result, rpc(21002,{'op':'status','task_id':result['task_id']}),Path('/tmp/authority.log').read_text());assert loads(base64.b64decode(result['payload']))['read_barrier']=='DRAINED'
    for job in jobs:assert ssh(job,'read 0 10').returncode!=0
    proc.kill();proc.wait();proc=start()
    for job in jobs:assert ssh(job,'read 0 10').returncode!=0
    print('PASS concurrent registration/revocation, durable tombstones and authority restart; revoked keys rejected',flush=True)
    # Isolated config writer under a SIXTH real UID, multiple client processes.
    cfg=Path('/var/lib/orca-configuration');cfg.mkdir(mode=0o700);os.chown(cfg,21006,21006)
    script="""import sys
sys.path.insert(0,'/opt/orca-download')
from config_store import ConfigStore,DEFAULTS
from ipc import Server,peer
s=ConfigStore('/var/lib/orca-configuration',True)
p=s.handle(21002,{'op':'preview','request_id':'p'*32,'sources':{k:None for k in DEFAULTS}})
s.handle(21002,{'op':'import','request_id':'i'*32,'preview_id':p['preview_id'],'hashes':p['hashes']})
Server('/run/orca-configuration/socket',s,lambda sock:peer(sock)[0]).serve_forever()
"""
    cdir=Path('/run/orca-configuration');cdir.mkdir();os.chown(cdir,21006,21006)
    cproc=subprocess.Popen([sys.executable,'-c',script],user=21006,group=21006,extra_groups=[])
    try:
        for _ in range(100):
            if (cdir/'socket').exists():break
            time.sleep(.05)
        def cfgcall(r):
            code="import sys;sys.path.insert(0,'/opt/orca-download');from ipc import call;from secure_store import dumps,loads;print(dumps(call('/run/orca-configuration/socket',loads(sys.stdin.buffer.read()))))"
            result=as_uid(21002,[sys.executable,'-c',code],input=dumps(r).encode());assert result.returncode==0,result.stderr;return loads(result.stdout)
        base=cfgcall({'op':'read','namespace':'settings'})
        def patch(item):
            return cfgcall({'op':'patch','namespace':'settings','request_id':uuid.uuid4().hex,**{k:base[k] for k in ('revision','epoch','store_id')},'patch':[{'op':'set','path':[item,'enabled'],'value':False}]})
        with concurrent.futures.ThreadPoolExecutor() as pool:list(pool.map(patch,['gpu','accounts']))
        current=loads(cfgcall({'op':'read','namespace':'settings'})['raw_json']);assert not current['gpu']['enabled'] and not current['accounts']['enabled']
        for suffix in ('','-journal','-wal','-shm'):
            result=as_uid(21002,[sys.executable,'-c',"open('/var/lib/orca-configuration/state.sqlite"+suffix+"','wb').write(b'BAD')"])
            assert result.returncode!=0 and b'PermissionError' in result.stderr
        backup=cfgcall({'op':'backup'})
        print('PASS config actual different UID: DB/journal/WAL/SHM denied; independent process field patches merge',flush=True)
    finally:cproc.kill();cproc.wait()
    (cfg/'state.sqlite').write_bytes(b'SYNTHETIC CORRUPTION')
    result=as_uid(21006,[sys.executable,str(code/'recover.py'),'--state',str(cfg),'--backup-id',backup['backup_id'],'--output',str(cfg/'recovered')])
    assert result.returncode==0,result.stderr
    assert (cfg/'state.sqlite').read_bytes()==b'SYNTHETIC CORRUPTION'
    result=as_uid(21006,[sys.executable,'-c',"import sys;sys.path.insert(0,'/opt/orca-download');from config_store import ConfigStore;s=ConfigStore('/var/lib/orca-configuration/recovered');assert s.meta('epoch') != sys.argv[1];assert s.read('settings')['revision']=='3';s.close()",base['epoch']])
    assert result.returncode==0,result.stderr
    print('PASS offline corruption recovery into new epoch/directory; damaged original retained',flush=True)
finally:
    proc.kill();proc.wait()
    if 'sshd' in locals():sshd.terminate();sshd.wait()
