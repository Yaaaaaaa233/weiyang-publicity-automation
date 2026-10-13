"""One command must close the usage loop, and it must refuse an unattributable window.

docs/run-logging.md requires per-call records inside an explicit window that covers the
real model calls, so the collector has to carry the window and every turn through, and
must not import when the export did not succeed. Every run here is redirected to a
temporary project root so a unit test never litters the real private directories.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_runtime, collect_round_usage as c


def reply(code=0, out='', err=''):
    return subprocess.CompletedProcess([], code, out, err)


class RoundUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'repo'
        (self.root / 'local').mkdir(parents=True)
        self.session = Path(self.tmp.name) / 'session.v4.jsonl'
        self.session.write_text('{}\n', encoding='utf-8')
        self.commands = []

    def argv(self, *extra):
        return ['collect_round_usage.py', '--run', 'fixture-run', '--date', '2026-10-11',
                '--session', str(self.session), '--start', '2026-10-11T09:00:00+00:00',
                '--end', '2026-10-11T09:10:00+00:00', '--turn', '1', '--turn', '2', *extra]

    def fake_call(self, fail_export=False, fail_import=False):
        def call(command):
            self.commands.append([str(part) for part in command])
            if str(command[0]).endswith('dsh_usage_export.py'):
                if fail_export:
                    return reply(1, '', 'Call crosses execution boundary')
                output = Path(str(command[command.index('--output') + 1]))
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text('[]', encoding='utf-8')
                output.with_suffix('.meta.json').write_text(json.dumps({
                    'status': 'measured', 'requests': 0, 'turns': [1, 2],
                    'window_start': '2026-10-11T09:00:00+00:00',
                    'window_end': '2026-10-11T09:10:00+00:00'}), encoding='utf-8')
                return reply(0, '{"status": "measured"}')
            if fail_import:
                return reply(1, '', 'Usage predates this execution')
            return reply(0, json.dumps({'imported': 0, 'usage': {'status': 'unknown', 'requests': 0}}))
        return call

    def run_main(self, *extra, **kwargs):
        with patch.object(sys, 'argv', self.argv(*extra)), \
             patch.object(check_runtime, 'ROOT', self.root), \
             patch.object(c, 'call', self.fake_call(**kwargs)):
            return c.main()

    def test_the_export_carries_the_window_and_every_turn(self):
        payload, code = self.run_main()
        self.assertEqual(code, 0)
        export = self.commands[0]
        self.assertTrue(export[0].endswith('dsh_usage_export.py'))
        self.assertEqual(export[export.index('--start') + 1], '2026-10-11T09:00:00+00:00')
        self.assertEqual(export[export.index('--end') + 1], '2026-10-11T09:10:00+00:00')
        self.assertEqual([export[i + 1] for i, part in enumerate(export) if part == '--turn'], ['1', '2'])
        self.assertTrue(export[export.index('--session') + 1].endswith('session.v4.jsonl'))
        self.assertEqual(payload['export']['status'], 'measured')
        self.assertEqual(payload['export']['turns'], [1, 2])

    def test_the_import_targets_the_run_and_the_file_that_was_exported(self):
        payload, code = self.run_main()
        self.assertEqual(code, 0)
        export, imported = self.commands
        self.assertTrue(imported[0].endswith('run_log.py'))
        self.assertEqual(imported[1], 'usage')
        self.assertEqual(imported[imported.index('--run') + 1], 'fixture-run')
        self.assertEqual(imported[imported.index('--date') + 1], '2026-10-11')
        self.assertEqual(imported[imported.index('--file') + 1], export[export.index('--output') + 1])
        self.assertEqual(payload['import']['imported'], 0)
        self.assertEqual(payload['import']['usage']['status'], 'unknown')

    def test_the_exported_file_lands_in_a_private_directory(self):
        payload, code = self.run_main()
        self.assertEqual(code, 0)
        output = Path(self.commands[0][self.commands[0].index('--output') + 1])
        self.assertTrue(output.is_relative_to(self.root / 'local'), output)
        self.assertTrue(output.exists())

    def test_a_failing_export_is_never_imported(self):
        with self.assertRaises(RuntimeError):
            self.run_main(fail_export=True)
        self.assertEqual(len(self.commands), 1)

    def test_an_import_failure_is_not_swallowed(self):
        with self.assertRaises(RuntimeError):
            self.run_main(fail_import=True)
        self.assertEqual(len(self.commands), 2)

    def test_a_window_without_a_timezone_is_refused(self):
        for value in ('2026-10-11T09:00:00', '2026-10-11'):
            with self.assertRaises(ValueError):
                c.stamp(value)

    def test_a_window_that_ends_before_it_starts_is_refused(self):
        argv = ['collect_round_usage.py', '--run', 'fixture-run', '--date', '2026-10-11',
                '--session', str(self.session), '--start', '2026-10-11T09:10:00+00:00',
                '--end', '2026-10-11T09:00:00+00:00', '--turn', '1']
        with patch.object(sys, 'argv', argv), patch.object(c, 'call', self.fake_call()), \
             patch.object(check_runtime, 'ROOT', self.root):
            with self.assertRaises(ValueError):
                c.main()
        self.assertEqual(self.commands, [])

    def test_the_exported_file_must_land_in_a_private_directory(self):
        with patch.object(sys, 'argv', self.argv('--output', 'docs/usage.json')), \
             patch.object(c, 'call', self.fake_call()), \
             patch.object(check_runtime, 'ROOT', self.root):
            with self.assertRaises(ValueError):
                c.main()
        self.assertEqual(self.commands, [])

    def test_a_missing_session_log_is_refused(self):
        argv = ['collect_round_usage.py', '--run', 'fixture-run', '--date', '2026-10-11',
                '--session', str(Path(self.tmp.name) / 'absent.jsonl'),
                '--start', '2026-10-11T09:00:00+00:00', '--end', '2026-10-11T09:10:00+00:00',
                '--turn', '1']
        with patch.object(sys, 'argv', argv), patch.object(c, 'call', self.fake_call()):
            with self.assertRaises(ValueError):
                c.main()
        self.assertEqual(self.commands, [])

    def test_export_only_never_mutates_the_later_business_log(self):
        payload,code=self.run_main('--export-only',fail_import=True)
        self.assertEqual(code,0)
        self.assertEqual(payload['import']['status'],'not_requested')
        self.assertEqual(len(self.commands),1)
        self.assertTrue(self.commands[0][0].endswith('dsh_usage_export.py'))

    def test_export_only_still_refuses_a_crossing_call(self):
        with self.assertRaises(RuntimeError):self.run_main('--export-only',fail_export=True)
        self.assertEqual(len(self.commands),1)

    def test_a_session_log_inside_the_repository_must_be_private(self):
        with self.assertRaises(ValueError):
            c.session_path('scripts/collect_round_usage.py')
        self.assertTrue(c.session_path(str(self.session)))


if __name__ == '__main__':
    unittest.main()
