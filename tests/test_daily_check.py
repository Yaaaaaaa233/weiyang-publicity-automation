import copy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.daily_check import evaluate, check_evidence


class DailyCheckTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.fromisoformat('2026-10-04T17:10:00+08:00')
        self.observation = {
            'date': '2026-10-04', 'timezone': 'Asia/Shanghai',
            'observed_at': '2026-10-04T17:00:00+08:00',
            'reservation': {
                'date': '2026-10-04', 'status': 'ok', 'complete': True,
                'conflicts_resolved': True, 'evidence_paths': ['artifacts/sheet.png'],
                'articles': [{'row': 6, 'title': '示例乙', 'is_headline': False, 'remarks': ''},
                             {'row': 8, 'title': '示例丙', 'is_headline': False, 'remarks': ''},
                             {'row': 7, 'title': '示例甲', 'is_headline': True, 'remarks': ''}]},
            'xiumi': {'status': 'ok', 'account_confirmed': True, 'search_complete': True,
                      'evidence_paths': ['artifacts/search.png'],
                      'matches': [{'row': row, 'candidates': [
                          {'id': str(row), 'title': {6:'示例乙',7:'示例甲',8:'示例丙'}[row], 'identity_confirmed': True}]}
                                  for row in (6, 7, 8)]}}

    def result(self):
        return evaluate(self.observation, '2026-10-04', self.now)

    def test_complete_group_orders_headline_then_source_rows(self):
        result = self.result()
        self.assertTrue(result['ready_to_format'])
        self.assertEqual(result['ordered_rows'], [7, 6, 8])

    def test_no_reservations_does_not_require_xiumi(self):
        self.observation['reservation']['articles'] = []
        del self.observation['xiumi']
        self.assertEqual(self.result()['status'], 'no_reservations')

    def test_unreadable_empty_sheet_is_failure(self):
        self.observation['reservation']['articles'] = []
        self.observation['reservation']['complete'] = False
        self.assertEqual(self.result()['status'], 'check_failed')

    def test_missing_unchecked_and_wrong_week_each_block(self):
        for mode in ('missing', 'unchecked', 'wrong_week'):
            with self.subTest(mode=mode):
                original = copy.deepcopy(self.observation)
                matches = self.observation['xiumi']['matches']
                if mode == 'missing':
                    matches[0]['candidates'] = []
                elif mode == 'unchecked':
                    matches.pop(0)
                else:
                    matches[0]['candidates'][0]['identity_confirmed'] = False
                self.assertFalse(self.result()['ready_to_format'])
                self.assertTrue(self.result()['needs_attention'])
                self.observation = original

    def test_duplicate_versions_cannot_be_selected_by_title(self):
        candidates = self.observation['xiumi']['matches'][0]['candidates']
        candidates.append(dict(candidates[0], id='second-version'))
        self.assertEqual(self.result()['issues'], [{'code': 'multiple_versions_unresolved', 'row': 6}])

    def test_one_draft_cannot_satisfy_two_bookings(self):
        self.observation['xiumi']['matches'][1]['candidates'][0]['id'] = '6'
        self.assertEqual(len(self.result()['issues']), 2)

    def test_login_failure_is_not_missing_manuscripts(self):
        self.observation['xiumi']['status'] = 'login_required'
        self.assertEqual(self.result()['status'], 'check_failed')
        self.assertEqual(self.result()['issues'], [{'code': 'xiumi_login_required'}])

    def test_wps_login_failure_is_specific_and_not_no_reservations(self):
        self.observation['reservation']['status'] = 'login_required'
        self.observation['reservation']['articles'] = []
        self.assertEqual(self.result()['status'], 'check_failed')
        self.assertEqual(self.result()['issues'], [{'code': 'wps_login_required'}])

    def test_title_difference_with_evidenced_identity_is_nonblocking(self):
        candidate = self.observation['xiumi']['matches'][0]['candidates'][0]
        candidate.update(title='示例乙正式题名', identity_reason='正文主题、日期和部门对应，周次一致，唯一版本')
        result = self.result()
        self.assertTrue(result['ready_to_format'])
        self.assertFalse(result['needs_attention'])
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['warnings'][0]['xiumi_title'], '示例乙正式题名')

    def test_title_similarity_without_identity_reason_is_not_a_match(self):
        self.observation['xiumi']['matches'][0]['candidates'][0]['title'] = '示例乙正式题名'
        self.assertEqual(self.result()['issues'], [{'code': 'identity_match_reason_missing', 'row': 6}])

    def test_stale_future_or_other_date_rejected(self):
        for timestamp in ('2026-10-04T15:00:00+08:00', '2026-10-04T18:00:00+08:00',
                          '2026-09-28T17:00:00+08:00', '2026-10-04T17:00:00'):
            self.observation['observed_at'] = timestamp
            self.assertEqual(self.result()['status'], 'check_failed')

    def test_wrong_date_even_when_sheet_empty(self):
        self.observation['reservation']['date'] = '2026-09-28'
        self.observation['reservation']['articles'] = []
        self.assertEqual(self.result()['status'], 'check_failed')

    def test_missing_headline_or_unresolved_remarks_blocks(self):
        self.observation['reservation']['articles'][2]['is_headline'] = False
        self.assertFalse(self.result()['ready_to_format'])
        self.observation['reservation']['articles'][2]['is_headline'] = True
        self.observation['reservation']['conflicts_resolved'] = False
        self.assertFalse(self.result()['ready_to_format'])

    def test_incomplete_search_blocks_even_with_all_matches(self):
        self.observation['xiumi']['search_complete'] = False
        self.assertEqual(self.result()['status'], 'check_failed')

    def test_unexpected_or_duplicate_rows_are_invalid(self):
        self.observation['xiumi']['matches'].append(copy.deepcopy(self.observation['xiumi']['matches'][0]))
        with self.assertRaises(ValueError):
            self.result()

    def test_evidence_is_required(self):
        self.observation['reservation']['evidence_paths'] = []
        self.assertEqual(self.result()['status'], 'check_failed')

    def test_real_evidence_must_exist_and_remain_private(self):
        with tempfile.TemporaryDirectory() as folder, patch('scripts.daily_check.ROOT', Path(folder)):
            root = Path(folder)
            (root / 'artifacts').mkdir()
            for name in ('sheet.png', 'search.png'):
                (root / 'artifacts' / name).write_bytes(b'fixture')
            check_evidence(self.observation)
            (root / 'artifacts/search.png').unlink()
            with self.assertRaises(ValueError):
                check_evidence(self.observation)
            (root / 'public.png').write_bytes(b'fixture')
            self.observation['xiumi']['evidence_paths'] = ['public.png']
            with self.assertRaises(ValueError):
                check_evidence(self.observation)

    def test_explicit_historical_mode_keeps_observations_fresh_today(self):
        self.observation['date'] = self.observation['reservation']['date'] = '2026-09-21'
        self.assertEqual(evaluate(self.observation, '2026-09-21', self.now)['status'], 'check_failed')
        result = evaluate(self.observation, '2026-09-21', self.now, historical_test=True)
        self.assertTrue(result['ready_to_format'])
        self.assertTrue(result['historical_test'])
        self.assertEqual(result['run_date'], '2026-10-04')

    def test_historical_mode_rejects_old_cached_or_stale_observations(self):
        self.observation['date'] = self.observation['reservation']['date'] = '2026-09-21'
        for timestamp in ('2026-09-21T17:00:00+08:00', '2026-10-04T15:00:00+08:00'):
            self.observation['observed_at'] = timestamp
            self.assertEqual(evaluate(self.observation, '2026-09-21', self.now, historical_test=True)['status'], 'check_failed')

    def test_historical_mode_rejects_today_or_future_business_date(self):
        for value in ('2026-10-04', '2026-10-05'):
            self.observation['date'] = self.observation['reservation']['date'] = value
            self.assertEqual(evaluate(self.observation, value, self.now, historical_test=True)['status'], 'check_failed')


if __name__ == '__main__':
    unittest.main()
