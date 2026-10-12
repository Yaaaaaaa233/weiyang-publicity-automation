"""Regression tests for the library-completeness gate (2026-10-11 field failure).

On 2026-10-11 the unattended acceptance round stopped with

    Stop('incomplete_library', 'Collected count 232 differs from displayed total 231')

after scanning the whole library.  The evidence shows the direction was EXCESS, not
a shortfall: 223 linked manuscripts (all ids unique) plus 9 unlinked restore-marked
slots, against a page counter of 231.  For the four days on record the counter equals
linked + 8, and 10-11 was the only day with a repeated unlinked title.  One unlinked
card had been rendered twice across the page-16/17 boundary, and because unlinked
cards carry page-scoped synthetic ids the repeat could never be caught by id.

These tests pin the asymmetric rule that replaces it, and pin the guards that must
NOT be weakened.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.check_runtime import Stop
from scripts import xiumi_scan


def card(id, title, available=True):
    return {'id': id, 'title': title, 'available': available, 'url': None if not available else 'u'}


def day_20261011():
    """The real 10-11 shape: 223 linked, 9 unlinked, one unlinked title repeated."""
    linked = [card('d%03d' % i, '真稿 %03d' % i) for i in range(223)]
    unlinked = [card('unavailable-slot-16-0', '未央科协招新 | xx组（头图附后）', False),
                card('unavailable-slot-16-1', '未央TMS招新 | XX组（各部门头图附后版）', False),
                card('unavailable-slot-17-0', '未央TMS招新 | XX组（各部门头图附后版）', False),
                card('unavailable-slot-17-1', '招新 | 未央TMS招新', False),
                card('unavailable-slot-17-2', '招新 | 未央科协招新', False),
                card('unavailable-slot-17-3', '未央团委招新 | 实践组', False),
                card('unavailable-slot-17-4', '招新 | 未央书院团委招新', False),
                card('unavailable-slot-17-5', '未央学生会招新 | XX部', False),
                card('unavailable-slot-17-6', '招新 | 未央书院学生会招新', False)]
    return linked + unlinked


def library_snapshot(total, page, card_titles):
    """Minimal page snapshot: nickname, counter, pager, and unlinked restore cards."""
    nodes = [{'selector': '#lib', 'class': 'nickname', 'own_text': 'expected', 'tag': 'span'},
             {'selector': '#lib > counter', 'class': '', 'own_text': '已保存图文:%d' % total, 'tag': 'div'},
             {'selector': '#lib > pager', 'class': 'pagination', 'own_text': '', 'tag': 'ul'},
             {'selector': '#lib > pager > active', 'class': '', 'parent_class': 'active',
              'own_text': str(page), 'tag': 'a'}]
    for i, title in enumerate(card_titles):
        base = '#lib > card%d' % i
        nodes.extend([{'selector': base, 'class': 'show-item', 'own_text': '', 'tag': 'div'},
                      {'selector': base + ' > title', 'class': 'title', 'own_text': title,
                       'tag': 'div', 'rendered_text': title},
                      {'selector': base + ' > restore', 'class': 'restore', 'own_text': '恢复',
                       'tag': 'button'}])
    return {'nodes': nodes}


def page_snapshot(total, linked, title='稿'):
    """A single last page with the username, counter, filter state and linked cards."""
    nodes = [{'selector': '#lib', 'class': 'nickname', 'own_text': 'expected', 'tag': 'span'},
             {'selector': '#lib > counter', 'class': '', 'own_text': '已保存图文:%d' % total, 'tag': 'div'},
             {'selector': '#lib > all', 'class': '', 'parent_class': 'active', 'own_text': '全部', 'tag': 'a'},
             {'selector': '#lib > search', 'class': '', 'own_text': '', 'tag': 'input',
              'placeholder': '输入标题或描述', 'value': ''},
             {'selector': '#lib > pager', 'class': 'pagination', 'own_text': '', 'tag': 'ul'},
             {'selector': '#lib > pager > active', 'class': '', 'parent_class': 'active',
              'own_text': '1', 'tag': 'a'}]
    for i, (draft_id, name) in enumerate(linked):
        base = '#lib > card%d' % i
        nodes.extend([{'selector': base, 'class': 'show-item', 'own_text': '', 'tag': 'div'},
                      {'selector': base + ' > link', 'class': '', 'own_text': '', 'tag': 'a',
                       'href': 'https://xiumi.us/studio/v5#/paper/for/' + draft_id},
                      {'selector': base + ' > title', 'class': 'title', 'own_text': name,
                       'tag': 'div', 'rendered_text': name},
                      {'selector': base + ' > time', 'class': '', 'tag': 'div',
                       'own_text': '2026年10月10日 9:12'}])
    return {'nodes': nodes}


class FakeBrowser:
    """Serves canned snapshots so library_state() can be exercised without a browser."""

    def __init__(self, snapshot):
        self.current = snapshot
        self.commands = []
        self.clicks = []

    def command(self, *args):
        self.commands.append(args)

    def wait(self, predicate):
        return self.current

    def snapshot(self):
        return self.current

    def click(self, node):
        self.clicks.append(node)


class CompletenessGateTests(unittest.TestCase):
    def test_real_20261011_shape_is_tolerated_and_reported(self):
        cards, warnings = xiumi_scan.finalize_catalog(day_20261011(), 231)
        self.assertEqual(len(cards), 232)
        self.assertEqual(sum(1 for c in cards if c['available']), 223)
        self.assertEqual(len(warnings), 1)
        warning = warnings[0]
        self.assertEqual(warning['code'], 'duplicate_unlinked_card')
        self.assertEqual(warning['count'], 1)
        self.assertEqual(warning['titles'], ['未央TMS招新 | XX组（各部门头图附后版）'])
        self.assertEqual(warning['slots'], ['unavailable-slot-16-1', 'unavailable-slot-17-0'])

    def test_shortfall_stays_fatal(self):
        with self.assertRaises(Stop) as caught:
            xiumi_scan.finalize_catalog(day_20261011(), 233)
        self.assertEqual(caught.exception.code, 'incomplete_library')
        self.assertIn('below displayed total', str(caught.exception))

    def test_exact_count_returns_clean_without_warning(self):
        cards, warnings = xiumi_scan.finalize_catalog(day_20261011(), 232)
        self.assertEqual(len(cards), 232)
        self.assertEqual(warnings, [])

    def test_unexplained_excess_stays_fatal(self):
        cards = day_20261011() + [card('d999', '另一篇真稿')]
        with self.assertRaises(Stop) as caught:
            xiumi_scan.finalize_catalog(cards, 231)
        self.assertEqual(caught.exception.code, 'incomplete_library')
        self.assertIn('explain only 1', str(caught.exception))

    def test_excess_without_any_repeated_unlinked_title_stays_fatal(self):
        cards = day_20261011() + [card('d999', '另一篇真稿')]
        cards = [c for c in cards if c['id'] != 'unavailable-slot-17-0']
        with self.assertRaises(Stop) as caught:
            xiumi_scan.finalize_catalog(cards, 231)
        self.assertEqual(caught.exception.code, 'incomplete_library')

    def test_repeated_linked_titles_are_not_treated_as_a_render_duplicate(self):
        """Two different manuscripts may legitimately share a title."""
        linked = [card('a1', '同名稿'), card('a2', '同名稿')]
        self.assertEqual(xiumi_scan.duplicate_unlinked(linked), {})
        with self.assertRaises(Stop):
            xiumi_scan.finalize_catalog(linked, 1)


class GuardNotWeakenedTests(unittest.TestCase):
    def test_repeated_id_across_pages_is_reported(self):
        collected = {}
        self.assertIsNone(xiumi_scan.absorb_page(collected, [card('a1', '稿一')]))
        self.assertEqual(xiumi_scan.absorb_page(collected, [card('a2', '稿二'), card('a1', '稿一')]), 'a1')
        self.assertEqual(sorted(collected), ['a1', 'a2'])

    def test_unlinked_ids_stay_page_scoped(self):
        """The guard hole is structural: a synthetic id can never repeat."""
        snapshot = library_snapshot(231, 16, ['未央TMS招新 | XX组（各部门头图附后版）'])
        page = xiumi_scan.parse_page(snapshot, 'expected')
        self.assertEqual([c['id'] for c in page['cards']], ['unavailable-slot-16-0'])
        other = library_snapshot(231, 17, ['未央TMS招新 | XX组（各部门头图附后版）'])
        self.assertEqual([c['id'] for c in xiumi_scan.parse_page(other, 'expected')['cards']],
                         ['unavailable-slot-17-0'])

    def test_duplicate_unlinked_groups_only_unlinked_cards(self):
        cards = [card('a1', '真稿'), card('u1', '槽位', False), card('u2', '槽位', False)]
        self.assertEqual(xiumi_scan.duplicate_unlinked(cards), {'槽位': ['u1', 'u2']})


class LibraryStateTests(unittest.TestCase):
    """library_state() must report an inconsistent reading, not raise over it."""

    def read(self, snapshot):
        browser = FakeBrowser(snapshot)
        with patch.object(xiumi_scan, 'find_window', lambda browser, url: None), \
                patch.object(xiumi_scan.time, 'sleep', lambda seconds: None):
            return xiumi_scan.library_state(browser, 'u', 'expected')

    def test_a_consistent_read_has_no_fault(self):
        collected, total, fault = self.read(page_snapshot(1, [('101', '稿一')]))
        self.assertIsNone(fault)
        self.assertEqual(total, 1)
        self.assertEqual(sorted(collected), ['101'])

    def test_a_count_mismatch_is_returned_as_a_retryable_fault(self):
        collected, total, fault = self.read(page_snapshot(2, [('101', '稿一')]))
        self.assertEqual(fault[0], 'incomplete_library')
        self.assertIn('differs from displayed total', fault[1])
        self.assertEqual(len(collected), 1)

    def test_a_repeated_linked_id_is_returned_as_a_retryable_fault(self):
        collected, total, fault = self.read(page_snapshot(2, [('101', '稿一'), ('101', '稿一')]))
        self.assertEqual(fault[0], 'library_changing')
        self.assertIn('Repeated draft across pages', fault[1])

    def test_wrong_account_still_raises_immediately(self):
        snapshot = page_snapshot(1, [('101', '稿一')])
        with patch.object(xiumi_scan, 'find_window', lambda browser, url: None):
            with self.assertRaises(Stop) as caught:
                xiumi_scan.library_state(FakeBrowser(snapshot), 'u', 'somebody-else')
        self.assertEqual(caught.exception.code, 'wrong_xiumi_account')

    def test_a_library_that_never_settles_is_returned_as_a_fault(self):
        snapshot = page_snapshot(1, [('101', '稿一')])
        browser = FakeBrowser(snapshot)
        real = xiumi_scan.parse_page
        state = {'n': 0}

        def rotating(s, account, require_all=False):
            page = real(s, account, require_all)
            if require_all:
                state['n'] += 1
                page['page'] = 1 + state['n'] % 2  # never the same twice
            return page

        ticks = iter([0.0] + [float(i) for i in range(1, 40)] + [999.0] * 20)
        with patch.object(xiumi_scan, 'find_window', lambda browser, url: None), \
                patch.object(xiumi_scan, 'parse_page', rotating), \
                patch.object(xiumi_scan.time, 'sleep', lambda seconds: None), \
                patch.object(xiumi_scan.time, 'monotonic', lambda: next(ticks)):
            collected, total, fault = xiumi_scan.library_state(browser, 'u', 'expected')
        self.assertEqual(fault[0], 'library_changing')
        self.assertIn('did not settle', fault[1])


class RetryTests(unittest.TestCase):
    """An inconsistent reading is re-read before anything is concluded from it."""

    def setUp(self):
        self.browser = SimpleNamespace(folder=Path('/private/mock/xiumi-scan'))
        self.written = []

    def catalog_with(self, readings):
        """Drive catalog() with canned readings; returns (result, attempts made)."""
        queue = list(readings)
        seen = []

        def fake_state(browser, url, account, max_pages=100):
            seen.append(1)
            return queue.pop(0)

        def fake_write(path, payload):
            self.written.append((str(path), payload))

        with patch.object(xiumi_scan, 'library_state', fake_state), \
                patch.object(xiumi_scan, 'write', fake_write), \
                patch.object(xiumi_scan.time, 'sleep', lambda seconds: None):
            try:
                return xiumi_scan.catalog(self.browser, 'u', 'a'), len(seen)
            except Stop as exc:
                return exc, len(seen)

    def excess_reading(self):
        return ({c['id']: c for c in day_20261011()}, 231,
                ('incomplete_library', 'Collected count 232 differs from displayed total 231'))

    def short_reading(self):
        cards = day_20261011()[:231]
        return ({c['id']: c for c in cards}, 232,
                ('incomplete_library', 'Collected count 231 differs from displayed total 232'))

    def shifted_reading(self):
        return ({'a1': card('a1', '稿一')}, None,
                ('library_changing', 'Repeated draft across pages: a1'))

    def clean_reading(self, total):
        cards = day_20261011()[:total]
        return ({c['id']: c for c in cards}, total, None)

    def test_a_spoiled_reading_is_re_read_and_the_fresh_one_wins(self):
        result, attempts = self.catalog_with([self.excess_reading(), self.clean_reading(232)])
        self.assertEqual(attempts, 2)
        cards, warnings = result
        self.assertEqual(len(cards), 232)
        self.assertEqual(warnings, [])

    def test_the_spoiled_reading_is_kept_as_evidence(self):
        self.catalog_with([self.excess_reading(), self.clean_reading(232)])
        self.assertEqual(len(self.written), 1)
        path, payload = self.written[0]
        self.assertIn('partial-catalog-1-', path)
        self.assertEqual(len(payload), 232)

    def test_a_clean_first_reading_is_not_retried_and_writes_nothing(self):
        result, attempts = self.catalog_with([self.clean_reading(232)])
        self.assertEqual(attempts, 1)
        self.assertEqual(len(result[0]), 232)
        self.assertEqual(self.written, [])

    def test_a_shortfall_is_re_read_and_can_recover(self):
        result, attempts = self.catalog_with([self.short_reading(), self.clean_reading(232)])
        self.assertEqual(attempts, 2)
        self.assertEqual(len(result[0]), 232)

    def test_a_shortfall_that_survives_the_retry_stays_fatal(self):
        result, attempts = self.catalog_with([self.short_reading(), self.short_reading()])
        self.assertEqual(attempts, 2)
        self.assertIsInstance(result, Stop)
        self.assertEqual(result.code, 'incomplete_library')
        self.assertIn('below displayed total', str(result))

    def test_an_excess_that_survives_the_retry_is_tolerated_and_reported(self):
        result, attempts = self.catalog_with([self.excess_reading(), self.excess_reading()])
        self.assertEqual(attempts, 2)
        cards, warnings = result
        self.assertEqual(len(cards), 232)
        self.assertEqual(warnings[0]['code'], 'duplicate_unlinked_card')
        self.assertEqual(warnings[0]['attempts'], 2)
        self.assertEqual(len(self.written), 2)

    def test_a_shifted_library_that_survives_the_retry_stays_fatal(self):
        result, attempts = self.catalog_with([self.shifted_reading(), self.shifted_reading()])
        self.assertEqual(attempts, 2)
        self.assertIsInstance(result, Stop)
        self.assertEqual(result.code, 'library_changing')

    def test_a_shifted_library_is_re_read_and_can_recover(self):
        result, attempts = self.catalog_with([self.shifted_reading(), self.clean_reading(232)])
        self.assertEqual(attempts, 2)
        self.assertEqual(result[1], [])

    def test_retry_count_is_bounded_and_settles_between_attempts(self):
        slept = []
        with patch.object(xiumi_scan, 'library_state', lambda *a, **k: self.short_reading()), \
                patch.object(xiumi_scan, 'write', lambda path, payload: None), \
                patch.object(xiumi_scan.time, 'sleep', lambda seconds: slept.append(seconds)):
            with self.assertRaises(Stop):
                xiumi_scan.catalog(self.browser, 'u', 'a')
        # Concrete values on purpose: deriving them from the constants would make this
        # test pass even if the retry were removed entirely.
        self.assertEqual(xiumi_scan.CATALOG_ATTEMPTS, 2)
        self.assertEqual(xiumi_scan.CATALOG_SETTLE_SECONDS, 5)
        self.assertEqual(slept, [5])


class ReportAndNotificationTests(unittest.TestCase):
    """A tolerated excess must reach the report and the operator message."""

    NOW = __import__('datetime').datetime.fromisoformat('2026-10-11T17:10:00+08:00')

    def observation(self, xiumi):
        return {'date': '2026-10-11', 'timezone': 'Asia/Shanghai',
                'observed_at': '2026-10-11T17:00:00+08:00', 'run_id': 'r1',
                'reservation': {'date': '2026-10-11', 'status': 'ok', 'complete': True,
                                'conflicts_resolved': True,
                                'evidence_paths': ['artifacts/sheet.png'],
                                'articles': [{'row': 54, 'title': '示例头条', 'is_headline': True,
                                              'remarks': ''}]},
                'xiumi': xiumi}

    def evaluate(self, xiumi):
        from scripts import daily_check
        return daily_check.evaluate(self.observation(xiumi), '2026-10-11', self.NOW)

    def test_scan_warning_lands_in_the_report(self):
        receipt = {'status': 'ok', 'account_confirmed': True, 'search_complete': True,
                   'evidence_paths': ['artifacts/search.png'],
                   'matches': [{'row': 54, 'candidates': [{'id': 'a1', 'title': '示例头条',
                                                           'identity_confirmed': True}]}],
                   'warnings': [{'code': 'duplicate_unlinked_card', 'count': 1,
                                 'titles': ['槽位'], 'slots': ['unavailable-slot-16-1']}]}
        report = self.evaluate(receipt)
        self.assertEqual(report['status'], 'ready_to_format')
        self.assertEqual([w['code'] for w in report['warnings']], ['duplicate_unlinked_card'])

    def test_a_scan_without_warnings_reports_none(self):
        receipt = {'status': 'ok', 'account_confirmed': True, 'search_complete': True,
                   'evidence_paths': ['artifacts/search.png'],
                   'matches': [{'row': 54, 'candidates': [{'id': 'a1', 'title': '示例头条',
                                                           'identity_confirmed': True}]}]}
        self.assertEqual(self.evaluate(receipt)['warnings'], [])

    def test_malformed_scan_warning_is_refused(self):
        receipt = {'status': 'ok', 'account_confirmed': True, 'search_complete': True,
                   'evidence_paths': ['artifacts/search.png'],
                   'matches': [{'row': 54, 'candidates': [{'id': 'a1', 'title': '示例头条',
                                                           'identity_confirmed': True}]}],
                   'warnings': [{'count': 1}]}
        with self.assertRaises(ValueError):
            self.evaluate(receipt)

    def test_count_mismatch_is_reported_by_its_real_name(self):
        receipt = {'status': 'failed', 'error_code': 'incomplete_library',
                   'evidence_paths': ['artifacts/search.png']}
        report = self.evaluate(receipt)
        self.assertEqual(report['status'], 'check_failed')
        self.assertEqual([i['code'] for i in report['issues']], ['xiumi_library_count_mismatch'])

    def test_library_changed_is_reported_by_its_real_name(self):
        receipt = {'status': 'failed', 'error_code': 'library_changing',
                   'evidence_paths': ['artifacts/search.png']}
        report = self.evaluate(receipt)
        self.assertEqual([i['code'] for i in report['issues']], ['xiumi_library_changed_during_scan'])

    def test_unknown_xiumi_stop_keeps_the_generic_message(self):
        receipt = {'status': 'failed', 'error_code': 'pagination_unknown',
                   'evidence_paths': ['artifacts/search.png']}
        report = self.evaluate(receipt)
        self.assertEqual([i['code'] for i in report['issues']],
                         ['xiumi_unavailable_wrong_account_or_incomplete_search'])

    def test_login_stop_keeps_its_own_message(self):
        receipt = {'status': 'login_required', 'error_code': 'xiumi_login_required',
                   'evidence_paths': ['artifacts/search.png']}
        report = self.evaluate(receipt)
        self.assertEqual([i['code'] for i in report['issues']], ['xiumi_login_required'])

    def test_operator_message_names_the_excess(self):
        from scripts import notify_wecom
        report = {'date': '2026-10-11', 'run_id': 'r1', 'status': 'ready_to_format',
                  'ready_to_format': True, 'needs_attention': False, 'issues': [],
                  'ordered_rows': [54],
                  'warnings': [{'code': 'duplicate_unlinked_card', 'count': 1,
                                'titles': ['槽位'], 'slots': ['unavailable-slot-16-1']}]}
        message = notify_wecom.result_from_report(report, self.observation({'status': 'ok'}))
        self.assertIn('秀米稿件库条数提醒（不阻止后续处理）：', message)
        self.assertIn('页面自报条数比实际渲染的卡片少 1 张', message)
        self.assertIn('不可用槽位不参与匹配', message)

    def test_title_difference_and_library_warning_render_together(self):
        from scripts import notify_wecom
        warnings = [{'code': 'title_difference_matched', 'row': 54, 'reservation_title': '甲',
                     'xiumi_title': '乙', 'draft_id': 'a1', 'reason': '正文与周次一致'},
                    {'code': 'duplicate_unlinked_card', 'count': 1, 'titles': ['槽位'],
                     'slots': ['unavailable-slot-16-1']}]
        lines = notify_wecom.nonblocking_warning_lines({'warnings': warnings})
        self.assertIn('标题差异提醒（已核验为同一篇，不阻止后续处理）：', lines)
        self.assertIn('秀米稿件库条数提醒（不阻止后续处理）：', lines)

    def test_unknown_nonblocking_warning_still_refused(self):
        from scripts import notify_wecom
        with self.assertRaises(ValueError):
            notify_wecom.nonblocking_warning_lines({'warnings': [{'code': 'invented'}]})

    def test_completion_comparison_ignores_library_warnings(self):
        """The completion check sorts title differences by row; a library warning has none."""
        from scripts import notify_wecom
        report = {'warnings': [{'code': 'duplicate_unlinked_card', 'count': 1,
                                'titles': ['槽位'], 'slots': ['unavailable-slot-16-1']},
                               {'code': 'title_difference_matched', 'row': 54,
                                'reservation_title': '甲', 'xiumi_title': '乙',
                                'draft_id': 'a1', 'reason': '正文与周次一致'}]}
        selected = notify_wecom.title_difference_warnings(report)
        self.assertEqual([w['row'] for w in selected], [54])
        self.assertEqual(sorted(selected, key=lambda w: w['row'])[0]['row'], 54)
        self.assertEqual(notify_wecom.title_difference_warnings({'warnings': report['warnings'][:1]}), [])


if __name__ == '__main__':
    unittest.main()
