#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import sys
# Installed bundle puts the shared modules beside this service. Source development
# can use PYTHONPATH; production runs an isolated interpreter (-I) from its bundle.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from authority import Authority
from ipc import Server, peer
from secure_store import loads, protected_ancestors

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--policy', required=True)
    p.add_argument('--initialize', action='store_true')
    a = p.parse_args()
    policy_path = Path(a.policy)
    protected_ancestors(policy_path)
    s = policy_path.lstat()
    if s.st_uid != 0 or s.st_mode & 0o022 or not policy_path.is_file() or policy_path.is_symlink():
        raise PermissionError('Policy must be root-owned')
    policy = loads(policy_path.read_bytes())
    if os.geteuid() == 0 or os.geteuid() in [policy['reader_uid'], policy['lookup_uid'], *map(int, policy['owners'])]:
        raise PermissionError('Authority must run under a distinct non-root UID')
    protected_ancestors(policy['state'])
    store = Authority(policy['state'], policy, a.initialize)
    if a.initialize:
        print(store.verify_key)
        print('authority_epoch=' + store.meta('epoch'))
        store.close()
    else:
        allowed = {policy['reader_uid'], policy['lookup_uid'], *map(int, policy['owners'])}
        def authorize(sock):
            uid, _ = peer(sock)
            if uid not in allowed:
                raise PermissionError('Unauthorized peer')
            return uid
        try:
            with Server(policy['socket'], store, authorize) as server:
                server.serve_forever()
        finally:
            store.close()
            os.unlink(policy['socket'])
