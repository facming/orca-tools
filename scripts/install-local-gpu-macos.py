"""Preview/install/manage a user LaunchAgent. Never installs ComfyUI or models."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sys

SOURCE = Path(__file__).resolve().parents[1] / 'services/local-gpu-bridge'
sys.path.insert(0, str(SOURCE))
from bridge_platform.macos import (MacOSPlatform, LABEL, environment, discover_python,
    validate_comfy, probe_python, private_dir, private_path, write_private, tail_ip, require_user)

FILES = ['bridge.py', 'comfy_startup.py', 'workflow_library.py', 'job_reuse.py', 'remote_privacy.py', 'macos_service.py']


def plan(settings, adapter):
    allowed = {'comfy_root', 'comfy_python', 'input_directory', 'output_directory', 'workflow_dirs', 'listen', 'allowed_peers', 'tailscale'}
    if set(settings) - allowed:
        raise ValueError('Unknown setting. Only ComfyUI and Tailscale settings are supported.')
    root = validate_comfy(settings['comfy_root'])
    python = discover_python(root, settings.get('comfy_python'))
    inputs = Path(settings.get('input_directory', root / 'input')).expanduser().resolve(strict=True)
    outputs = Path(settings.get('output_directory', root / 'output')).expanduser().resolve(strict=True)
    workflows = [Path(p).expanduser().resolve(strict=True) for p in settings.get('workflow_dirs', [root / 'user/default/workflows'])]
    if not workflows or not all(p.is_dir() for p in [inputs, outputs, *workflows]):
        raise ValueError('Input, output and workflow directories must already exist.')
    tailscale = settings.get('tailscale') or shutil.which('tailscale')
    if not tailscale or not Path(tailscale).is_absolute() or not os.access(tailscale, os.X_OK):
        raise ValueError('Select an existing absolute Tailscale CLI executable.')
    listen = tail_ip(settings['listen'])
    peers = settings['allowed_peers']
    if not isinstance(peers, list) or not peers:
        raise ValueError('An explicit peer allowlist is required.')
    for peer in peers:
        tail_ip(peer)
    return {'platform': 'darwin', 'data_dir': str(adapter.root), 'listen': listen,
        'allowed_peers': peers, 'tailscale': tailscale, 'port': 18772, 'admin_port': 18773,
        'comfy_url': 'http://127.0.0.1:8188', 'workflow_dirs': [str(p) for p in workflows],
        'comfy_startup': {'enabled': True, 'root': str(root), 'python': python,
                          'input_directory': str(inputs), 'output_directory': str(outputs)}}


def install(config, adapter, bridge_python=None):
    require_user()
    # This function is called only after the preview and explicit confirmation.
    if adapter.root.exists() or adapter.root.is_symlink() or adapter.plist.exists() or adapter.plist.is_symlink():
        raise ValueError('An installation or retained data already exists. Review it manually; it will not be overwritten.')
    backend = probe_python(config['comfy_startup']['python'])
    state = adapter.tailscale(config)
    config['self_node_id'] = state['Self']['ID']
    config['peer_ids'] = {}
    for peer in config['allowed_peers']:
        matches = [p['ID'] for p in state.get('Peer', {}).values() if peer in p.get('TailscaleIPs', []) and p.get('ID')]
        if len(matches) != 1:
            raise ValueError('An approved peer could not be uniquely identified on Tailscale.')
        config['peer_ids'][peer] = matches[0]
    adapter.validate_network(config)
    os.umask(0o077)
    for directory in (adapter.root, adapter.logs, adapter.cache, adapter.root / 'app', adapter.root / 'app/bridge_platform', adapter.root / 'jobs', adapter.root / 'workflows'):
        private_dir(directory)
    for name in FILES:
        write_private(adapter.root / 'app' / name, (SOURCE / name).read_bytes())
    for name in ('__init__.py', 'base.py', 'macos.py', 'windows.py'):
        write_private(adapter.root / 'app/bridge_platform' / name, (SOURCE / 'bridge_platform' / name).read_bytes())
    token = secrets.token_hex(32)
    write_private(adapter.root / 'token', token.encode())
    write_private(adapter.root / 'config.json', json.dumps(config, indent=2).encode())
    python = str(Path(bridge_python or sys.executable).absolute())
    write_private(adapter.root / 'launcher.json', json.dumps({'platform': 'darwin', 'python': python, 'label': LABEL, 'manager_url': 'http://127.0.0.1:18773/'}).encode())
    write_private(adapter.root / 'client.local.json', json.dumps({'url': 'http://' + config['listen'] + ':18772', 'token': token, 'require_private_tailnet_metadata': True}).encode())
    # LaunchAgents may already be 0755; do not change a shared user directory.
    adapter.plist.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(p.is_symlink() for p in (adapter.plist.parent, *adapter.plist.parents)):
        raise ValueError('LaunchAgents directory must not contain symbolic links.')
    info = adapter.plist.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('LaunchAgents directory has unsafe ownership or permissions.')
    fd = os.open(adapter.plist, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(adapter.launch_plist(python))
    result = adapter.run(['/usr/bin/plutil', '-lint', str(adapter.plist)], capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('LaunchAgent validation failed; service was not started.')
    adapter.launchctl('start')
    return backend


def uninstall(adapter):
    # Remove service registration only; keep all models, workflows, tokens, jobs and logs.
    require_user()
    adapter.validate_launch_agent()
    targets = [adapter.plist, adapter.root / 'launcher.json']
    def identity(file):
        info = private_path(file).stat()
        return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
    approved = [identity(file) for file in targets]
    if adapter.launchctl('status'):
        adapter.launchctl('stop')
    else:
        # A loaded but stopped agent must also be removed before deleting its plist.
        result = adapter.run(['/bin/launchctl', 'print', 'gui/' + str(os.getuid()) + '/' + LABEL], capture_output=True, timeout=15)
        if result.returncode == 0:
            adapter.launchctl('stop')
    adapter.validate_launch_agent()
    if [identity(file) for file in targets] != approved:
        raise ValueError('Managed service files changed during uninstall; no files were removed.')
    for file in targets:
        file.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'start', 'stop', 'status', 'logs', 'uninstall'])
    parser.add_argument('--settings', help='JSON settings; no token. Omit to choose paths interactively.')
    parser.add_argument('--confirm', action='store_true', help='Apply the printed install/uninstall preview.')
    args = parser.parse_args()
    try:
        environment()
        adapter = MacOSPlatform()
        if args.action == 'install':
            if args.settings:
                settings = json.loads(Path(args.settings).read_text())
            else:
                settings = {'comfy_root': input('Trusted existing ComfyUI root: ').strip(),
                    'comfy_python': input('Existing ComfyUI Python executable: ').strip(),
                    'listen': input('This Mac Tailscale IPv4: ').strip(),
                    'allowed_peers': input('Approved peer Tailscale IPv4 addresses, comma-separated: ').strip().split(',')}
            config = plan(settings, adapter)
            print('Preview: use the selected trusted ComfyUI/Python; probe dependencies and MPS; create private bridge app/config/token and client export; register a user LaunchAgent. ComfyUI starts only after a local Start action. Custom/API nodes are disabled. No models, environments or network rules will be installed.')
            print(json.dumps({k: v for k, v in config.items() if k in ('comfy_startup', 'workflow_dirs')}, indent=2))
            print('Destinations: ~/Library/Application Support/LocalGpuBridge, ~/Library/Logs/LocalGpuBridge, ~/Library/Caches/LocalGpuBridge, ~/Library/LaunchAgents/' + LABEL + '.plist')
            if not args.confirm:
                print('Preview only. Review selected trusted code, then repeat with --confirm.')
                return
            print(json.dumps(install(config, adapter)))
            print('Installed. Open http://127.0.0.1:18773/ to start ComfyUI. Transfer client.local.json privately to the VPS after approval; never paste its token into a command.')
        elif args.action == 'uninstall':
            print('Preview: stop the managed service/ComfyUI, remove only its LaunchAgent and launcher.json. Retain app, config/token, jobs, logs, cache, ComfyUI, models and workflows. Reinstallation over retained data is refused.')
            if args.confirm:
                uninstall(adapter)
                print('Service uninstalled; retained data requires separate manual review.')
        elif args.action == 'logs':
            # Logs contain fixed diagnostics only, never raw third-party output.
            file = adapter.logs / 'bridge.log'
            print(private_path(file).read_text()[-16000:])
        elif args.action == 'status':
            print(json.dumps({'launch_agent_running': adapter.launchctl('status'), 'note': 'GPU ready requires the manager health check, including live ComfyUI.'}))
        else:
            adapter.launchctl(args.action)
            print('LaunchAgent ' + args.action + ' completed.')
    except Exception:
        print('Operation failed. Check selected paths, private permissions, Python dependencies, LaunchAgent and Tailscale. No automatic repair or privilege escalation was attempted.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
