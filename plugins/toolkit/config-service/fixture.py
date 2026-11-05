"""Explicit synthetic test process; never imported by production clients."""
import sys
from config_store import ConfigStore, DEFAULTS
from secure_store import loads, dumps
from test_store import request
if __name__ == '__main__':
    root=sys.argv[1]
    initialize=len(sys.argv)>2 and sys.argv[2]=='init'
    store=ConfigStore(root,initialize)
    try:
        if initialize:
            raw=sys.stdin.buffer.read()
            p=store.handle(501,request('preview',sources=loads(raw) if raw else {k:None for k in DEFAULTS}))
            store.handle(501,request('import',preview_id=p['preview_id'],hashes=p['hashes']))
        else:
            try:print(dumps({'ok':True,'result':store.handle(501,loads(sys.stdin.buffer.read()))}))
            except Exception as e:print(dumps({'ok':False,'error':str(e)}))
    finally:store.close()
