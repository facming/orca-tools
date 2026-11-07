#!/usr/bin/env python3
"""Run the development Host through an existing SSH connection, without copying keys."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('--electron', required=True)
p.add_argument('--entry', required=True)
p.add_argument('--connection', required=True)
p.add_argument('--local-port', required=True, type=int)
p.add_argument('--remote-port', required=True, type=int)
a = p.parse_args()
if any(not 1 <= port <= 65535 for port in (a.local_port, a.remote_port)):
    raise SystemExit('Invalid tunnel port')
c = json.loads(Path(a.connection).read_text())
if (not re.fullmatch(r'[A-Za-z0-9_.:-]+', c['host']) or c['host'].startswith('-')
        or not re.fullmatch(r'[a-z_][a-z0-9_-]*', c['username'])
        or not 1 <= int(c['ssh_port']) <= 65535):
    raise SystemExit('Invalid existing SSH connection')

def listening():
    try:
        with socket.create_connection(('127.0.0.1', a.local_port), timeout=0.3):
            return True
    except OSError:
        return False

tunnel = None
try:
    if not listening():
        tunnel = subprocess.Popen([
            '/usr/bin/ssh', '-F', '/dev/null', '-N', '-T',
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
            '-o', 'LogLevel=ERROR', '-p', str(c['ssh_port']),
            '-i', os.path.expanduser(c['ssh_key']),
            '-L', f'127.0.0.1:{a.local_port}:127.0.0.1:{a.remote_port}',
            c['username'] + '@' + c['host']
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            if tunnel.poll() is not None:
                raise SystemExit('Existing SSH connection failed; check its key and known host.')
            if listening():
                break
            time.sleep(0.25)
        else:
            raise SystemExit('Download tunnel did not become ready.')
    raise SystemExit(subprocess.call([a.electron, a.entry]))
finally:
    if tunnel is not None:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()
            tunnel.wait()
