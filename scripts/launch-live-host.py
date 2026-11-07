#!/usr/bin/env python3
"""Supervise only this development Host and its existing SSH connection."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--electron')
    p.add_argument('--entry', required=True)
    p.add_argument('--connection')
    p.add_argument('--local-port', type=int)
    p.add_argument('--remote-port', type=int)
    p.add_argument('--stop', action='store_true')
    a = p.parse_args()
    root = Path(a.entry).resolve().parent
    control = str(root / 'control.sock')
    if a.stop:
        with socket.socket(socket.AF_UNIX) as client:
            try:
                client.connect(control)
            except (FileNotFoundError, ConnectionRefusedError):
                print('Development Host is not running.')
                return
            client.sendall(b'quit')
        print('Saving downloads and closing the development Host.')
        return
    if not a.electron or not a.connection or any(
        port is None or not 1 <= port <= 65535 for port in (a.local_port, a.remote_port)
    ):
        p.error('Supply Electron, the existing connection and valid tunnel ports')
    c = json.loads(Path(a.connection).read_text())
    if (not re.fullmatch(r'[A-Za-z0-9_.:-]+', c['host']) or c['host'].startswith('-')
            or not re.fullmatch(r'[a-z_][a-z0-9_-]*', c['username'])
            or not 1 <= int(c['ssh_port']) <= 65535):
        raise SystemExit('Invalid existing SSH connection')
    with (root / 'launcher.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Development Host is already running.')
            return
        Path(control).unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(control)
            os.chmod(control, 0o600)
            server.listen(1)
            server.settimeout(0.5)
            tunnel = host = None
            stopping = False
            next_attempt = 0
            failures = 0

            def stop(*_):
                nonlocal stopping
                stopping = True
                if host and host.poll() is None:
                    host.terminate()

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                # Never borrow or terminate an unrelated listener on this port.
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', a.local_port))
                while not stopping or (host and host.poll() is None):
                    if not stopping and (tunnel is None or tunnel.poll() is not None) and time.monotonic() >= next_attempt:
                        tunnel = subprocess.Popen([
                            '/usr/bin/ssh', '-F', '/dev/null', '-N', '-T',
                            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                            '-o', 'IdentitiesOnly=yes', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
                            '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=15',
                            '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
                            '-o', 'LogLevel=ERROR', '-p', str(c['ssh_port']),
                            '-i', os.path.expanduser(c['ssh_key']),
                            '-L', f'127.0.0.1:{a.local_port}:127.0.0.1:{a.remote_port}',
                            c['username'] + '@' + c['host']
                        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        failures += 1
                        next_attempt = time.monotonic() + min(15, 2 ** min(failures, 4))
                        print('Starting download tunnel' if failures == 1 else 'Reconnecting download tunnel', flush=True)
                    if host is None and tunnel and tunnel.poll() is None:
                        try:
                            with socket.create_connection(('127.0.0.1', a.local_port), timeout=0.2):
                                failures = 0
                            host = subprocess.Popen([a.electron, a.entry])
                        except OSError:
                            pass
                    if host and host.poll() is not None:
                        return host.returncode
                    try:
                        client, _ = server.accept()
                        with client:
                            client.settimeout(1)
                            if client.recv(16) == b'quit':
                                stop()
                    except socket.timeout:
                        pass
            finally:
                if tunnel and tunnel.poll() is None:
                    tunnel.terminate()
                    try:
                        tunnel.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        tunnel.kill()
                        tunnel.wait()
                Path(control).unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
