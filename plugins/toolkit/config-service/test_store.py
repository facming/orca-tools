import base64
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from config_store import ConfigStore, DEFAULTS
from secure_store import dumps, loads


def request(op, **kw):
    return {'op': op, 'request_id': uuid.uuid4().hex, **kw}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ConfigStore(self.root, True)
        self.import_defaults()

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def import_defaults(self):
        preview = self.store.handle(501, request('preview', sources={k: None for k in DEFAULTS}))
        self.store.handle(501, request('import', preview_id=preview['preview_id'], hashes=preview['hashes']))

    def patch(self, base, path, value):
        return request('patch', namespace='settings', **{k: base[k] for k in ('store_id','epoch','revision')},
                       patch=[{'op':'set','path':path,'value':value}])

    def test_merge_conflict_aba_and_atomic_idempotency(self):
        base = self.store.read('settings')
        a = self.patch(base, ['gpu','enabled'], False)
        first = self.store.handle(501, a)
        self.assertEqual(first, self.store.handle(501, a))
        with self.assertRaisesRegex(ValueError, 'request_id conflict'):
            self.store.handle(501, {**a, 'patch': []})
        second = self.store.handle(501, self.patch(base, ['accounts','environment'], 'Other'))
        self.assertFalse(loads(second['raw_json'])['gpu']['enabled'])
        self.store.handle(501, self.patch(second, ['gpu','enabled'], True))
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.store.handle(501, self.patch(base, ['gpu','enabled'], False))
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.store.handle(501, self.patch(base, ['accounts'], {}))

    def test_cross_document_transaction_rolls_back_on_conflict(self):
        settings=self.store.read('settings');prefs=self.store.read('downloads-preferences')
        self.store.handle(501,self.patch(settings,['gpu','enabled'],False))
        doc={'namespace':'downloads-preferences',**{k:prefs[k] for k in ('store_id','epoch','revision')},
             'patch':[{'op':'set','path':['folder'],'value':'/new'}]}
        stale=self.patch(settings,['gpu','enabled'],True)
        with self.assertRaisesRegex(ValueError,'conflict'):
            self.store.handle(501,request('patch-many',documents=[doc,stale]))
        self.assertEqual(self.store.read('downloads-preferences')['revision'],'1')
        current=self.store.read('settings')
        result=self.store.handle(501,request('patch-many',documents=[doc,self.patch(current,['gpu','enabled'],True)]))
        self.assertEqual(len(result['documents']),2)
        self.assertEqual(loads(self.store.read('downloads-preferences')['raw_json'])['folder'],'/new')

    def test_exact_unknown_numbers_and_confirmed_bytes(self):
        self.store.close(); self.tmp.cleanup(); self.tmp = tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.store=ConfigStore(self.root, True)
        raw=b'\xef\xbb\xbf{"future":{"big":9007199254740993123456789,"fraction":0.123456789012345678901},"gpu":{"enabled":true}}'
        r=request('preview', sources={'settings':base64.b64encode(raw).decode(),'downloads-preferences':None})
        preview=self.store.handle(501,r)
        self.store.close(); self.store=ConfigStore(self.root)
        confirm=request('import',preview_id=preview['preview_id'],hashes=preview['hashes'])
        self.store.handle(501,confirm)
        self.assertEqual(self.store.handle(501,confirm)['state'],'ready')
        base=self.store.read('settings');result=self.store.handle(501,self.patch(base,['gpu','enabled'],False))
        self.assertIn('9007199254740993123456789',result['raw_json'])
        self.assertIn('0.123456789012345678901',result['raw_json'])
        self.assertEqual(base64.b64decode(loads(self.store.db.execute('SELECT raw FROM sources').fetchone()[0])['settings']),raw)
        for raw in [b'{"x":1,"x":2}', b'{broken', b'null', b'{"gpu":{"enabled":3}}']:
            with self.assertRaises(ValueError):
                self.store.handle(501,request('preview',sources={'settings':base64.b64encode(raw).decode(),'downloads-preferences':None}))

    def test_backup_restore_epoch_and_target_lease(self):
        backup=self.store.backup();base=self.store.read('settings')
        target={k:DEFAULTS['settings']['downloads'][k] for k in ('environment','sshHost','sshUser','sshPort')}
        self.store.handle(501,request('lease-acquire',task_id='a'*32,target=target))
        with self.assertRaisesRegex(ValueError,'lease'):
            self.store.handle(501,self.patch(base,['downloads','environment'],'new'))
        self.store.close();self.store=ConfigStore(self.root)
        with self.assertRaisesRegex(ValueError,'lease'):
            self.store.handle(501,request('restore',**backup))
        self.store.handle(501,request('lease-release',task_id='a'*32))
        self.store.handle(501,self.patch(base,['gpu','enabled'],False))
        self.store.handle(501,request('restore',**backup))
        self.assertTrue(loads(self.store.read('settings')['raw_json'])['gpu']['enabled'])
        with self.assertRaisesRegex(ValueError,'epoch'):
            self.store.handle(501,self.patch(base,['gpu','enabled'],False))

    def test_host_task_ownership_restart_transitions_and_restore(self):
        base = self.store.read('settings')
        backup = self.store.backup()
        target = {k: DEFAULTS['settings']['downloads'][k] for k in ('environment','sshHost','sshUser','sshPort')}
        identity = dict(task_id='b'*32, owner='c'*32, epoch=base['epoch'], store_id=base['store_id'])
        create = request('task-create', **identity, target=target)
        task = self.store.handle(501, create)
        self.assertEqual(task['state'], 'created')
        self.assertEqual(self.store.handle(501, create), task)
        self.store.close(); self.store = ConfigStore(self.root)
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.store.handle(501, request('lease-release', task_id='b'*32))
        with self.assertRaisesRegex(ValueError, 'owner'):
            self.store.handle(501, request('task-update', **{**identity, 'owner':'d'*32}, revision='1', state='running', released=False))
        with self.assertRaisesRegex(ValueError, 'cleanup'):
            self.store.handle(501, request('task-update', **identity, revision='1', state='running', released=True))
        for revision, state in [('1','running'), ('2','paused'), ('3','running'), ('4','complete')]:
            self.store.handle(501, request('task-update', **identity, revision=revision, state=state, released=False))
            with self.assertRaisesRegex(ValueError, 'lease'):
                self.store.handle(501, request('restore', **backup))
            with self.assertRaisesRegex(ValueError, 'lease'):
                self.store.handle(501, self.patch(base, ['downloads','sshHost'], 'other'))
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.store.handle(501, request('task-update', **identity, revision='4', state='cancelled', released=True))
        self.store.handle(501, request('task-update', **identity, revision='5', state='complete', released=True))
        restored = self.store.handle(501, request('restore', **backup, epoch=base['epoch'], store_id=base['store_id']))
        self.assertNotEqual(restored['epoch'], base['epoch'])
        with self.assertRaisesRegex(ValueError, 'epoch'):
            self.store.handle(501, request('task-create', **{**identity, 'task_id':'e'*32}, target=target))
        with self.assertRaisesRegex(ValueError, 'epoch'):
            self.store.handle(501, request('restore', **backup, epoch=base['epoch'], store_id=base['store_id']))

    def test_claim_fences_old_owner_and_concurrent_claims_without_releasing(self):
        from concurrent.futures import ThreadPoolExecutor
        base = self.store.read('settings')
        identity = dict(task_id='a'*32, owner='b'*32, epoch=base['epoch'], store_id=base['store_id'])
        target = {k: DEFAULTS['settings']['downloads'][k] for k in ('environment','sshHost','sshUser','sshPort')}
        self.store.handle(501, request('task-create', **identity, target=target))
        self.store.handle(501, request('task-update', **identity, revision='1', state='running', released=False))
        self.store.close(); self.store = ConfigStore(self.root)
        def claim(owner):
            try:
                return self.store.handle(501, request('task-claim', **{**identity, 'owner':owner}, previous_owner='b'*32, revision='2'))
            except ValueError:
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(claim, ['c'*32, 'd'*32]))
        winner = [r for r in results if r]
        self.assertEqual(len(winner), 1)
        self.assertEqual(winner[0]['state'], 'paused')
        self.assertFalse(winner[0]['released'])
        self.assertEqual(len(self.store.status()['target_leases']), 1)
        with self.assertRaisesRegex(ValueError, 'owner'):
            self.store.handle(501, request('task-update', **identity, revision='3', state='cancelled', released=True))

    def test_legacy_adoption_requires_existing_lease_and_keeps_inventory_barrier(self):
        base = self.store.read('settings')
        target = {k: DEFAULTS['settings']['downloads'][k] for k in ('environment','sshHost','sshUser','sshPort')}
        adopt = request('task-adopt', task_id='a'*32, owner='b'*32, epoch=base['epoch'], store_id=base['store_id'], target=target)
        with self.assertRaisesRegex(ValueError, 'Legacy lease'):
            self.store.handle(501, adopt)
        self.store.handle(501, request('lease-acquire', task_id='a'*32, target=target))
        self.store.db.execute("UPDATE meta SET value='legacy-review-required' WHERE key='task_inventory'")
        result = self.store.handle(501, adopt)
        self.assertEqual(result['state'], 'paused')
        self.assertFalse(result['released'])
        self.assertEqual(self.store.handle(501, adopt), result)
        self.assertEqual(self.store.status()['task_inventory']['state'], 'legacy-review-required')
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.store.handle(501, request('lease-release', task_id='a'*32))

    def test_offline_recovery_never_trusts_an_old_empty_inventory(self):
        from recover import recover
        from unittest.mock import patch
        backup = self.store.backup()
        base = self.store.read('settings')
        target = {k: DEFAULTS['settings']['downloads'][k] for k in ('environment','sshHost','sshUser','sshPort')}
        self.store.handle(501, request('task-create', task_id='a'*32, owner='b'*32,
                                      epoch=base['epoch'], store_id=base['store_id'], target=target))
        self.store.close()
        output = self.root / 'recovered'
        # Temp ancestry is not a production installation; the actual DB/lock is exercised.
        with patch('recover.protected_ancestors'):
            recover(self.root, backup['backup_id'], output)
        restored = ConfigStore(output)
        try:
            self.assertNotEqual(restored.status()['epoch'], base['epoch'])
            self.assertEqual(restored.status()['task_inventory']['state'], 'legacy-review-required')
            with self.assertRaisesRegex(ValueError, 'Legacy'):
                restored.handle(501, self.patch(restored.read('settings'), ['downloads','environment'], 'other'))
        finally:
            restored.close()
            self.store = ConfigStore(self.root)
        self.assertEqual(len(self.store.status()['target_leases']), 1)

    def test_upgrade_does_not_infer_empty_legacy_inventory(self):
        self.store.db.execute("DELETE FROM meta WHERE key='task_inventory'")
        self.store.db.execute('DROP TABLE host_tasks')
        self.store.close(); self.store = ConfigStore(self.root)
        self.assertEqual(self.store.status()['task_inventory']['state'], 'legacy-review-required')
        with self.assertRaisesRegex(ValueError, 'Legacy'):
            self.store.handle(501, self.patch(self.store.read('settings'), ['downloads','environment'], 'new'))

    def test_concurrent_task_creation_and_target_change_never_split_target(self):
        from concurrent.futures import ThreadPoolExecutor
        base = self.store.read('settings')
        target = {k: DEFAULTS['settings']['downloads'][k] for k in ('environment','sshHost','sshUser','sshPort')}
        create = request('task-create', task_id='f'*32, owner='c'*32, epoch=base['epoch'], store_id=base['store_id'], target=target)
        def attempt(r):
            try: return self.store.handle(501, r)
            except ValueError: return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, [create, self.patch(base, ['downloads','environment'], 'changed')]))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_corruption_permissions_and_missing_never_reset(self):
        self.store.close()
        database=self.root/'state.sqlite';original=database.read_bytes()
        database.chmod(0o644)
        with self.assertRaises(PermissionError):ConfigStore(self.root)
        self.assertEqual(database.read_bytes(),original);database.chmod(0o600)
        database.write_bytes(b'BROKEN')
        with self.assertRaises(sqlite3.DatabaseError):ConfigStore(self.root)
        self.assertEqual(database.read_bytes(),b'BROKEN')
        database.write_bytes(original)
        self.store=ConfigStore(self.root)

    def test_sigkill_hot_journal_recovers_request_and_document_together(self):
        import subprocess, sys
        base=self.store.read('settings');self.store.close()
        script="""import sys,time
from config_store import ConfigStore
s=ConfigStore(sys.argv[1])
with s.transaction():
 s.db.execute("UPDATE documents SET body='{}', revision=999 WHERE name='settings'")
 s.db.execute("INSERT INTO requests VALUES('partial','partial','{}')")
 print('uncommitted',flush=True)
 time.sleep(60)
"""
        child=subprocess.Popen([sys.executable,'-c',script,str(self.root)],stdout=subprocess.PIPE,
                               env={**os.environ,'PYTHONPATH':str(Path(__file__).parent)})
        self.assertEqual(child.stdout.readline().strip(),b'uncommitted')
        self.assertEqual((Path(str(self.root/'state.sqlite')+'-journal').stat().st_mode & 0o777),0o600)
        child.kill();child.wait();child.stdout.close()
        self.store=ConfigStore(self.root)
        self.assertEqual(self.store.read('settings'),base)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM requests WHERE id='partial'").fetchone())

    def test_rollback_disk_full(self):
        base=self.store.read('settings')
        self.store.db.execute('PRAGMA max_page_count='+str(self.store.db.execute('PRAGMA page_count').fetchone()[0]))
        with self.assertRaises(sqlite3.OperationalError):
            self.store.handle(501,self.patch(base,['future'],'x'*200000))
        self.assertEqual(self.store.read('settings'),base)

if __name__=='__main__':unittest.main()
