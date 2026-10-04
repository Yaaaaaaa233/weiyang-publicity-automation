"""Workflow recovery and mutation guards using synthetic articles only."""
import copy
from contextlib import closing
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import sync_workflow as w
from scripts import taskctl
from tests.test_sync_composition import inputs


def plan_input():
    _, plan = inputs()
    plan.update({'plan_confirmed': True, 'composer_url': 'https://xiumi.us/example-composer'})
    for article in plan['articles']:
        article['source_title'] = article['sync_title']
    plan['articles'][1]['sync_title'] = '测试头条组合标题'
    return plan


def snapshot_with_count(count, plan):
    snapshot, _ = inputs()
    snapshot['nodes'] = [n for n in snapshot['nodes'] if n.get('selector') == 'body > main' or
                         not n.get('selector', '').startswith('body > main > ')]
    snapshot['backgrounds'] = []
    for index, article in enumerate(w.ordered(plan)[:count]):
        path = 'body > main > div:nth-of-type(' + str(index + 1) + ')'
        snapshot['nodes'].extend([
            {'tag': 'div', 'class': 'x3-slice-plate', 'selector': path, 'rect': {'y': index * 100}},
            {'tag': 'input', 'class': 'inner', 'selector': path + ' > input', 'value': article['sync_title']}])
    return snapshot


class FakeBrowser:
    def __init__(self, plan, count=0):
        self.plan, self.data = plan, snapshot_with_count(count, plan)
        self.selected, self.edits = [], []

    def snapshot(self):
        return copy.deepcopy(self.data)

    def menu(self, plan):
        return self.snapshot()

    def search(self, title):
        return {'source_title': title}

    def click_text(self, candidate, title):
        assert candidate['source_title'] == title
        self.selected.append(title)
        count = len(w.title_controls(self.data)) + 1
        self.data = snapshot_with_count(count, self.plan)
        w.title_controls(self.data)[-1]['value'] = title

    def command(self, *args):
        if args[0] == 'type':
            self.edits.append(args[1])
            w.title_controls(self.data)[-1]['value'] = args[1]

    def wait(self, predicate):
        if not predicate(self.data):
            raise w.Stop('Fake page failed to settle.')
        return self.snapshot()


class WorkflowTests(unittest.TestCase):
    def test_unconfirmed_and_multiple_headlines_are_rejected(self):
        plan = plan_input(); plan['plan_confirmed'] = False
        with self.assertRaises(w.Stop): w.validate_plan(plan)
        plan = plan_input(); plan['articles'][0]['is_headline'] = True
        with self.assertRaises(w.Stop): w.validate_plan(plan)

    def test_foreign_composer_and_duplicate_source_versions_are_rejected(self):
        plan = plan_input(); plan['composer_url'] = 'https://example.com/other'
        with self.assertRaises(w.Stop): w.validate_plan(plan)
        plan = plan_input(); plan['articles'][0]['source_title'] = plan['articles'][1]['source_title']
        with self.assertRaises(w.Stop): w.validate_plan(plan)

    def test_same_account_day_key_survives_changed_titles(self):
        plan = plan_input(); changed = copy.deepcopy(plan)
        changed['articles'][0]['sync_title'] = '改名测试'
        self.assertEqual(w.job_key(plan), w.job_key(changed))

    def test_search_fallback_keeps_full_title_for_matching(self):
        self.assertEqual(w.search_queries('测试栏目 | 完整标题'), ['测试栏目 | 完整标题', '测试栏目'])
        self.assertEqual(w.search_queries('【测试标记】栏目丨完整标题'),
                         ['【测试标记】栏目丨完整标题', '【测试标记】'])
        self.assertEqual(w.search_queries('无分隔标题'), ['无分隔标题'])

    def test_collapsed_search_is_opened_and_waits_for_results(self):
        collapsed = snapshot_with_count(0, plan_input())
        collapsed['nodes'].append({'tag': 'span', 'class': 'tn-tpl-search-icon',
                                   'selector': 'body > aside > span', 'own_text': ''})
        opened = copy.deepcopy(collapsed)
        opened['nodes'].append({'tag': 'input', 'class': 'form-control',
                                'placeholder': '输入关键词后按回车键', 'selector': 'body > aside > input'})
        matched = copy.deepcopy(opened)
        matched['nodes'].extend([
            {'tag': 'div', 'class': 'article-container', 'selector': 'body > aside > div'},
            {'tag': 'div', 'class': 'title', 'selector': 'body > aside > div > div'},
            {'tag': 'div', 'class': 'inner', 'selector': 'body > aside > div > div > div', 'own_text': '测试稿1'}])
        class Search(w.Browser):
            def __init__(self): self.frames = iter((collapsed, opened, opened, matched, matched)); self.commands = []
            def snapshot(self): return next(self.frames)
            def command(self, *args): self.commands.append(args)
        browser = Search()
        with patch.object(w.time, 'sleep'):
            result = browser.search('测试稿1')
        self.assertEqual(result['own_text'], '测试稿1')
        self.assertEqual(browser.commands[0][0], 'click-element')
        self.assertEqual(sum(args[0] == 'type' for args in browser.commands), 1)

    def test_existing_lock_is_not_removed_or_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'workflow.lock'
            with w.runner_lock(path):
                with self.assertRaises(w.Stop):
                    with w.runner_lock(path): pass
                self.assertTrue(path.exists())
            self.assertFalse(path.exists())

    def test_assemble_promotes_headline_and_edits_only_composition_title(self):
        plan = plan_input(); browser = FakeBrowser(plan); events = []
        w.assemble(browser, plan, lambda phase, index: events.append((phase, index)))
        self.assertEqual(browser.selected, ['测试稿2', '测试稿1', '测试稿3'])
        self.assertEqual(browser.edits, ['测试头条组合标题'])
        self.assertEqual([n['value'] for n in w.title_controls(browser.data)],
                         [a['sync_title'] for a in w.ordered(plan)])
        self.assertEqual(sum(phase == 'selection_verified' for phase, _ in events), 3)

    def test_resume_does_not_reselect_confirmed_prefix(self):
        plan = plan_input(); browser = FakeBrowser(plan, count=1)
        w.assemble(browser, plan, lambda *args: None)
        self.assertEqual(browser.selected, ['测试稿1', '测试稿3'])
        self.assertEqual(browser.edits, [])

    def test_resume_repairs_only_recognized_pending_title(self):
        plan = plan_input(); browser = FakeBrowser(plan, count=1)
        w.title_controls(browser.data)[0]['value'] = '测试稿2'
        w.assemble(browser, plan, lambda *args: None)
        self.assertEqual(browser.edits, ['测试头条组合标题'])
        self.assertNotIn('测试稿2', browser.selected)

    def test_wrong_existing_selection_stops_before_any_selection(self):
        plan = plan_input(); browser = FakeBrowser(plan, count=1)
        w.title_controls(browser.data)[0]['value'] = '其他稿件'
        with self.assertRaises(w.Stop): w.assemble(browser, plan, lambda *args: None)
        self.assertEqual(browser.selected, [])
        self.assertEqual(browser.edits, [])

    def test_duplicate_library_cards_are_rejected_and_selected_cards_excluded(self):
        snapshot = snapshot_with_count(1, plan_input())
        def card(path):
            return [{'tag': 'div', 'class': 'article-container', 'selector': path},
                    {'tag': 'div', 'class': 'title', 'selector': path + ' > div'},
                    {'tag': 'div', 'class': 'inner', 'selector': path + ' > div > div', 'own_text': '测试稿1'}]
        snapshot['nodes'] += card('body > main > div:nth-of-type(1) > div')
        snapshot['nodes'] += card('body > aside > div:nth-of-type(1)')
        self.assertEqual(w.library_title(snapshot, '测试稿1')['selector'],
                         'body > aside > div:nth-of-type(1) > div > div')
        snapshot['nodes'] += card('body > aside > div:nth-of-type(2)')
        with self.assertRaises(w.Stop): w.library_title(snapshot, '测试稿1')

    def test_submission_intent_is_committed_before_replay_can_be_attempted(self):
        class Book:
            def __init__(self): self.state = {'phase': 'prepared'}; self.attempts = 0; self.records = []
            def current(self): return {'status': 'ready_for_review'}, self.attempts
            def checkpoint(self, status, phase, evidence, **extra):
                self.attempts = extra['sync_attempt_count']; self.records.append((status, phase))
                self.state.update({'phase': phase, **extra})
        book = Book()
        w.reserve_submission(book, [])
        self.assertEqual(book.records, [('verification_pending', 'submission_intent')])
        self.assertEqual(book.attempts, 1)
        with self.assertRaises(w.Stop): w.reserve_submission(book, [])

    def test_durable_history_blocks_resetting_attempt_counter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'data').mkdir()
            with patch.object(w, 'ROOT', root), patch.object(taskctl, 'ROOT', root):
                with closing(taskctl.connect(root / 'data/tasks.sqlite')) as db:
                    db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                               ('job', '{}', 'ready_for_review', 'prepared', '{}', 2, None, None, 0))
                    db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                               ('job', 1, 'checkpoint', json.dumps({'sync_attempt_count': 1}), 0))
                book = w.Ledger.__new__(w.Ledger)
                book.task_id, book.plan, book.state = 'job', plan_input(), {'phase': 'prepared', 'sync_attempt_count': 0}
                self.assertEqual(book.current()[1], 1)
                with self.assertRaises(w.Stop): w.reserve_submission(book, [])

    def test_new_run_name_cannot_bypass_existing_account_day(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); plan = plan_input(); (root / 'data').mkdir()
            source = {'workflow_job_key': w.job_key(plan), 'run_folder': 'local/sync-workflow/original',
                      'plan_sha256': w.digest(plan)}
            with patch.object(w, 'ROOT', root), patch.object(taskctl, 'ROOT', root):
                with closing(taskctl.connect(root / 'data/tasks.sqlite')) as db:
                    db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                               ('job', json.dumps(source), 'verification_pending', 'submission_intent', '{}', 1, None, None, 0))
                with self.assertRaises(w.Stop):
                    w.Ledger(root / 'local/sync-workflow/different', plan)

    def submit_cli_fixture(self, prior_attempts, execute=True, repeat=False):
        plan = plan_input(); plan['prior_sync_attempts'] = prior_attempts
        events, books = [], []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); run = root / 'local/sync-workflow/example'
            w.write(run / 'plan.json', plan)
            w.write(run / 'state.json', {'phase': 'prepared', 'handle': 'fixture', 'sync_attempt_count': prior_attempts})
            class Book:
                def __init__(self, folder, plan):
                    self.folder, self.plan, self.task_id = folder, plan, 'offline-fixture'
                    self.state = w.read(folder / 'state.json'); books.append(self)
                def current(self):
                    return {'status': self.state.get('status', 'ready_for_review'),
                            'workflow_submission_reserved': self.state.get('submission_reserved', False)}, self.state['sync_attempt_count']
                def claim(self): events.append('claim')
                def checkpoint(self, status, phase, evidence, **extra):
                    self.state.update({'status': status, 'phase': phase, **extra})
                    w.write(self.folder / 'state.json', self.state); events.append(phase)
                def finish(self): events.append('release')
            class Browser:
                def __init__(self, folder): self.evidence = []
                def command(self, *args): events.append(args[0]); return {}
                def menu(self, plan):
                    snapshot = snapshot_with_count(3, plan)
                    snapshot['backgrounds'] = inputs()[0]['backgrounds']
                    snapshot['nodes'].append({'tag': 'li', 'class': 'ok-btn', 'own_text': '开始同步',
                                              'selector': 'body > header > li'})
                    return snapshot
                def click_text(self, node, text):
                    events.append(('click_sync', books[-1].current()[1]))
                    raise w.Stop('Simulated uncertain dispatch.')
            args = SimpleNamespace(command='submit', run='example', execute=execute)
            with patch.object(w, 'ROOT', root), patch.object(w, 'Ledger', Book), patch.object(w, 'Browser', Browser):
                with self.assertRaises(w.Stop): w.run(args)
                if repeat:
                    with self.assertRaises(w.Stop): w.run(args)
            return events, books[-1].state

    def test_cli_existing_attempt_cannot_click_submit(self):
        events, state = self.submit_cli_fixture(1)
        self.assertFalse(any(isinstance(e, tuple) for e in events))
        self.assertEqual(state['sync_attempt_count'], 1)

    def test_cli_dispatch_failure_keeps_intent_and_next_run_does_not_click(self):
        events, state = self.submit_cli_fixture(0, repeat=True)
        self.assertEqual([e for e in events if isinstance(e, tuple)], [('click_sync', 1)])
        self.assertLess(events.index('submission_intent'), events.index(('click_sync', 1)))
        self.assertEqual(state['phase'], 'awaiting_confirmation')
        self.assertEqual(state['sync_attempt_count'], 1)
        self.assertTrue(state['submission_reserved'])

    def test_cli_without_execute_never_claims_or_operates_browser(self):
        events, state = self.submit_cli_fixture(0, execute=False)
        self.assertEqual(events, [])
        self.assertEqual(state['phase'], 'prepared')


if __name__ == '__main__':
    unittest.main()
