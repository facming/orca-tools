#!/usr/bin/env python3
import argparse
import ctypes
import os
from pathlib import Path
from config_store import ConfigStore
from ipc import Server, config_authorizer
from secure_store import protected_ancestors

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--state', required=True)
    p.add_argument('--socket', required=True)
    p.add_argument('--tenant', type=int, required=True)
    p.add_argument('--requirement', required=True)
    p.add_argument('--initialize', action='store_true', help='Explicit empty installation only; never overwrites')
    a = p.parse_args()
    protected_ancestors(a.state)
    auth = config_authorizer(a.tenant, a.requirement)
    native = ctypes.CDLL(str(Path(__file__).with_name('peer_identity.dylib')))
    native.orca_local_storage.argtypes = [ctypes.c_char_p]
    if native.orca_local_storage(os.fsencode(a.state)) != 0:
        raise PermissionError('Configuration requires writable local storage')
    store = ConfigStore(a.state, a.initialize)
    if not a.initialize:
        try:
            with Server(a.socket, store, auth) as server:
                server.serve_forever()
        finally:
            store.close()
            os.unlink(a.socket)
    else:
        store.close()
