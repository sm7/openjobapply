"""Filesystem/backend behavior fixtures; never invoke native service managers.

Run: python3 -m unittest discover -s test -p 'test_services.py'
"""
import configparser
import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import services
from settings import DEFAULT_RUNTIME


class Manager:
    """A service-manager transport fixture, not an application process."""
    def __init__(self, root, home, platform):
        self.root, self.home, self.platform = root, home, platform
        self.calls = []
        self.loaded = {}
        self.failure = None
        self.available = True

    def run(self, args, **kwargs):
        self.calls.append(args)
        if self.failure and self.failure in args:
            return subprocess.CompletedProcess(args, 1, '', 'fixture manager failure')
        if args[0] == 'launchctl':
            action = args[1]
            if action == 'print':
                name = args[2].split('/')[-1]
                if name not in self.loaded:
                    return subprocess.CompletedProcess(args, 113, '', 'Could not find service')
                path, pid = self.loaded[name]
                spec = plistlib.loads(path.read_bytes())
                text = (f'path = {path}\nworking directory = {spec["WorkingDirectory"]}\n'
                        + 'arguments = {\n' + '\n'.join(spec['ProgramArguments'])
                        + '\n}\nstate = running\n' + (f'pid = {pid}\n' if pid else ''))
                return subprocess.CompletedProcess(args, 0, text, '')
            if action == 'bootstrap':
                path = Path(args[3])
                spec = plistlib.loads(path.read_bytes())
                self.loaded[spec['Label']] = (path, 423 + len(self.loaded))
            elif action == 'kickstart':
                name = args[2].split('/')[-1]
                path, _ = self.loaded[name]
                self.loaded[name] = (path, 423)
            elif action == 'bootout':
                self.loaded.pop(args[2].split('/')[-1], None)
        else:
            action = args[2]
            if not self.available:
                return subprocess.CompletedProcess(args, 1, '', 'Failed to connect to bus')
            if action == 'show' and '--value' in args:
                return subprocess.CompletedProcess(args, 0, 'fixture-version\n', '')
            if action == 'show':
                name = args[3]
                path = self.home / '.config/systemd/user' / name
                if not path.exists():
                    return subprocess.CompletedProcess(args, 1, 'LoadState=not-found\n', '')
                parser = configparser.ConfigParser(interpolation=None)
                parser.read(path)
                # Manager output exposes expanded working directory/command, not source escapes.
                role = 'applications' if name.endswith('-applications.service') else 'application-monitor'
                script = self.root / ('src/campaign.py' if role == 'applications' else 'src/campaign_monitor.py')
                pid = self.loaded.get(name, (None, 0))[1]
                text = (f'LoadState=loaded\nActiveState={"active" if pid else "inactive"}\n'
                        f'SubState={"running" if pid else "dead"}\nMainPID={pid}\n'
                        f'FragmentPath={path}\nWorkingDirectory={self.root}\nExecStart={script}\n')
                return subprocess.CompletedProcess(args, 0, text, '')
            if action == 'start':
                name = args[3]
                self.loaded[name] = (self.home / '.config/systemd/user' / name, 423 + len(self.loaded))
            elif action == 'stop':
                self.loaded.pop(args[3], None)
        return subprocess.CompletedProcess(args, 0, '', '')

    def mutations(self):
        return [call for call in self.calls if (call[0] == 'launchctl' and call[1] != 'print')
                or (call[0] == 'systemctl' and call[2] != 'show')]


class ServicesTests(unittest.TestCase):
    def fixture(self, platform='darwin', special=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        root = base / ('project space % "quote" $money' if special else 'project')
        home = base / ('home space % "quote"' if special else 'home')
        (root / 'src').mkdir(parents=True)
        home.mkdir()
        for name in ('campaign.py', 'campaign_monitor.py'):
            (root / 'src' / name).write_text('# fixture entry point, never executed\n')
        campaign = {'runtime': {**DEFAULT_RUNTIME, 'service_name': 'fixture', 'dashboard_port': 9876}}
        manager = Manager(root, home, platform)
        for context in (patch.object(services.sys, 'platform', platform),
                        patch.object(services.Path, 'home', return_value=home),
                        patch.object(services.shutil, 'which', return_value=str(base / 'omp space/omp')),
                        patch.object(services.subprocess, 'run', side_effect=manager.run)):
            context.start()
            self.addCleanup(context.stop)
        return root, campaign, manager

    def install(self, platform='darwin', special=False):
        root, campaign, manager = self.fixture(platform, special)
        result = services.manage('install', root, campaign)
        paths = [Path(item['path']) for item in result['services']]
        return root, campaign, manager, paths

    def snapshot(self, root):
        return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in root.rglob('*') if path.is_file()}

    def test_launchd_install_is_not_start_and_repeat_preserves_files(self):
        root, campaign, manager, paths = self.install(special=True)
        before = self.snapshot(manager.home)
        services.manage('install', root, campaign)
        self.assertEqual(self.snapshot(manager.home), before)
        self.assertFalse(manager.mutations())
        for path in paths:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            spec = plistlib.loads(path.read_bytes())
            self.assertEqual(spec['KeepAlive'], {'SuccessfulExit': False})
            self.assertFalse(spec['AbandonProcessGroup'])
            self.assertNotIn('credentials', str(spec))

    def test_systemd_special_paths_survive_install_ownership_and_start(self):
        root, campaign, manager, paths = self.install('linux', special=True)
        before = self.snapshot(manager.home)
        services.manage('install', root, campaign)
        self.assertEqual(self.snapshot(manager.home), before)
        self.assertFalse(any('start' in call for call in manager.calls))
        result = services.manage('status', root, campaign)
        self.assertTrue(all(item['owned'] for item in result['services']))
        services.manage('start', root, campaign)
        self.assertEqual(len(manager.loaded), 2)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(paths[0])
        self.assertEqual(parser['Service']['Restart'], 'on-failure')
        self.assertEqual(parser['Service']['KillMode'], 'mixed')
        self.assertEqual(parser['Service']['SendSIGKILL'], 'yes')

    def test_status_never_creates_state_or_removes_stop(self):
        root, campaign, manager = self.fixture()
        (root / 'data').mkdir()
        (root / 'data/campaign.stop').write_text('manual recovery')
        before = self.snapshot(root.parent)
        result = services.manage('status', root, campaign)
        self.assertFalse(any(item['loaded'] for item in result['services']))
        self.assertEqual(self.snapshot(root.parent), before)
        self.assertFalse(manager.mutations())

    def test_start_is_idempotent_with_inflight_owned_supervisor(self):
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform):
                root, campaign, manager, _ = self.install(platform)
                services.manage('start', root, campaign)
                (root / 'data').mkdir()
                lock = (root / 'data/campaign-worker.lock').open('w')
                self.addCleanup(lock.close)
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                (root / 'data/campaign-runtime.json').write_text(json.dumps({'supervisor_pid': 423}))
                (root / 'data/campaign.stop').write_text('do not disturb in-flight worker')
                manager.calls.clear()
                services.manage('start', root, campaign)
                self.assertFalse(manager.mutations())
                self.assertTrue((root / 'data/campaign.stop').exists())

    def test_active_foreground_launcher_refuses_all_service_starts(self):
        root, campaign, manager, _ = self.install()
        (root / 'data').mkdir()
        lock = (root / 'data/campaign-worker.lock').open('w')
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manager.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'singleton lock'):
            services.manage('start', root, campaign)
        self.assertFalse(manager.mutations())
        self.assertFalse((root / 'data/campaign-runtime.json').exists())

    def test_explicit_start_clears_stop_only_when_starting_application(self):
        root, campaign, manager, paths = self.install()
        (root / 'data').mkdir()
        stop = root / 'data/campaign.stop'
        stop.write_text('stopped')
        manager.loaded['com.fixture.applications'] = (paths[0], 0)
        services.manage('status', root, campaign)
        self.assertTrue(stop.exists())
        manager.calls.clear()
        services.manage('start', root, campaign)
        self.assertFalse(stop.exists())
        self.assertEqual(manager.loaded['com.fixture.applications'][1], 423)
        self.assertIn(['launchctl', 'kickstart', f'gui/{os.getuid()}/com.fixture.applications'],
                      manager.mutations())

    def test_foreign_loaded_job_cannot_be_controlled_via_owned_disk_file(self):
        root, campaign, manager, paths = self.install()
        foreign = root / 'foreign.plist'
        spec = plistlib.loads(paths[0].read_bytes())
        spec['WorkingDirectory'] = '/other/project'
        spec['ProgramArguments'][1] = '/other/project/src/campaign.py'
        foreign.write_bytes(plistlib.dumps(spec))
        manager.loaded['com.fixture.applications'] = (foreign, 999)
        manager.calls.clear()
        for action in ('start', 'stop', 'uninstall', 'install'):
            with self.assertRaisesRegex(ValueError, 'foreign loaded'):
                services.manage(action, root, campaign, replace=True)
        self.assertFalse(manager.mutations())
        self.assertTrue(all(path.exists() for path in paths))

    def test_linux_foreign_unit_refuses_replacement_and_uninstall(self):
        root, campaign, manager, paths = self.install('linux')
        content = paths[0].read_text().replace(str(root), '/foreign/project')
        paths[0].write_text(content)
        before = self.snapshot(manager.home)
        manager.calls.clear()
        for action in ('install', 'uninstall'):
            with self.assertRaisesRegex(ValueError, 'foreign'):
                services.manage(action, root, campaign, replace=True)
        self.assertEqual(self.snapshot(manager.home), before)
        self.assertFalse(manager.mutations())

    def test_malformed_plist_is_an_owned_file_refusal_not_a_parser_crash(self):
        root, campaign, manager, paths = self.install()
        paths[0].write_text('<?xml version=\"1.0\"?><plist><dict>')
        manager.calls.clear()
        with self.assertRaisesRegex(ValueError, 'foreign'):
            services.manage('install', root, campaign, replace=True)
        self.assertFalse(manager.calls)

    def test_owned_difference_requires_replace_without_restarting(self):
        root, campaign, manager, paths = self.install()
        services.manage('start', root, campaign)
        before = [path.read_bytes() for path in paths]
        campaign['runtime']['dashboard_port'] = 9888
        manager.calls.clear()
        with self.assertRaisesRegex(ValueError, '--replace'):
            services.manage('install', root, campaign)
        self.assertEqual([path.read_bytes() for path in paths], before)
        services.manage('install', root, campaign, replace=True)
        self.assertEqual(len(manager.loaded), 2)
        self.assertFalse(manager.mutations())
        self.assertEqual(plistlib.loads(paths[1].read_bytes())['ProgramArguments'][-1], '9888')

    def test_foreign_second_file_prevents_partial_install_even_with_replace(self):
        root, campaign, manager = self.fixture()
        directory = manager.home / 'Library/LaunchAgents'
        directory.mkdir(parents=True)
        foreign = directory / 'com.fixture.application-monitor.plist'
        foreign.write_bytes(plistlib.dumps({'Label': 'com.fixture.application-monitor',
                                           'WorkingDirectory': '/elsewhere',
                                           'ProgramArguments': ['/usr/bin/python3', '/elsewhere/src/campaign_monitor.py', '--port', '8765']}))
        before = self.snapshot(manager.home)
        for replace in (False, True):
            with self.assertRaisesRegex(ValueError, 'foreign'):
                services.manage('install', root, campaign, replace=replace)
        self.assertEqual(self.snapshot(manager.home), before)
        self.assertFalse(manager.calls)

    def test_symlink_never_counts_as_owned_even_if_target_matches(self):
        root, campaign, manager, paths = self.install()
        target = root / 'saved.plist'
        target.write_bytes(paths[0].read_bytes())
        paths[0].unlink()
        paths[0].symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'non-owned'):
            services.manage('uninstall', root, campaign)
        self.assertTrue(target.exists())
        self.assertTrue(paths[1].exists())

    def test_uninstall_stops_owned_services_preserves_unrelated_files(self):
        root, campaign, manager, paths = self.install('linux')
        services.manage('start', root, campaign)
        unrelated = paths[0].parent / 'another.service'
        unrelated.write_text('foreign')
        services.manage('uninstall', root, campaign)
        self.assertFalse(manager.loaded)
        self.assertFalse(any(path.exists() for path in paths))
        self.assertEqual(unrelated.read_text(), 'foreign')
        commands = [call[2] for call in manager.mutations()]
        self.assertEqual(commands[-4:], ['stop', 'stop', 'disable', 'daemon-reload'])

    def test_stop_uses_bounded_manager_cleanup_without_stopfile_changes(self):
        root, campaign, manager, paths = self.install()
        services.manage('start', root, campaign)
        (root / 'data').mkdir()
        stop = root / 'data/campaign.stop'
        stop.write_text('preserve')
        manager.calls.clear()
        services.manage('stop', root, campaign)
        self.assertFalse(manager.loaded)
        self.assertTrue(all(path.exists() for path in paths))
        self.assertEqual(stop.read_text(), 'preserve')
        self.assertEqual([call[1] for call in manager.mutations()], ['bootout', 'bootout'])

    def test_manager_failure_is_not_reported_as_success(self):
        root, campaign, manager, _ = self.install()
        manager.failure = 'bootstrap'
        with self.assertRaisesRegex(RuntimeError, 'fixture manager failure'):
            services.manage('start', root, campaign)
        self.assertFalse(manager.loaded)

    def test_status_permission_failure_is_not_stopped(self):
        root, campaign, manager = self.fixture()
        manager.failure = 'print'
        with self.assertRaisesRegex(RuntimeError, 'Cannot inspect launchd'):
            services.manage('status', root, campaign)

    def test_linux_no_user_manager_has_actionable_nonmutating_failure(self):
        root, campaign, manager = self.fixture('linux')
        manager.available = False
        before = self.snapshot(root.parent)
        with self.assertRaisesRegex(RuntimeError, 'foreground'):
            services.manage('install', root, campaign)
        self.assertEqual(self.snapshot(root.parent), before)
        self.assertFalse(manager.mutations())

    def test_newline_root_and_omp_are_rejected(self):
        root, campaign, manager = self.fixture()
        with self.assertRaisesRegex(ValueError, 'newlines'):
            services.manage('install', root / 'bad\nroot', campaign)
        campaign['runtime']['omp_executable'] = 'omp\nEnvironment=evil'
        with self.assertRaisesRegex(ValueError, 'newlines'):
            services.manage('install', root, campaign)
        self.assertFalse(manager.mutations())

    def test_start_does_not_install_implicitly(self):
        root, campaign, manager = self.fixture()
        with self.assertRaisesRegex(ValueError, 'not installed'):
            services.manage('start', root, campaign)
        self.assertFalse(manager.mutations())


if __name__ == '__main__':
    unittest.main()
