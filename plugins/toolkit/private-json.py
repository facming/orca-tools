"""JSON stdin/stdout adapter; config data and paths never enter command arguments."""
import json
import sys
from private_files import PrivateDirectory


def run(request):
    if request['name'] not in ('settings.json', 'downloads-preferences.json'):
        raise ValueError('Unexpected configuration file')
    directory = PrivateDirectory(request['root'])
    try:
        with directory.lock('.' + request['name'] + '.lock'):
            data, version = directory.read(request['name'])
            value = None if data is None else json.loads(data.decode('utf-8-sig'))
            if data is not None and not isinstance(value, dict):
                raise ValueError('Invalid configuration object; original file retained')
            if request['operation'] == 'write':
                if version != request['expected']:
                    raise ValueError('External file change; reload before saving')
                data = json.dumps(request['value'], ensure_ascii=False, indent=2).encode()
                version = directory.replace(request['name'], data, version)
                value = request['value']
            return {'value': value, 'version': version}
    finally:
        directory.close()


if __name__ == '__main__':
    try:
        print(json.dumps(run(json.load(sys.stdin))))
    except Exception:
        # No configuration content or sensitive path is reflected.
        print('Unsafe, changed, corrupt or inaccessible settings; original configuration retained. Reload or repair it manually.', file=sys.stderr)
        sys.exit(3)
