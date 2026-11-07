"""Durable records written only through the authenticated Host channel."""
import re
from secure_store import dumps, loads

STATES = {'created': {'running', 'cancelled'}, 'running': {'paused', 'error', 'complete'},
          'paused': {'running', 'cancelled'}, 'error': {'running', 'cancelled'},
          'complete': set(), 'cancelled': set()}
TARGET = ('environment', 'sshHost', 'sshUser', 'sshPort')


class TaskRecords:
    def __init__(self, store, fresh):
        self.store = store
        self.db = store.db
        with store.transaction():
            self.db.execute('''CREATE TABLE IF NOT EXISTS host_tasks(
                id TEXT PRIMARY KEY, owner TEXT, epoch TEXT, revision INTEGER,
                state TEXT, target TEXT, released INTEGER)''')
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('task_inventory',?)",
                            ('complete' if fresh else 'legacy-review-required',))

    def inventory(self):
        return {'state': self.store.meta('task_inventory'), 'tasks': [
            dict(zip(('task_id', 'owner', 'epoch', 'revision', 'state', 'target', 'released'),
                     (*row[:3], str(row[3]), row[4], loads(row[5]), bool(row[6]))))
            for row in self.db.execute('SELECT * FROM host_tasks ORDER BY id')]}

    def require_quiescent(self):
        if self.store.meta('task_inventory') != 'complete':
            raise ValueError('Legacy task inventory requires offline review')
        if self.db.execute('SELECT 1 FROM leases LIMIT 1').fetchone():
            raise ValueError('Active or unknown target leases; cleanup required')

    def mutate(self, r):
        required = {'op', 'request_id', 'task_id', 'owner', 'epoch', 'store_id'}
        required |= ({'target'} if r['op'] in ('task-create', 'task-adopt') else
                     {'revision', 'previous_owner'} if r['op'] == 'task-claim' else
                     {'revision', 'state', 'released'})
        if set(r) != required or any(not isinstance(r[k], str) or not re.fullmatch(r'[a-f0-9]{32}', r[k])
                                    for k in ('task_id', 'owner', 'epoch', 'store_id')):
            raise ValueError('Invalid Host task request')
        if r['epoch'] != self.store.meta('epoch') or r['store_id'] != self.store.meta('store_id'):
            raise ValueError('Configuration epoch changed')
        if r['op'] == 'task-adopt':
            lease = self.db.execute('SELECT target FROM leases WHERE task=?', (r['task_id'],)).fetchone()
            if not lease or loads(lease[0]) != r['target']:
                raise ValueError('Legacy lease target cannot be verified')
            self.db.execute('INSERT INTO host_tasks VALUES(?,?,?,1,?,?,0)',
                            (r['task_id'], r['owner'], r['epoch'], 'paused', lease[0]))
        elif r['op'] == 'task-claim':
            row = self.db.execute('SELECT owner,epoch,revision,state,released FROM host_tasks WHERE id=?',
                                  (r['task_id'],)).fetchone()
            if not row or (r['previous_owner'], r['epoch'], r['revision']) != (row[0], row[1], str(row[2])):
                raise ValueError('Task claim conflict')
            self.db.execute('UPDATE host_tasks SET owner=?,revision=revision+1,state=? WHERE id=?',
                            (r['owner'], 'paused' if row[3] in ('created', 'running') else row[3], r['task_id']))
        elif r['op'] == 'task-create':
            if self.store.meta('task_inventory') != 'complete':
                raise ValueError('Legacy task inventory requires offline review')
            settings = loads(self.store.read('settings')['raw_json'])['downloads']
            target = {key: settings[key] for key in TARGET}
            if r['target'] != target:
                raise ValueError('Target changed')
            if self.db.execute('SELECT count(*) FROM leases').fetchone()[0] >= 20:
                raise ValueError('Too many leased tasks')
            self.db.execute('INSERT INTO host_tasks VALUES(?,?,?,1,?,?,0)',
                            (r['task_id'], r['owner'], r['epoch'], 'created', dumps(target)))
            self.db.execute('INSERT INTO leases VALUES(?,?)', (r['task_id'], dumps(target)))
        elif r['op'] == 'task-update':
            row = self.db.execute('SELECT owner,epoch,revision,state,released FROM host_tasks WHERE id=?',
                                  (r['task_id'],)).fetchone()
            if not row or (r['owner'], r['epoch'], r['revision']) != (row[0], row[1], str(row[2])):
                raise ValueError('Task owner, epoch or revision conflict')
            state, released = r['state'], r['released']
            if row[4] and released is True and state == row[3]:
                return next(t for t in self.inventory()['tasks'] if t['task_id'] == r['task_id'])
            if (type(released) is not bool or row[4] or state not in STATES or
                    (state != row[3] and state not in STATES[row[3]]) or
                    (released and state not in ('complete', 'cancelled'))):
                raise ValueError('Invalid task transition or cleanup')
            self.db.execute('UPDATE host_tasks SET revision=revision+1,state=?,released=? WHERE id=?',
                            (state, released, r['task_id']))
            if released:
                self.db.execute('DELETE FROM leases WHERE task=?', (r['task_id'],))
        else:
            raise ValueError('Unknown task operation')
        return next(t for t in self.inventory()['tasks'] if t['task_id'] == r['task_id'])
