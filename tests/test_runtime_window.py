"""The run window owns the container lifecycle, and must never touch the named volumes.

Docker Desktop is closed while the machine is used for other work, so unattended runs
start it themselves. Login state lives in the browser-data named volume, which is the
whole reason stopping the container is safe and `compose down -v` is not.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_runtime, runtime_window as rw
from scripts.check_runtime import Stop


def done(argv, code=0, out='', err=''):
    return subprocess.CompletedProcess(argv, code, out, err)


def fake_docker(engine=True, container='running', up_ok=True, stop_ok=True, volumes=rw.VOLUMES):
    """Scripted docker CLI: records argv and moves the container between states."""
    state = {'engine': engine, 'container': container}
    calls = []

    def run(command, seconds=180):
        argv = [str(part) for part in command]
        calls.append(argv)
        if argv[1] == 'info':
            return done(argv, 0 if state['engine'] else 1, '27.0.0' if state['engine'] else '', '')
        if 'volume' in argv:
            name = argv[argv.index('--filter') + 1].split('=', 1)[1]
            return done(argv, 0, name + '\n' if name in volumes else '')
        if 'ps' in argv:
            return done(argv, 0, 'cid\n' if state['container'] != 'absent' else '')
        if 'inspect' in argv:
            return done(argv, 0, state['container'] + '\n')
        if argv[1:3] == ['compose', 'up']:
            if up_ok:
                state['container'] = 'running'
            return done(argv, 0 if up_ok else 1, '', '' if up_ok else 'no such image')
        if argv[1:3] == ['compose', 'stop']:
            if stop_ok:
                state['container'] = 'exited'
            return done(argv, 0 if stop_ok else 1, '', '' if stop_ok else 'stop refused')
        return done(argv)

    return run, state, calls


class RunWindowTests(unittest.TestCase):
    def test_an_already_running_container_is_not_recreated(self):
        run, state, calls = fake_docker(container='running')
        with patch.object(rw, 'run', run):
            result = rw.up('docker', {})
        self.assertIn('container:already-running', result['steps'])
        self.assertEqual([c for c in calls if c[1:3] == ['compose', 'up']], [])
        self.assertEqual(result['volumes'], list(rw.VOLUMES))

    def test_a_stopped_container_is_started_and_waited_for(self):
        run, state, calls = fake_docker(container='exited')
        with patch.object(rw, 'run', run):
            result = rw.up('docker', {})
        self.assertIn('container:started', result['steps'])
        starts = [c for c in calls if c[1:3] == ['compose', 'up']]
        self.assertEqual(len(starts), 1)
        self.assertIn('--wait', starts[0])
        self.assertEqual(state['container'], 'running')

    def test_a_closed_engine_is_started_before_any_container_command(self):
        run, state, calls = fake_docker(engine=False, container='absent')
        marks = []

        def start(docker, env):
            marks.append('engine')
            state['engine'] = True
            return 'fixture'

        with patch.object(rw, 'run', run), patch.object(rw, 'start_engine', start):
            result = rw.up('docker', {})
        self.assertEqual(marks, ['engine'])
        self.assertIn('engine:ready', result['steps'])
        self.assertEqual(result['container'], 'running')
        self.assertEqual([c[1] for c in calls[:3]], ['info', 'info', 'compose'])

    def test_an_unreachable_engine_fails_closed_with_its_own_code(self):
        run, state, calls = fake_docker(engine=False)

        def never(docker, env):
            raise AssertionError('engine autostart must not happen when it is disabled')

        with patch.object(rw, 'run', run), patch.object(rw, 'start_engine', never):
            with self.assertRaises(rw.RuntimeUnavailable) as caught:
                rw.up('docker', {}, engine=False)
        self.assertEqual(caught.exception.code, 'engine_unavailable')
        self.assertEqual([c for c in calls if c[1:3] == ['compose', 'up']], [])

    def test_a_container_that_never_becomes_running_is_named(self):
        run, state, calls = fake_docker(container='exited', up_ok=False)
        with patch.object(rw, 'run', run):
            with self.assertRaises(rw.RuntimeUnavailable) as caught:
                rw.up('docker', {})
        self.assertEqual(caught.exception.code, 'container_unavailable')

    def test_shutdown_stops_the_container_and_never_deletes_volumes(self):
        run, state, calls = fake_docker(container='running')
        with patch.object(rw, 'run', run):
            result = rw.down('docker', {})
        self.assertIn('container:stopped', result['steps'])
        self.assertEqual(len([c for c in calls if c[1:3] == ['compose', 'stop']]), 1)
        self.assertEqual(state['container'], 'exited')
        self.assertEqual(result['volumes'], list(rw.VOLUMES))
        for argv in calls:
            self.assertEqual([part for part in argv if part in rw.FORBIDDEN], [], argv)

    def test_the_volume_guard_refuses_every_destroying_command(self):
        for command in (['docker', 'compose', 'down'],
                        ['docker', 'compose', 'down', '-v'],
                        ['docker', 'volume', 'rm', rw.VOLUMES[0]],
                        ['docker', 'system', 'prune', '-a', '--volumes']):
            with self.assertRaises(rw.RuntimeUnavailable) as caught:
                rw.guard(command)
            self.assertEqual(caught.exception.code, 'volume_guard')
        self.assertEqual(rw.guard(['docker', 'compose', 'stop', 'browser']),
                         ['docker', 'compose', 'stop', 'browser'])

    def test_keep_running_escape_valve_skips_the_stop(self):
        run, state, calls = fake_docker(container='running')
        with patch.object(rw, 'run', run), patch.dict(os.environ, {'WEIYANG_BROWSER_KEEP_RUNNING': '1'}):
            result = rw.down('docker', {})
        self.assertEqual(result['steps'], ['kept-running'])
        self.assertEqual(state['container'], 'running')
        self.assertEqual(calls, [])

    def test_stopping_a_closed_engine_is_reported_rather_than_attempted(self):
        run, state, calls = fake_docker(engine=False)
        with patch.object(rw, 'run', run):
            result = rw.down('docker', {})
        self.assertEqual(result['steps'], ['engine-already-stopped'])
        self.assertEqual([c for c in calls if c[1:3] == ['compose', 'stop']], [])

    def test_engine_quit_is_opt_in_because_only_a_validated_command_is_safe(self):
        sent = []

        def run(command, seconds=180):
            sent.append([str(part) for part in command])
            return done(command)

        with patch.object(rw, 'run', run), patch.object(rw, 'config', lambda: {}):
            self.assertEqual(rw.stop_engine('docker', {}), 'engine:left-running')
        self.assertEqual(sent, [])
        with patch.object(rw, 'run', run), \
             patch.object(rw, 'config', lambda: {'docker_desktop_stop_command': ['fixture-quit']}):
            self.assertEqual(rw.stop_engine('docker', {}), 'engine:stop-command-sent')
        self.assertEqual(sent, [['fixture-quit']])

    def test_status_reports_what_the_pre_run_checklist_needs(self):
        run, state, calls = fake_docker(container='running')
        with patch.object(rw, 'run', run):
            result = rw.status('docker', {})
        self.assertTrue(result['engine'])
        self.assertEqual(result['container'], 'running')
        self.assertEqual(result['volumes'], list(rw.VOLUMES))
        with patch.object(rw, 'run', fake_docker(container='running', volumes=())[0]):
            self.assertEqual(rw.status('docker', {})['volumes'], [])
        with patch.object(rw, 'run', fake_docker(engine=False)[0]):
            self.assertEqual(rw.status('docker', {}), {'engine': False, 'container': None, 'volumes': []})

    def test_a_missing_docker_cli_names_the_runtime_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rw.shutil, 'which', lambda name: None), \
                 patch.object(Path, 'home', staticmethod(lambda: Path(tmp))):
                with self.assertRaises(rw.RuntimeUnavailable) as caught:
                    rw.docker_cli()
        self.assertEqual(caught.exception.code, 'docker_cli_missing')


class RuntimePreflightTests(unittest.TestCase):
    """Only a proven absence may block a run; a broken probe must not invent one.

    The project root is redirected for every test here, so even a regressed
    implementation cannot write a drift record into the real private directory.
    """

    def setUp(self):
        check_runtime.RUNTIME_STATE.update(checked=False, result=None, failure=None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'local').mkdir(parents=True)
        root = patch.object(check_runtime, 'ROOT', self.root)
        root.start()
        self.addCleanup(root.stop)

    def records(self):
        return list((self.root / 'local/browser-image-drift').glob('runtime-*.json'))

    def test_a_proven_absence_blocks_and_records_the_fault(self):
        failure = {'ok': False, 'error': 'engine_unavailable', 'message': 'Docker engine is not reachable'}
        with patch.object(check_runtime, 'runtime_probe', lambda *a, **k: failure), \
             patch.object(check_runtime, 'bridge', lambda *a, **k: None):
            with self.assertRaises(Stop) as caught:
                check_runtime.browser_runtime_preflight()
            self.assertEqual(caught.exception.code, 'browser_runtime_unavailable')
            records = self.records()
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0].read_text(encoding='utf-8'))['error'], 'engine_unavailable')

    def test_a_container_the_bridge_still_reaches_is_not_blocked(self):
        failure = {'ok': False, 'error': 'container_unavailable', 'message': 'flaky probe'}
        with patch.object(check_runtime, 'runtime_probe', lambda *a, **k: failure), \
             patch.object(check_runtime, 'bridge', lambda *a, **k: {'match': True}):
            self.assertIsNone(check_runtime.browser_runtime_preflight())
        self.assertEqual(self.records(), [])

    def test_a_probe_that_cannot_report_does_not_block(self):
        with patch.object(check_runtime, 'runtime_probe', lambda *a, **k: None):
            self.assertIsNone(check_runtime.browser_runtime_preflight())
        self.assertEqual(self.records(), [])

    def test_a_healthy_runtime_returns_its_report_without_a_fault_record(self):
        healthy = {'ok': True, 'result': {'steps': ['container:already-running'], 'container': 'running'}}
        with patch.object(check_runtime, 'runtime_probe', lambda *a, **k: healthy):
            self.assertEqual(check_runtime.browser_runtime_preflight()['container'], 'running')
        self.assertEqual(self.records(), [])

    def test_the_probe_runs_once_per_process(self):
        seen = []

        def probe(*args, **kwargs):
            seen.append(args)
            return {'ok': True, 'result': {'container': 'running'}}

        with patch.object(check_runtime, 'runtime_probe', probe):
            check_runtime.browser_runtime_preflight()
            check_runtime.browser_runtime_preflight()
        self.assertEqual(seen, [('up',)])

    def test_the_runtime_probe_can_be_disabled_for_a_machine_that_keeps_docker_open(self):
        def explode(*args, **kwargs):
            raise AssertionError('the probe must not run when it is disabled')

        with patch.dict(os.environ, {'WEIYANG_BROWSER_RUNTIME': '0'}), \
             patch.object(check_runtime, 'runtime_probe', explode):
            self.assertIsNone(check_runtime.browser_runtime_preflight())


if __name__ == '__main__':
    unittest.main()
