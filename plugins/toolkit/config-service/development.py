"""Explicit development Host storage; real SQLite, without production UID isolation."""
import argparse
import base64
import os
from pathlib import Path
import sys
import uuid
from config_store import ConfigStore
from secure_store import dumps, loads

p = argparse.ArgumentParser()
p.add_argument('--state', required=True)
p.add_argument('--initialize-settings')
a = p.parse_args()
store = ConfigStore(a.state, bool(a.initialize_settings))
try:
    if a.initialize_settings:
        sources = {'settings': base64.b64encode(Path(a.initialize_settings).read_bytes()).decode(),
                   'downloads-preferences': None}
        preview = store.handle(os.getuid(), {'op': 'preview', 'request_id': uuid.uuid4().hex, 'sources': sources})
        store.handle(os.getuid(), {'op': 'import', 'request_id': uuid.uuid4().hex,
                                 'preview_id': preview['preview_id'], 'hashes': preview['hashes']})
    else:
        try:
            print(dumps({'ok': True, 'result': store.handle(os.getuid(), loads(sys.stdin.buffer.read()))}))
        except Exception:
            print(dumps({'ok': False, 'error': 'Development configuration request failed.'}))
finally:
    store.close()
