import base64
import concurrent.futures
import io
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import subprocess
from authority import Authority, DOMAIN, proof_payload
from cryptography.hazmat.primitives import serialization
from ipc import receive
from secure_store import loads, dumps

class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name).resolve()
        self.state=self.root/'state';self.state.mkdir(mode=0o700)
        self.source=self.root/'file';self.source.write_bytes(b'DATA'*1024)
        self.owner=os.getuid()
        self.policy={'server_id':'test','endpoint_id':'isolated','account_id':'reader','signing_key_id':'one',
                     'owners':{str(self.owner):[str(self.root)]},'reader_uid':self.owner+1,'lookup_uid':self.owner+2,
                     'dispatcher':'/opt/orca-download/dispatch'}
        self.store=Authority(self.state,self.policy,True)
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def registration(self):
        task=uuid.uuid4().hex; key=self.root/task
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
        r={'op':'register','protocol':3,'request_id':uuid.uuid4().hex,'task_id':task,'source':str(self.source),
           'public_key':' '.join(Path(str(key)+'.pub').read_text().split()[:2])}
        proof=subprocess.run(['ssh-keygen','-Y','sign','-f',str(key),'-n','orca-download-registration-v3'],
                             input=proof_payload(r),capture_output=True,check=True).stdout
        return {**r,'proof':base64.b64encode(proof).decode()}
    def revoke(self, task):
        return {**self.store.identity(self.store.task(task)),'op':'revoke','request_id':uuid.uuid4().hex,'client_nonce':os.urandom(32).hex()}
    def decode(self, envelope):
        raw=base64.b64decode(envelope['payload']);self.store.signer.public_key().verify(base64.b64decode(envelope['signature']),DOMAIN+raw)
        return loads(raw)
    def test_concurrent_register_revoke_persistent_tombstone(self):
        requests=[self.registration(),self.registration()]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results=list(pool.map(lambda r:self.store.handle(self.owner,r),requests))
        self.assertTrue(all(self.decode(r)['state']=='ACTIVE' for r in results))
        rs=[self.revoke(r['task_id']) for r in requests]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results=list(pool.map(lambda r:self.store.handle(self.owner,r),rs))
        self.assertTrue(all(self.decode(r)['read_barrier']=='DRAINED' for r in results))
        self.store.close();self.store=Authority(self.state,self.policy)
        for r in requests:
            lookup=self.store.handle(self.policy['lookup_uid'],{'op':'lookup','public_key':r['public_key']})
            self.assertEqual(lookup['authorized_key'],'')
            self.assertEqual(self.decode(self.store.handle(self.owner,self.revoke(r['task_id'])))['state'],'REVOKED')
            with self.assertRaises(Exception):self.store.handle(self.owner,{**r,'request_id':uuid.uuid4().hex})
    def test_registration_and_revocation_overlap(self):
        old=self.registration();self.store.handle(self.owner,old)
        new=self.registration();rev=self.revoke(old['task_id'])
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results=list(pool.map(lambda r:self.store.handle(self.owner,r),[new,rev]))
        self.assertEqual(self.decode(results[0])['state'],'ACTIVE')
        self.assertEqual(self.decode(results[1])['state'],'REVOKED')
        self.assertEqual(self.store.task(new['task_id'])['state'],'ACTIVE')
        self.assertEqual(self.store.task(old['task_id'])['state'],'REVOKED')

    def test_nonce_epoch_owner_and_proof_rejected(self):
        r=self.registration();bad={**r,'proof':base64.b64encode(b'fake').decode()}
        with self.assertRaises(PermissionError):self.store.handle(self.owner,bad)
        self.store.handle(self.owner,r);rev=self.revoke(r['task_id'])
        with self.assertRaises(PermissionError):self.store.handle(self.owner,{**rev,'authority_epoch':'old'})
        with self.assertRaises(PermissionError):self.store.handle(self.owner+99,rev)
        receipt=self.store.handle(self.owner,rev)
        self.assertEqual(receipt,self.store.handle(self.owner,rev))
        with self.assertRaises(Exception):self.store.handle(self.owner,{**rev,'request_id':uuid.uuid4().hex})
    def test_lost_reader_survives_restart_without_false_drained(self):
        r=self.registration();self.store.handle(self.owner,r);task=self.store.task(r['task_id'])
        outgoing=io.BytesIO()
        with self.assertRaises(ValueError):
            self.store.stream_read(self.policy['reader_uid'],{'op':'read-range','task_id':task['id'],'generation':task['generation'],'offset':0,'length':4},io.BytesIO(b''),outgoing)
        self.assertIn(b'DATA',outgoing.getvalue())
        self.store.close();self.store=Authority(self.state,self.policy)
        result=self.store.handle(self.owner,self.revoke(task['id']))
        self.assertEqual(result['state'],'REVOKING');self.assertNotIn('signature',result)
        with self.assertRaises(PermissionError):
            self.store.stream_read(self.policy['reader_uid'],{'task_id':task['id'],'generation':task['generation'],'offset':0,'length':4},io.BytesIO(),io.BytesIO())
    def test_source_symlink_hardlink_escape_and_disk_failure(self):
        for mode in ('symlink','hardlink','outside'):
            r=self.registration();link=self.root/('link-'+mode)
            if mode=='symlink':link.symlink_to(self.source)
            elif mode=='hardlink':os.link(self.source,link)
            else:link=Path('/etc/passwd')
            r['source']=str(link)
            # Valid proof binds this source, so rejection exercises export validation.
            key=self.root/r['task_id'];unsigned={k:v for k,v in r.items() if k!='proof'}
            proof=subprocess.run(['ssh-keygen','-Y','sign','-f',str(key),'-n','orca-download-registration-v3'],input=proof_payload(unsigned),capture_output=True,check=True).stdout
            r['proof']=base64.b64encode(proof).decode()
            with self.assertRaises((PermissionError,OSError)):self.store.handle(self.owner,r)
            if mode=='hardlink':link.unlink()
        r=self.registration();self.store.handle(self.owner,r)
        self.store.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(Exception):self.store.handle(self.owner,self.revoke(r['task_id']))
        self.assertEqual(self.store.task(r['task_id'])['state'],'ACTIVE')

if __name__=='__main__':unittest.main()
