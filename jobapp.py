#!/usr/bin/env python3
"""Configure and run the local job-application campaign on macOS or Linux."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from settings import (ConfigError, DEFAULT_CAMPAIGN, DEFAULT_RUNTIME, atomic_write_json,
                      default_profile, load_campaign, load_profile, read_json,
                      resolve_path, validate_campaign, validate_profile)


def executable_path(config, root=ROOT):
    value = config['runtime']['omp_executable']
    if '/' in value or value.startswith('~'):
        value = str(resolve_path(value, root))
    found = shutil.which(value)
    if not found:
        raise ConfigError('omp executable is unavailable; install omp or configure campaign.runtime.omp_executable')
    return found


def _same_file_contents(left, right):
    if left.stat().st_size != right.stat().st_size:
        return False
    def digest(path):
        value = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(65536), b''):
                value.update(block)
        return value.digest()
    return digest(left) == digest(right)


def setup(args, root=ROOT):
    """Create missing configuration only; never reset a candidate or application ledger."""
    campaign_path, profile_path = root / 'data/campaign.json', root / 'profile.json'
    current_campaign = load_campaign(root) if campaign_path.exists() else validate_campaign(copy.deepcopy(DEFAULT_CAMPAIGN))
    current_profile = load_profile(root) if profile_path.exists() else default_profile()
    if args.profile:
        imported = validate_profile(read_json(Path(args.profile).expanduser()), root)
        if profile_path.exists() and imported != current_profile:
            raise ConfigError('profile.json already exists; setup will not overwrite a candidate. Edit it with config set or use a fresh project copy.')
        current_profile = imported
    destination = None
    source = None
    if args.resume:
        source = Path(args.resume).expanduser().resolve()
        try:
            with source.open('rb') as handle:
                if handle.read(5) != b'%PDF-':
                    raise ConfigError('--resume must be a local PDF')
        except OSError:
            raise ConfigError('--resume is missing or unreadable') from None
        destination = root / 'data/resume.pdf'
        if destination.exists() and not _same_file_contents(source, destination):
            raise ConfigError('data/resume.pdf already differs; setup will not replace the approved resume')
        if profile_path.exists() and current_profile.get('candidate', {}).get('resume_path') != 'data/resume.pdf':
            raise ConfigError('Existing profile uses a different resume path; configure it explicitly instead of replacing it during setup')
        current_profile.setdefault('candidate', {})['resume_path'] = 'data/resume.pdf'
    validate_profile(current_profile, root)
    import campaign
    with campaign.locked(root / 'data/configuration.lock'):
        # Recheck under the lock so two setup processes cannot overwrite each other.
        if profile_path.exists() and read_json(profile_path) != current_profile:
            raise ConfigError('profile.json changed; setup refused to overwrite it')
        if source is not None and destination.exists() and not _same_file_contents(source, destination):
            raise ConfigError('Approved resume changed; setup refused to overwrite it')
        if not campaign_path.exists():
            runtime = dict(DEFAULT_RUNTIME)
            suffix = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:8]
            runtime['service_name'] = 'jobapp-' + suffix
            runtime['credentials_file'] = '~/.omp/job-applications/' + runtime['service_name'] + '/credentials.json'
            current_campaign['runtime'] = runtime
            atomic_write_json(campaign_path, validate_campaign(current_campaign))
        if source is not None and not destination.exists():
            fd, temporary = tempfile.mkstemp(prefix='.resume-', dir=destination.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, 'wb') as output, source.open('rb') as original:
                    shutil.copyfileobj(original, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        if not profile_path.exists():
            atomic_write_json(profile_path, current_profile)
        (root / 'logs/campaign/receipts').mkdir(parents=True, exist_ok=True)
    try:
        load_profile(root, ready=True)
        ready = True
    except ConfigError:
        ready = False
    return {'configured': True, 'profile_ready': ready,
            'next': 'Run doctor, then start or service start.' if ready else 'Fill real candidate details, search queries and resume in profile.json, then run config validate.'}


def _reject_number(value):
    raise ValueError('non-finite JSON number')


def _parse_value(value):
    try:
        return json.loads(value, parse_constant=_reject_number)
    except json.JSONDecodeError:
        return value
    except ValueError:
        raise ConfigError('Configuration values must be finite JSON values') from None


def config_set(key, value, root=ROOT):
    parts = key.split('.')
    if len(parts) < 2 or parts[0] not in ('campaign', 'profile') or any(not part for part in parts):
        raise ConfigError('Use a dotted key starting with campaign. or profile.')
    path = root / ('data/campaign.json' if parts[0] == 'campaign' else 'profile.json')
    import campaign
    with campaign.locked(root / 'data/configuration.lock'):
        data = read_json(path)
        cursor = data
        for part in parts[1:-1]:
            if part not in cursor:
                cursor[part] = {}
            if not isinstance(cursor[part], dict):
                raise ConfigError('Configuration path crosses a non-object field')
            cursor = cursor[part]
        cursor[parts[-1]] = value
        if parts[0] == 'profile':
            if parts[1:] in (['preferences', 'salary_expectation_usd'], ['answers', 'salary_expectation_usd']):
                data.setdefault('preferences', {})['salary_expectation_usd'] = value
                data.setdefault('answers', {})['salary_expectation_usd'] = value
            validate_profile(data, root)
        else:
            data = validate_campaign(data)
        atomic_write_json(path, data)
    return {'updated': key, 'takes_effect': 'Next worker round; dashboard-port or service-name changes require an explicit service update.'}


def doctor(root=ROOT, offline=False):
    checks = []
    def add(name, status, detail):
        checks.append({'name': name, 'status': status, 'detail': detail})
    supported = sys.platform in ('darwin', 'linux') and sys.version_info >= (3, 10)
    add('platform', 'pass' if supported else 'fail', 'Requires Python 3.10+ on macOS or Linux')
    config = None
    try:
        config = load_campaign(root)
        load_profile(root, ready=True)
        add('configuration', 'pass', 'Campaign and candidate profile validated; local PDF is readable')
    except ConfigError as error:
        add('configuration', 'fail', str(error))
    if config:
        try:
            executable = executable_path(config, root)
            result = subprocess.run([executable, '--help'], capture_output=True, text=True, timeout=15)
            help_text = result.stdout + result.stderr
            flags = ('--print', '--wait-async', '--max-time', '--auto-approve')
            compatible = result.returncode == 0 and all(flag in help_text for flag in flags)
            add('omp', 'pass' if compatible else 'fail', 'Required unattended worker flags available' if compatible else 'Installed omp does not advertise the required worker flags')
        except (ConfigError, OSError, subprocess.SubprocessError) as error:
            add('omp', 'fail', str(error))
    try:
        import campaign
        table = campaign.process_table()
        add('process_supervision', 'pass' if os.getpid() in table else 'fail', 'POSIX process identities and process-group tooling available')
    except (OSError, RuntimeError) as error:
        add('process_supervision', 'fail', str(error))
    if offline:
        add('chrome_relay', 'warn', 'Offline check: relay connection not checked')
    else:
        try:
            with urllib.request.urlopen('http://127.0.0.1:9224/json/version', timeout=3) as response:
                relay = json.load(response)
            connected = isinstance(relay, dict) and not relay.get('error')
        except (OSError, ValueError, urllib.error.URLError):
            connected = False
        add('chrome_relay', 'pass' if connected else 'warn', 'Chrome relay connected' if connected else 'Run omp browser-relay and connect its Chrome extension; the supervisor waits safely while disconnected')
    add('model_authentication', 'warn', 'Not tested by doctor. Authenticate the configured provider in omp; no paid model request was made.')
    return {'ok': all(check['status'] != 'fail' for check in checks), 'checks': checks}


def start(args, root=ROOT):
    if sys.platform not in ('darwin', 'linux'):
        raise ConfigError('Supported local platforms are macOS and Linux')
    config = load_campaign(root)
    load_profile(root, ready=True)
    executable_path(config, root)
    import services
    if services.supervisor_running(root):
        print('Application supervisor is already running; nothing restarted.', flush=True)
        return 0
    import campaign
    import campaign_monitor
    server = None
    thread = None
    try:
        if not args.no_dashboard:
            server = campaign_monitor.make_server(config['runtime']['dashboard_port'])
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(f'Dashboard: http://127.0.0.1:{server.server_port}/', flush=True)
        (root / 'data/campaign.stop').unlink(missing_ok=True)
        print('Starting supervised applications. Ctrl-C stops this foreground run safely.', flush=True)
        campaign.loop()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join()
    return 0


def stop(root=ROOT):
    import services
    if not (root / 'data').is_dir() or not services.supervisor_running(root):
        return {'stop_requested': False, 'reason': 'No application supervisor is running'}
    fd = os.open(root / 'data/campaign.stop', os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    return {'stop_requested': True, 'note': 'Graceful worker cleanup and unresolved-submission quarantine remain enforced; the separate service dashboard stays available.'}


def status(root=ROOT):
    config = load_campaign(root)
    if not (root / 'data/jobs.db').exists():
        return {'confirmed': 0, 'target': config['target_total'], 'remaining': config['target_total'],
                'complete': config['target_total'] == 0,
                'runtime': {'state': 'complete' if config['target_total'] == 0 else 'not_started'}, 'activity': None}
    import campaign_monitor
    return campaign_monitor.read_status()


def _emit(value, as_json=False):
    if as_json:
        print(json.dumps(value, indent=2))
    elif isinstance(value, dict) and 'checks' in value:
        for check in value['checks']:
            print(f"{check['status'].upper():4} {check['name']}: {check['detail']}")
    else:
        print(json.dumps(value, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    initialize = commands.add_parser('setup', help='Create missing configuration safely; never reset existing state')
    initialize.add_argument('--profile', help='Import a reviewed profile JSON into a fresh project')
    initialize.add_argument('--resume', help='Copy a PDF into data/resume.pdf without overwriting a different resume')
    configure = commands.add_parser('config', help='Inspect and edit profile/campaign JSON settings')
    actions = configure.add_subparsers(dest='action', required=True)
    show = actions.add_parser('show')
    show.add_argument('section', choices=('campaign', 'profile'), nargs='?', default='campaign')
    setting = actions.add_parser('set')
    setting.add_argument('key', help='For example profile.preferences.min_salary_usd')
    setting.add_argument('value', help='A JSON value, or a plain string')
    actions.add_parser('validate')
    diagnostics = commands.add_parser('doctor', help='Check prerequisites without applying or reading credentials')
    diagnostics.add_argument('--offline', action='store_true')
    diagnostics.add_argument('--json', action='store_true')
    run = commands.add_parser('start', help='Run supervisor and dashboard in this terminal')
    run.add_argument('--no-dashboard', action='store_true')
    commands.add_parser('stop', help='Request a graceful application stop without deleting state')
    snapshot = commands.add_parser('status', help='Read campaign state without starting applications')
    snapshot.add_argument('--json', action='store_true')
    service = commands.add_parser('service', help='Manage launchd (macOS) or systemd --user (Linux)')
    service.add_argument('action', choices=('install', 'start', 'stop', 'status', 'uninstall'))
    service.add_argument('--replace', action='store_true', help='Explicitly replace an owned, changed service definition')
    credential = commands.add_parser('credential', help='Store one employer password using a hidden terminal prompt')
    credential.add_argument('site')
    credential.add_argument('--email', help='Defaults to the configured candidate email')
    args = parser.parse_args(argv)
    if sys.platform not in ('darwin', 'linux'):
        parser.error('Supported local platforms are macOS and Linux')
    try:
        if args.command == 'setup':
            _emit(setup(args))
        elif args.command == 'config':
            if args.action == 'show':
                _emit(load_campaign() if args.section == 'campaign' else load_profile(), True)
            elif args.action == 'set':
                _emit(config_set(args.key, _parse_value(args.value)))
            else:
                load_campaign()
                load_profile(ready=True)
                _emit({'valid': True})
        elif args.command == 'doctor':
            report = doctor(offline=args.offline)
            _emit(report, args.json)
            return 0 if report['ok'] else 1
        elif args.command == 'start':
            return start(args)
        elif args.command == 'stop':
            _emit(stop())
        elif args.command == 'status':
            _emit(status(), args.json)
        elif args.command == 'service':
            import services
            config = load_campaign()
            if args.action == 'start':
                load_profile(ready=True)
                executable_path(config)
            _emit(services.manage(args.action, ROOT, config, replace=args.replace))
        elif args.command == 'credential':
            config, profile = load_campaign(), load_profile()
            email = args.email or profile.get('candidate', {}).get('email')
            if not email:
                raise ConfigError('Provide --email or configure profile.candidate.email')
            return subprocess.call([sys.executable, str(ROOT / 'scripts/store_credential.py'), args.site, email],
                                   env={**os.environ, 'ATS_CREDENTIALS_FILE': str(resolve_path(config['runtime']['credentials_file']))})
    except (ConfigError, ValueError, RuntimeError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
