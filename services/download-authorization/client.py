#!/usr/bin/env python3
"""Install as three immutable entry points. Never interprets arbitrary commands."""
import base64
import os
import re
import socket
import sys
from ipc import call, receive, send
from secure_store import loads, dumps

SOCKET = '/run/orca-download/authority.sock'


def main():
    mode, *args = sys.argv[1:]
    if mode == 'lookup':
        if len(args) != 2 or args[0] != 'ssh-ed25519':
            return
        print(call(SOCKET, {'op': 'lookup', 'public_key': ' '.join(args)})['authorized_key'])
    elif mode == 'manage':
        if len(args) != 1:
            raise ValueError('One encoded request required')
        request = loads(base64.b64decode(args[0], validate=True))
        print('ORCA_DOWNLOAD_V3=' + dumps(call(SOCKET, request)), flush=True)
    elif mode == 'dispatch':
        if len(args) != 2 or any(not re.fullmatch('[a-f0-9]{32}', x) for x in args):
            raise ValueError('Invalid forced binding')
        words = os.environ.get('SSH_ORIGINAL_COMMAND', '').split(' ')
        if len(words) != 3 or words[0] != 'read' or any(not re.fullmatch('[0-9]{1,16}', x) for x in words[1:]):
            raise ValueError('Only bounded read is permitted')
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(30); sock.connect(SOCKET)
            with sock.makefile('rwb', buffering=0) as stream:
                send(stream, {'op': 'read-range', 'task_id': args[0], 'generation': args[1],
                              'offset': int(words[1]), 'length': int(words[2])})
                response = receive(stream)
                if not response['ok']:
                    raise ValueError('Read denied')
                result = response['result']
                remaining = result['length']
                while remaining:
                    block = stream.read(min(65536, remaining))
                    if not block:
                        raise ValueError('Authority stopped')
                    sys.stdout.buffer.write(block); remaining -= len(block)
                sys.stdout.buffer.flush()
                send(stream, {'ack': result['lease']})
                if not receive(stream)['ok']:
                    raise ValueError('Drain acknowledgment failed')
    else:
        raise ValueError('Unknown mode')

if __name__ == '__main__':
    main()
