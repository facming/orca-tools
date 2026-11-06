"""Field-level optimistic concurrency, lossless migration and target leases."""
import base64
import hashlib
import os
import re
import sqlite3
import uuid
from secure_store import Store, dumps, loads, private
from task_records import TaskRecords

DEFAULTS = {'settings': {'accounts': {'enabled': True, 'environment': '', 'worktree': ''},
             'downloads': {'enabled': True, 'environment': '', 'sshHost': '', 'sshUser': '', 'sshPort': 22},
             'gpu': {'enabled': True}}, 'downloads-preferences': {}}
TARGET = ('environment', 'sshHost', 'sshUser', 'sshPort')


def validate(namespace, value):
    if not isinstance(value, dict):
        raise ValueError('Document must be an object')
    if namespace == 'settings':
        for section, fields in DEFAULTS['settings'].items():
            obj = value.get(section, {})
            if not isinstance(obj, dict):
                raise ValueError('Invalid settings section')
            for key, default in fields.items():
                v = obj.get(key, default)
                if type(v) is not type(default):
                    raise ValueError('Invalid known field: ' + key)
                if key == 'sshPort' and not 1 <= v <= 65535:
                    raise ValueError('Invalid port')
                if isinstance(v, str) and (len(v) > 4096 or any(ord(c) < 32 for c in v)):
                    raise ValueError('Invalid text')
            if section == 'accounts' and obj.get('worktree') and not obj['worktree'].startswith('path:/'):
                raise ValueError('Invalid worktree')
            if section == 'downloads':
                if obj.get('sshHost') and not re.fullmatch(r'[A-Za-z0-9.:-]+', obj['sshHost']):
                    raise ValueError('Invalid host')
                if obj.get('sshUser') and not re.fullmatch(r'[a-z_][a-z0-9_-]*', obj['sshUser']):
                    raise ValueError('Invalid account')
    elif namespace == 'downloads-preferences':
        if 'folder' in value and (not isinstance(value['folder'], str) or not value['folder'].startswith('/')):
            raise ValueError('Invalid folder')
        if 'remoteLocations' in value and not isinstance(value['remoteLocations'], dict):
            raise ValueError('Invalid remote locations')
    else:
        raise ValueError('Unknown namespace')


def overlap(a, b):
    return a[:len(b)] == b or b[:len(a)] == a


class ConfigStore(Store):
    def __init__(self, root, initialize=False):
        super().__init__(root, initialize)
        if initialize:
            self.db.executescript('''BEGIN IMMEDIATE;
            CREATE TABLE documents(name TEXT PRIMARY KEY, revision INTEGER, body TEXT);
            CREATE TABLE revisions(name TEXT, revision INTEGER, paths TEXT, body TEXT, PRIMARY KEY(name,revision));
            CREATE TABLE previews(id TEXT PRIMARY KEY, raw TEXT, hashes TEXT);
            CREATE TABLE sources(id TEXT PRIMARY KEY, raw TEXT, hashes TEXT);
            CREATE TABLE leases(task TEXT PRIMARY KEY, target TEXT);
            INSERT INTO meta VALUES('migration','pending');
            COMMIT;''')
        self.tasks = TaskRecords(self, initialize)

    def status(self):
        return {'state': 'ready' if self.meta('migration') == 'complete' else 'migration-pending',
                'store_id': self.meta('store_id'), 'epoch': self.meta('epoch'),
                'task_inventory': self.tasks.inventory(),
                'target_leases': [{'task_id': task, 'target': loads(target)} for task, target in self.db.execute('SELECT task,target FROM leases')]}

    def read(self, name):
        if name not in DEFAULTS:
            raise ValueError('Unknown namespace')
        if self.meta('migration') != 'complete':
            raise ValueError('Configuration migration pending confirmation')
        row = self.db.execute('SELECT revision,body FROM documents WHERE name=?', (name,)).fetchone()
        return {**self.status(), 'revision': str(row[0]), 'raw_json': row[1]}

    def patch(self, r):
        name = r['namespace']
        current = self.read(name)
        if r['epoch'] != current['epoch'] or r['store_id'] != current['store_id']:
            raise ValueError('Configuration epoch changed')
        base = int(r['revision'])
        if base < 1 or base > int(current['revision']):
            raise ValueError('Unknown base revision')
        paths = []
        for op in r['patch']:
            p = op['path']
            if (op['op'] not in ('set', 'delete') or not isinstance(p, list) or not 1 <= len(p) <= 16
                or any(not isinstance(x, str) or not x or len(x) > 256 or x in ('__proto__', 'prototype', 'constructor') for x in p)):
                raise ValueError('Invalid patch')
            if any(overlap(p, q) for q in paths):
                raise ValueError('Overlapping patch')
            paths.append(p)
        if not 1 <= len(paths) <= 128:
            raise ValueError('Invalid patch size')
        for row in self.db.execute('SELECT paths FROM revisions WHERE name=? AND revision>?', (name, base)):
            if any(overlap(a, b) for a in paths for b in loads(row[0])):
                raise ValueError('Configuration field conflict; reload')
        value = loads(current['raw_json'])
        for op in r['patch']:
            obj = value
            for key in op['path'][:-1]:
                if key not in obj:
                    obj[key] = {}
                obj = obj[key]
                if not isinstance(obj, dict):
                    raise ValueError('Arrays are atomic conflict units')
            if op['op'] == 'set':
                obj[op['path'][-1]] = op['value']
            else:
                obj.pop(op['path'][-1], None)
        validate(name, value)
        if name == 'settings':
            old = loads(current['raw_json']).get('downloads', {})
            new = value.get('downloads', {})
            if any(old.get(k) != new.get(k) for k in TARGET):
                self.tasks.require_quiescent()
        revision = int(current['revision']) + 1
        body = dumps(value)
        self.db.execute('UPDATE documents SET revision=?,body=? WHERE name=? AND revision=?',
                        (revision, body, name, revision-1))
        self.db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (name, revision, dumps(paths), body))
        return self.read(name)

    def preview(self, r):
        raw, hashes, report = {}, {}, {}
        if set(r['sources']) != set(DEFAULTS):
            raise ValueError('Both sources must be explicitly selected (null means defaults)')
        for name, encoded in r['sources'].items():
            data = base64.b64decode(encoded, validate=True) if encoded is not None else None
            value = loads(data) if data is not None else DEFAULTS[name]
            validate(name, value)
            raw[name] = encoded
            hashes[name] = hashlib.sha256(data).hexdigest() if data is not None else 'explicit-defaults'
            report[name] = {'raw_json': dumps(value), 'sha256': hashes[name],
                            'known_fields': DEFAULTS[name], 'unknown_fields': 'preserved in full raw_json'}
        preview = uuid.uuid4().hex
        self.db.execute('INSERT INTO previews VALUES(?,?,?)', (preview, dumps(raw), dumps(hashes)))
        return {'preview_id': preview, 'hashes': hashes, 'documents': report}

    def import_preview(self, r):
        self.tasks.require_quiescent()
        if self.meta('migration') == 'complete':
            raise ValueError('Already migrated; use versioned patch or restore')
        row = self.db.execute('SELECT raw,hashes FROM previews WHERE id=?', (r['preview_id'],)).fetchone()
        if not row or loads(row[1]) != r['hashes']:
            raise ValueError('Preview confirmation mismatch')
        for name, encoded in loads(row[0]).items():
            value = loads(base64.b64decode(encoded)) if encoded is not None else DEFAULTS[name]
            if name == 'settings':
                value = {**value, **{section: {**fields, **value.get(section, {})} for section, fields in DEFAULTS[name].items()}}
            body = dumps(value)
            self.db.execute('INSERT INTO documents VALUES(?,1,?)', (name, body))
            self.db.execute('INSERT INTO revisions VALUES(?,1,?,?)', (name, '[[]]', body))
        self.db.execute('INSERT INTO sources VALUES(?,?,?)', (r['preview_id'], *row))
        self.db.execute("UPDATE meta SET value='complete' WHERE key='migration'")
        return self.status()

    def backup(self):
        name = 'backup-' + uuid.uuid4().hex + '.sqlite'
        with self.lock:
            target = sqlite3.connect(self.root / name)
            try:
                self.db.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Invalid backup')
            finally:
                target.close()
            private(self.root / name)
            fd = os.open(self.root / name, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return {'backup_id': name}

    def restore(self, r):
        name = r['backup_id']
        if not re.fullmatch(r'backup-[a-f0-9]{32}\.sqlite', name):
            raise ValueError('Invalid backup identifier')
        self.tasks.require_quiescent()
        private(self.root / name)
        source = sqlite3.connect('file:' + str(self.root / name) + '?mode=ro', uri=True)
        try:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Invalid backup')
            rows = source.execute('SELECT name,body FROM documents').fetchall()
            if len(rows) != 2:
                raise ValueError('Incomplete backup')
            for ns, body in rows:
                validate(ns, loads(body))
                rev = int(self.read(ns)['revision']) + 1
                self.db.execute('UPDATE documents SET body=?,revision=? WHERE name=?', (body, rev, ns))
                self.db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (ns, rev, '[[]]', body))
            self.db.execute("UPDATE meta SET value=? WHERE key='epoch'", (uuid.uuid4().hex,))
        finally:
            source.close()
        return self.status()

    def handle(self, actor, r):
        with self.lock:
            if r['op'] == 'status':
                return self.status()
            if r['op'] == 'read':
                return self.read(r['namespace'])
            if r['op'] == 'backup':
                return self.backup()
            def mutate():
                if r['op'] in ('import', 'restore') and ('epoch' in r or 'store_id' in r):
                    if r.get('epoch') != self.meta('epoch') or r.get('store_id') != self.meta('store_id'):
                        raise ValueError('Configuration epoch changed')
                if r['op'].startswith('task-'):
                    return self.tasks.mutate(r)
                if r['op'] == 'patch':
                    return self.patch(r)
                if r['op'] == 'patch-many':
                    if not isinstance(r['documents'], list) or not 1 <= len(r['documents']) <= 2:
                        raise ValueError('Invalid document transaction')
                    return {'documents': [self.patch(doc) for doc in r['documents']]}
                if r['op'] == 'preview':
                    return self.preview(r)
                if r['op'] == 'import':
                    return self.import_preview(r)
                if r['op'] == 'restore':
                    return self.restore(r)
                if r['op'] == 'lease-acquire':
                    value = loads(self.read('settings')['raw_json'])['downloads']
                    target = {k: value[k] for k in TARGET}
                    if r['target'] != target:
                        raise ValueError('Target changed')
                    self.db.execute('INSERT INTO leases VALUES(?,?)', (r['task_id'], dumps(target)))
                    return {'task_id': r['task_id'], 'target': target}
                if r['op'] == 'lease-release':
                    if self.db.execute('SELECT 1 FROM host_tasks WHERE id=?', (r['task_id'],)).fetchone():
                        raise ValueError('Host task ownership required')
                    self.db.execute('DELETE FROM leases WHERE task=?', (r['task_id'],))
                    return {'released': r['task_id']}
                raise ValueError('Unknown operation')
            return self.request(actor, r, mutate)
