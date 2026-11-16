"""Explicit, user-only launchd/systemd operations; importing performs no I/O."""
import configparser
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
from xml.parsers.expat import ExpatError
import tempfile


_ACTIONS = {'install', 'start', 'stop', 'status', 'uninstall'}
_STOP_SECONDS = 30


def _clean(value):
    value = str(value)
    if any(character in value for character in ('\n', '\r', '\x00')):
        raise ValueError('Service paths and values must not contain newlines or NUL bytes')
    return value


def _run(args, *, check=True):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=_STOP_SECONDS + 15)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f'{args[0]} timed out; inspect the user service manager before retrying') from error
    except FileNotFoundError as error:
        raise RuntimeError(f'{args[0]} is unavailable; use python3 jobapp.py start in the foreground') from error
    if check and result.returncode:
        raise RuntimeError(f'{shlex.join(args)} failed: {(result.stderr or result.stdout).strip()}')
    return result


def _quote(value, *, command=False):
    value = _clean(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if command:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def _words(value, *, command=False):
    values = shlex.split(value)
    return [item.replace('%%', '%').replace('$$', '$') if command else item.replace('%%', '%') for item in values]


def _identity(args, directory, root, role):
    if not isinstance(args, list) or len(args) < 3 or not all(isinstance(item, str) for item in args):
        return False
    if directory != str(root) or not Path(args[0]).is_absolute():
        return False
    if role == 'applications':
        return args[1:] == [str(root / 'src/campaign.py'), 'loop']
    return (len(args) == 4 and args[1:3] == [str(root / 'src/campaign_monitor.py'), '--port']
            and args[3].isdigit() and 1 <= int(args[3]) <= 65535)


def _owned_file(path, name, root, role, backend):
    if not path.exists() and not path.is_symlink():
        return False
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError(f'Refusing non-owned service file: {path}')
    try:
        if backend == 'launchd':
            spec = plistlib.loads(path.read_bytes())
            owned = (isinstance(spec, dict) and spec.get('Label') == name
                     and _identity(spec.get('ProgramArguments'), spec.get('WorkingDirectory'), root, role))
        else:
            text = path.read_text()
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.read_string(text)
            args = _words(parser['Service']['ExecStart'], command=True)
            directory = _words(parser['Service']['WorkingDirectory'])
            owned = (f'# jobapp-service: {name}\n' in text
                     and f'# jobapp-root: {json.dumps(str(root))}\n' in text
                     and len(directory) == 1 and _identity(args, directory[0], root, role))
    except (ValueError, TypeError, KeyError, configparser.Error, plistlib.InvalidFileException, ExpatError):
        owned = False
    if not owned:
        raise ValueError(f'Refusing unknown or foreign service file: {path}')
    return True


def _spec(root, runtime, role, name, backend):
    python = _clean(Path(sys.executable).resolve())
    requested = _clean(runtime['omp_executable'])
    if '/' in requested:
        executable = Path(requested).expanduser()
        if not executable.is_absolute():
            executable = root / executable
        omp = shutil.which(str(executable))
    else:
        omp = shutil.which(requested)
    if not omp:
        raise ValueError(f'Configured omp executable is unavailable: {requested}')
    args = [python, str(root / ('src/campaign.py' if role == 'applications' else 'src/campaign_monitor.py'))]
    args += ['loop'] if role == 'applications' else ['--port', str(runtime['dashboard_port'])]
    if not Path(args[1]).is_file():
        raise ValueError(f'Missing service entry point: {args[1]}')
    path = os.pathsep.join(dict.fromkeys([
        _clean(Path(omp).absolute().parent), str(Path(python).parent),
        '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin']))
    environment = {'PATH': path, 'PI_BROWSER_RELAY': '1', 'PYTHONUNBUFFERED': '1'}
    if backend == 'launchd':
        return plistlib.dumps({
            'Label': name, 'ProgramArguments': args, 'WorkingDirectory': str(root),
            'EnvironmentVariables': environment, 'RunAtLoad': True,
            'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 15,
            'ExitTimeOut': _STOP_SECONDS, 'AbandonProcessGroup': False,
            'StandardOutPath': str(root / 'logs/campaign' / f'{name}.stdout.log'),
            'StandardErrorPath': str(root / 'logs/campaign' / f'{name}.stderr.log'),
        }, sort_keys=True)
    lines = [f'# jobapp-service: {name}', f'# jobapp-root: {json.dumps(str(root))}',
             '[Unit]', f'Description=Job application {role}', '', '[Service]',
             'Type=simple', 'WorkingDirectory=' + _quote(root),
             'ExecStart=' + ' '.join(_quote(arg, command=True) for arg in args)]
    lines += ['Environment=' + ' '.join(_quote(f'{key}={value}') for key, value in environment.items())]
    lines += ['Restart=on-failure', 'RestartSec=15s', 'KillMode=mixed',
              f'TimeoutStopSec={_STOP_SECONDS}s', 'SendSIGKILL=yes', '',
              '[Install]', 'WantedBy=default.target', '']
    return '\n'.join(lines).encode()


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _launch_state(name, path, root, role, domain):
    result = _run(['launchctl', 'print', f'{domain}/{name}'], check=False)
    if result.returncode:
        # A missing job is normal; other launchctl failures must not masquerade as stopped.
        if any(message in (result.stderr + result.stdout).lower() for message in
               ('could not find service', 'could not find specified service', 'service not found')):
            return {'loaded': False, 'active': False, 'pid': None, 'owned': False}
        raise RuntimeError(f'Cannot inspect launchd service {name}: {(result.stderr or result.stdout).strip()}')
    text = result.stdout
    def field(key):
        match = re.search(r'^\s*' + re.escape(key) + r' = (.*?)\s*$', text, re.M)
        return match.group(1) if match else None
    directory = field('working directory')
    if directory == '"' + str(root) + '"':
        directory = str(root)
    block = re.search(r'^\s*arguments = \{\s*\n(.*?)^\s*\}', text, re.M | re.S)
    args = [line.strip().strip('"') for line in block.group(1).splitlines()] if block else []
    # launchctl prints arguments verbatim, including quotes that are part of a path.
    if block:
        raw = [line.strip() for line in block.group(1).splitlines()]
        if _identity(raw, directory, root, role):
            args = raw
    pid = field('pid')
    pid = int(pid) if pid and pid.isdigit() else None
    owned = field('path') in (str(path), '"' + str(path) + '"') and _identity(args, directory, root, role)
    return {'loaded': True, 'active': pid is not None, 'pid': pid, 'owned': owned,
            'state': field('state')}


def _linux_state(name, path, root, role):
    result = _run(['systemctl', '--user', 'show', name,
                   '--property=LoadState,ActiveState,SubState,MainPID,FragmentPath,WorkingDirectory,ExecStart'],
                  check=False)
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode and fields.get('LoadState') != 'not-found':
        raise RuntimeError(f'Cannot inspect systemd service {name}: {(result.stderr or result.stdout).strip()}')
    loaded = fields.get('LoadState') not in (None, 'not-found')
    active = fields.get('ActiveState') in ('active', 'activating', 'reloading', 'deactivating')
    pid = int(fields.get('MainPID') or 0) or None
    script = str(root / ('src/campaign.py' if role == 'applications' else 'src/campaign_monitor.py'))
    owned = (loaded and fields.get('FragmentPath') == str(path)
             and fields.get('WorkingDirectory') == str(root) and script in fields.get('ExecStart', ''))
    return {'loaded': loaded, 'active': active, 'pid': pid, 'owned': owned,
            'state': fields.get('SubState')}


def supervisor_running(root):
    try:
        handle = (root / 'data/campaign-worker.lock').open('r')
    except FileNotFoundError:
        return False
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def _check_singleton(root, state):
    if not supervisor_running(root):
        return
    try:
        runtime = json.loads((root / 'data/campaign-runtime.json').read_text())
    except (OSError, ValueError):
        runtime = {}
    if not (state['owned'] and state['active'] and state['pid']
            and isinstance(runtime, dict) and runtime.get('supervisor_pid') == state['pid']):
        raise RuntimeError('Another launcher holds the campaign singleton lock; stop that launcher before starting services')


def manage(action, root, campaign, replace=False):
    """Manage two independent services. Install does not start either service.

    Start never restarts an active job; inactive loaded launchd jobs use kickstart without -k.
    Stop and uninstall use manager TERM/grace/KILL cleanup, not campaign stop files.
    """
    if action not in _ACTIONS:
        raise ValueError(f'Unknown service action: {action}')
    root = Path(_clean(Path(root).expanduser().resolve()))
    runtime = campaign['runtime']
    namespace = _clean(runtime['service_name'])
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', namespace):
        raise ValueError('Invalid service namespace')
    if sys.platform == 'darwin':
        backend = 'launchd'
        directory = Path.home() / 'Library/LaunchAgents'
        names = [f'com.{namespace}.applications', f'com.{namespace}.application-monitor']
        domain = f'gui/{os.getuid()}'
    elif sys.platform.startswith('linux'):
        backend = 'systemd'
        directory = Path.home() / '.config/systemd/user'
        names = [f'{namespace}-applications.service', f'{namespace}-application-monitor.service']
        domain = None
        probe = _run(['systemctl', '--user', 'show', '--property=Version', '--value'], check=False)
        if probe.returncode:
            raise RuntimeError('User systemd is unavailable; log into a systemd user session, or use '
                               'python3 jobapp.py start in the foreground. ' + probe.stderr.strip())
    else:
        raise ValueError('User services require macOS launchd or Linux systemd; use the foreground CLI')
    roles = ['applications', 'application-monitor']
    paths = [directory / (name + '.plist' if backend == 'launchd' else name) for name in names]
    installed = [_owned_file(path, name, root, role, backend)
                 for path, name, role in zip(paths, names, roles)]
    states = [(_launch_state(name, path, root, role, domain) if backend == 'launchd'
               else _linux_state(name, path, root, role))
              for name, path, role in zip(names, paths, roles)]
    if action != 'status':
        for name, state in zip(names, states):
            if state['loaded'] and not state['owned']:
                raise ValueError(f'Refusing foreign loaded service: {name}')
    if action == 'install':
        contents = [_spec(root, runtime, role, name, backend) for role, name in zip(roles, names)]
        for path, exists, content in zip(paths, installed, contents):
            if exists and path.read_bytes() != content and not replace:
                raise ValueError(f'Owned service differs: {path}; use --replace to update it without restarting')
        if backend == 'launchd':
            (root / 'logs/campaign').mkdir(parents=True, exist_ok=True)
        for path, exists, content in zip(paths, installed, contents):
            if not exists or path.read_bytes() != content:
                _write(path, content)
        if backend == 'systemd':
            _run(['systemctl', '--user', 'daemon-reload'])
            _run(['systemctl', '--user', 'enable', *names])
        installed = [True, True]
    elif action == 'start':
        if not all(installed):
            raise ValueError('Services are not installed; run the service install command first')
        _check_singleton(root, states[0])
        if not states[0]['active']:
            (root / 'data/campaign.stop').unlink(missing_ok=True)
        for name, path, state in zip(names, paths, states):
            if backend == 'launchd' and not state['loaded']:
                _run(['launchctl', 'bootstrap', domain, str(path)])
            elif backend == 'launchd' and not state['active']:
                # No -k: never terminate/restart a job that became active since inspection.
                _run(['launchctl', 'kickstart', f'{domain}/{name}'])
            elif backend == 'systemd' and not state['active']:
                _run(['systemctl', '--user', 'start', name])
    elif action in ('stop', 'uninstall'):
        # Stop applications first so their bounded cleanup completes before the monitor exits.
        for name, path, state in zip(names, paths, states):
            if backend == 'launchd' and state['loaded']:
                _run(['launchctl', 'bootout', f'{domain}/{name}'])
            elif backend == 'systemd' and state['loaded']:
                _run(['systemctl', '--user', 'stop', name])
        if action == 'uninstall':
            if backend == 'systemd':
                owned_names = [name for name, exists in zip(names, installed) if exists]
                if owned_names:
                    _run(['systemctl', '--user', 'disable', *owned_names])
            for path, exists in zip(paths, installed):
                if exists:
                    path.unlink()
            if backend == 'systemd':
                _run(['systemctl', '--user', 'daemon-reload'])
            installed = [False, False]
    return {'action': action, 'backend': backend, 'root': str(root),
            'services': [{'name': name, 'path': str(path), 'installed': exists, **state}
                         for name, path, exists, state in zip(names, paths, installed, states)],
            'status_timing': 'before_action' if action != 'status' else 'current'}
