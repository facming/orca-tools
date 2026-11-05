#!/usr/bin/env python3
"""Offline, explicit recovery into a NEW service-owned directory.
Never resets, overwrites, renames or deletes the damaged original or journal.
Run as the isolated service identity after the administrator stops its daemon.
"""
import argparse
import fcntl
import os
from pathlib import Path
import re
import sqlite3
import uuid
from secure_store import private, protected_ancestors


def recover(state, backup, output):
    state=Path(state);output=Path(output)
    private(state,True);protected_ancestors(state);protected_ancestors(output)
    if not re.fullmatch(r'backup-[a-f0-9]{32}\.sqlite',backup):raise ValueError('Invalid backup identifier')
    lock=os.open(state/'writer.lock',os.O_RDWR|os.O_NOFOLLOW)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        private(state/backup)
        source=sqlite3.connect('file:'+str(state/backup)+'?mode=ro',uri=True)
        try:
            if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid backup')
            if source.execute("SELECT value FROM meta WHERE key='schema'").fetchone()!=('1',):raise ValueError('Unsupported schema')
            if source.execute('SELECT 1 FROM leases LIMIT 1').fetchone():raise ValueError('Backup has unresolved target leases')
            output.mkdir(mode=0o700)  # refuses any existing destination
            os.umask(0o077)
            target=sqlite3.connect(output/'state.sqlite')
            try:
                source.backup(target)
                target.execute('PRAGMA synchronous=EXTRA');target.execute('PRAGMA fullfsync=ON')
                target.execute("UPDATE meta SET value=? WHERE key='epoch'",(uuid.uuid4().hex,))
                target.execute('DELETE FROM requests')  # old epoch acknowledgments cannot be reused
                target.commit()
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Recovery verification failed')
            finally:target.close()
            private(output/'state.sqlite')
            for path in (output/'state.sqlite',output):
                fd=os.open(path,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
        finally:source.close()
    finally:os.close(lock)
    return output

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--state',required=True);p.add_argument('--backup-id',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    print(recover(a.state,a.backup_id,a.output))
    print('Original retained. Administrator must review restored date, all local tasks/leases, and explicitly switch the stopped daemon to the new directory.')
