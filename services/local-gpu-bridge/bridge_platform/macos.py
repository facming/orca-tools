"""User-level macOS ComfyUI capabilities. No shell, PF, or native script API."""
import ipaddress
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from .base import Platform

LABEL = 'org.orca.local-gpu-bridge'
TAILNET = ipaddress.ip_network('100.64.0.0/10')


def require_user():
    if os.getuid() == 0 or os.geteuid() == 0 or os.getuid() != os.geteuid():
        raise ValueError('Bridge requires an unprivileged login user.')


def read_token(root):
    private_path(root, True)
    token = private_path(Path(root) / 'token').read_text().strip()
    if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid bridge token file.')
    return token


def private_path(path, directory=False):
    path = Path(path)
    info = path.lstat()
    if path.is_symlink() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise ValueError('Private bridge path has unsafe ownership or permissions.')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('Private bridge path has an invalid type.')
    # Reject symlink ancestors, including redirected Application Support roots.
    if any(p.is_symlink() for p in path.parents):
        raise ValueError('Private bridge path contains a symbolic link.')
    return path


def private_dir(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Private bridge path contains a symbolic link.')
    if not path.exists() and not path.is_symlink():
        path.mkdir(parents=True, mode=0o700)
    return private_path(path, True)


def write_private(path, data):
    """New files only. Never silently replace an installation or user data."""
    path = Path(path)
    private_path(path.parent, True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def tail_ip(value):
    try:
        address = ipaddress.ip_address(value)
        if address.version != 4 or address not in TAILNET:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('A Tailscale IPv4 address in 100.64.0.0/10 is required.') from None
    return str(address)


def environment(system=None, machine=None, version=None):
    require_user()
    if (system or platform.system()) != 'Darwin':
        raise ValueError('This entry point requires macOS.')
    arch = machine or platform.machine()
    if arch != 'arm64':
        raise ValueError('This phase supports native macOS arm64 only; Intel/Rosetta is unverified.')
    if tuple(version or sys.version_info[:2]) < (3, 10):
        raise ValueError('Python 3.10 or newer is required.')
    return {'platform': 'darwin', 'architecture': arch}


def discover_python(root, selected=None):
    root = Path(root)
    candidates = [Path(selected)] if selected else [root / '.venv/bin/python3', root / 'venv/bin/python3']
    for candidate in candidates:
        # Keep venv path: resolving its executable symlink loses the venv.
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.absolute())
    raise ValueError('Select the existing ComfyUI Python environment; no environment will be installed.')


def validate_comfy(root):
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir() or not (root / 'main.py').is_file() or not (root / 'comfy/cli_args.py').is_file():
        raise ValueError('Select an existing ComfyUI root containing main.py and comfy/cli_args.py.')
    # These flags prevent third-party custom node prestartup scripts and API nodes.
    flags = (root / 'comfy/cli_args.py').read_text()
    if not all(flag in flags for flag in ('--disable-all-custom-nodes', '--disable-api-nodes')):
        raise ValueError('ComfyUI must support disabling custom nodes and API nodes.')
    return root


PROBE = '''import json, platform, sys
import torch, aiohttp, PIL, numpy, safetensors, torchsde, einops, yaml
mps = getattr(torch.backends, "mps", None)
print(json.dumps({"version":list(sys.version_info[:2]),"architecture":platform.machine(),"mps":bool(mps and mps.is_built() and mps.is_available())}))'''


def probe_python(python, run=subprocess.run):
    try:
        result = run([python, '-I', '-c', PROBE], cwd='/', capture_output=True, text=True, timeout=60, check=True)
        value = json.loads(result.stdout)
        environment('Darwin', value['architecture'], value['version'])
        if type(value['mps']) is not bool:
            raise ValueError()
        return {'backend': 'mps' if value['mps'] else 'cpu', 'cpu_fallback': not value['mps']}
    except Exception:
        raise ValueError('ComfyUI Python/dependency/MPS probe failed. Verify the selected trusted environment.') from None


class MacOSPlatform(Platform):
    comfy_only = True

    def __init__(self, home=None, run=subprocess.run):
        self.home = Path(home or Path.home())
        self.root = self.home / 'Library/Application Support/LocalGpuBridge'
        self.logs = self.home / 'Library/Logs/LocalGpuBridge'
        self.cache = self.home / 'Library/Caches/LocalGpuBridge'
        self.plist = self.home / 'Library/LaunchAgents' / (LABEL + '.plist')
        self.run = run

    def native_controls(self, config, save_config):
        return None, None

    def tailscale(self, config):
        try:
            value = self.run([config['tailscale'], 'status', '--json'], capture_output=True, text=True, timeout=4, check=True)
            status = json.loads(value.stdout)
            own = status['Self']
            if status['BackendState'] != 'Running' or own.get('Online') is not True:
                raise ValueError()
            if tail_ip(config['listen']) not in own.get('TailscaleIPs', []):
                raise ValueError()
            if config.get('self_node_id') and own.get('ID') != config['self_node_id']:
                raise ValueError()
            # Self metadata alone is insufficient: require the address on a
            # local tunnel interface before binding, and on every later check.
            interfaces = self.run(['/sbin/ifconfig', '-a'], capture_output=True, text=True, timeout=4, check=True)
            interface, flags = None, 0
            owners = []
            for line in interfaces.stdout.splitlines():
                header = re.match(r'^(\w+):\s+flags=([0-9a-fA-F]+)', line)
                if line and not line[0].isspace():
                    interface, flags = (header[1], int(header[2], 16)) if header else (None, 0)
                address = re.match(r'\s+inet\s+(\S+)', line)
                if address and address[1] == config['listen']:
                    owners.append((interface, flags))
            # IFF_UP (1) and IFF_RUNNING (0x40); reject stale/down tunnels and
            # duplicate addresses on another interface, including a LAN NIC.
            if len(owners) != 1 or not owners[0][0] or not re.fullmatch(r'utun\d+', owners[0][0]) or owners[0][1] & 0x41 != 0x41:
                raise ValueError()
            return status
        except Exception:
            raise ValueError('Tailscale is offline or the configured address/identity is not local.') from None

    def validate_network(self, config):
        tail_ip(config['listen'])
        if not isinstance(config.get('self_node_id'), str) or not config['self_node_id']:
            raise ValueError('The installed Tailscale node identity is required.')
        peers = config.get('allowed_peers')
        identities = config.get('peer_ids', {})
        if not isinstance(peers, list) or not peers or len(peers) != len(set(peers)):
            raise ValueError('An explicit peer allowlist is required.')
        for peer in peers:
            tail_ip(peer)
            if peer == config['listen'] or not isinstance(identities.get(peer), str) or not identities[peer]:
                raise ValueError('Each approved peer requires a distinct Tailscale node identity.')
        status = self.tailscale(config)
        for peer in peers:
            if not any(p.get('ID') == identities[peer] and peer in p.get('TailscaleIPs', []) for p in status.get('Peer', {}).values()):
                raise ValueError('Approved Tailscale peer identity could not be verified.')
        return status

    def authorize_peer(self, config, peer):
        if peer not in config['allowed_peers']:
            return False
        try:
            status = self.validate_network(config)
            return any(p.get('ID') == config['peer_ids'][peer] and p.get('Online') is True and peer in p.get('TailscaleIPs', []) for p in status.get('Peer', {}).values())
        except Exception:
            return False

    def launch_plist(self, python):
        return plistlib.dumps({'Label': LABEL, 'ProgramArguments': [str(python), '-I', str(self.root / 'app/macos_service.py'), '--config', str(self.root / 'config.json')],
            'RunAtLoad': True, 'KeepAlive': False, 'ProcessType': 'Background', 'Umask': 0o077,
            'WorkingDirectory': str(self.root), 'StandardOutPath': str(self.logs / 'launch.log'),
            'StandardErrorPath': str(self.logs / 'launch.log')})

    def launchctl(self, action):
        require_user()
        if action in ('start', 'stop'):
            self.validate_launch_agent()
        domain = 'gui/' + str(os.getuid())
        args = {'start': ['bootstrap', domain, str(self.plist)], 'stop': ['bootout', domain + '/' + LABEL],
                'status': ['print', domain + '/' + LABEL]}[action]
        if action == 'start':
            current = self.run(['/bin/launchctl', 'print', domain + '/' + LABEL], capture_output=True, text=True, timeout=15)
            if current.returncode == 0:
                if re.search(r'\bstate = running\b', current.stdout):
                    return True
                # A login-time Tailscale failure may leave a loaded, exited job.
                args = ['kickstart', domain + '/' + LABEL]
        result = self.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=15)
        if action == 'status':
            return result.returncode == 0 and bool(re.search(r'\bstate = running\b', result.stdout))
        if result.returncode:
            raise ValueError('LaunchAgent operation failed; inspect its local status before retrying.')
        return True

    def validate_launch_agent(self):
        private_path(self.root, True)
        launcher = json.loads(private_path(self.root / 'launcher.json').read_text())
        expected = {'platform': 'darwin', 'python': launcher.get('python'), 'label': LABEL,
                    'manager_url': 'http://127.0.0.1:18773/'}
        if launcher != expected or not isinstance(launcher['python'], str) or not Path(launcher['python']).is_absolute():
            raise ValueError('Invalid managed launcher metadata.')
        value = plistlib.loads(private_path(self.plist).read_bytes())
        if value != plistlib.loads(self.launch_plist(launcher['python'])):
            raise ValueError('LaunchAgent is not the managed bridge; refusing operation.')
        return launcher


class ComfyProcess:
    """Own only children started here; never kill an external instance or a saved PID."""
    def __init__(self, config, platform_adapter):
        self.config, self.platform = config, platform_adapter
        self.process = None
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.closed = threading.Event()
        self.backend = {'backend': 'unchecked', 'cpu_fallback': False}
        self.on_security_failure = lambda: None

    def verify_listener(self, required=True):
        """Inspect the actual port before sending HTTP; never adopt another PID."""
        # The monitor must keep checking Tailscale while Start owns the lock
        # for its dependency probe/startup loop. Start checks its own listener.
        if not self.lock.acquire(blocking=required):
            return True  # Monitor-only skip, never used to authorize HTTP.
        try:
            if not self.process or self.process.poll() is not None:
                return False
            try:
                result = self.platform.run(['/usr/sbin/lsof', '-nP', '-iTCP:8188', '-sTCP:LISTEN', '-Fpn'],
                                           capture_output=True, text=True, timeout=4)
                if result.returncode == 1 and not result.stdout:
                    return not required  # Child may still be loading; no HTTP yet.
                if result.returncode != 0:
                    raise ValueError()
                pid, listeners = None, []
                for line in result.stdout.splitlines():
                    if line.startswith('p'):
                        pid = line[1:]
                    elif line.startswith('n'):
                        listeners.append((pid, line[1:]))
                if not listeners or any(item != (str(self.process.pid), '127.0.0.1:8188') for item in listeners):
                    raise ValueError()
                return True
            except Exception:
                self.on_security_failure()
                self.stop()
                raise ValueError('ComfyUI listener ownership or loopback binding could not be verified.') from None
        finally:
            self.lock.release()

    def start(self, timeout=300):
        from comfy_startup import launch_spec, healthy
        with self.lock:
            if self.closed.is_set():
                raise ValueError('Managed ComfyUI service has stopped.')
            if self.config.get('comfy_url') != 'http://127.0.0.1:8188':
                raise ValueError('Managed ComfyUI requires the fixed loopback endpoint.')
            self.stopping.clear()
            command, cwd, url = launch_spec(self.config)
            if self.process and self.process.poll() is None:
                if self.healthy_owned():
                    return {'state': 'ready', **self.backend}
                raise ValueError('Managed ComfyUI is still starting.')
            with socket.socket() as sock:
                sock.settimeout(1)
                if sock.connect_ex(('127.0.0.1', int(url.rsplit(':', 1)[1]))) == 0:
                    raise ValueError('ComfyUI port belongs to an external instance; no process was started.')
            validate_comfy(cwd)
            self.backend = probe_python(command[0])
            # stop(final=True) can arrive while the bounded dependency probe
            # holds this lock. Never spawn a child after that stop request.
            if self.closed.is_set() or self.stopping.is_set():
                raise ValueError('ComfyUI startup was stopped.')
            command += ['--disable-all-custom-nodes', '--disable-api-nodes', '--disable-auto-launch']
            if self.backend['cpu_fallback']:
                command.append('--cpu')
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
            env['PYTHONIOENCODING'] = 'utf-8'
            # Raw third-party stdout may contain secrets. Keep only fixed diagnostics.
            self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, shell=False)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.stopping.is_set():
                    self.stop()
                    raise ValueError('ComfyUI startup was stopped.')
                if self.process.poll() is not None:
                    self.stop()
                    raise ValueError('ComfyUI exited during startup; verify dependencies and selected directories.')
                if self.verify_listener() and healthy(url):
                    return {'state': 'ready', **self.backend}
                time.sleep(.25)
            self.stop()
            raise ValueError('ComfyUI startup timed out; managed process stopped.')

    def stop(self, final=False):
        if final:
            self.closed.set()
        self.stopping.set()
        with self.lock:
            proc = self.process
            if proc and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
            self.process = None
            return {'state': 'stopped'}

    def healthy_owned(self):
        from comfy_startup import healthy
        return bool(self.verify_listener() and healthy(self.config['comfy_url']))
