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
        self.assertEqual(self.result()['issues'], [{'code': 'candidate_identity_unresolved', 'row': 6}])

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

    def test_unmarked_headline_falls_back_to_sheet_order(self):
        """Operator decision 2026-10-12: a day that marks no headline is not a stop.

        The sheet asks for the ordinary top-to-bottom order, so the round proceeds on
        row order and records a non-blocking warning naming the order source.
        """
        for article in self.observation['reservation']['articles']:
            article['is_headline'] = False
        result = self.result()
        self.assertTrue(result['ready_to_format'])
        self.assertFalse(result['needs_attention'])
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['ordered_rows'], [6, 7, 8])
        self.assertEqual([w['code'] for w in result['warnings']], ['headline_absent_sheet_order'])

    def test_multiple_headlines_still_block(self):
        """Two marked headlines stay a stop: no fallback may invent which one leads."""
        self.observation['reservation']['articles'][0]['is_headline'] = True
        result = self.result()
        self.assertFalse(result['ready_to_format'])
        self.assertEqual(result['issues'], [{'code': 'headline_conflict_multiple'}])

    def test_unresolved_remarks_still_block(self):
        self.observation['reservation']['conflicts_resolved'] = False
        self.assertFalse(self.result()['ready_to_format'])

    def test_headline_warning_survives_a_run_that_stops_for_identity(self):
        """The order source is recorded even when another issue stops the round."""
        for article in self.observation['reservation']['articles']:
            article['is_headline'] = False
        candidates = self.observation['xiumi']['matches'][0]['candidates']
        candidates.append(dict(candidates[0], id='second-version'))
        result = self.result()
        self.assertEqual(result['issues'], [{'code': 'candidate_identity_unresolved', 'row': 6}])
        self.assertEqual([w['code'] for w in result['warnings']], ['headline_absent_sheet_order'])

    def test_20261012_shape_defers_only_the_ambiguous_row(self):
        """Replay of the real 2026-10-12 stop, with every real blocker accounted for.

        The 17:00 round reported three issues.  Two were the headline gate and its
        derivative remarks text; the third was a genuine title ambiguity on the first
        row (a sheet-side 样/羊 style difference) that must still be settled from the
        body.  One unmarked row is given thirty candidates so the ambiguity is real.
        """
        reservation = self.observation['reservation']
        reservation['articles'] = [
            {'row': 7, 'title': '示例样题丨第五周', 'is_headline': False, 'remarks': ''},
            {'row': 8, 'title': '示例甲题', 'is_headline': False, 'remarks': ''},
            {'row': 9, 'title': '示例乙题', 'is_headline': False, 'remarks': ''}]
        matches = []
        for row in (7, 8, 9):
            title = {7: '示例样题丨第五周', 8: '示例甲题', 9: '示例乙题'}[row]
            candidates = [{'id': str(row), 'title': title, 'identity_confirmed': row != 7}]
            if row == 7:
                # Unconfirmed near-misses, exactly like the returned 30-candidate row.
                candidates += [{'id': '7-' + str(i), 'title': '示例羊题丨第五周',
                                'identity_confirmed': False} for i in range(3)]
            matches.append({'row': row, 'candidates': candidates})
        self.observation['xiumi']['matches'] = matches
        result = self.result()
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['issues'], [{'code': 'candidate_identity_unresolved', 'row': 7}])
        self.assertEqual(result['ordered_rows'], [7, 8, 9])
        self.assertEqual([w['code'] for w in result['warnings']], ['headline_absent_sheet_order'])

    def test_only_the_semantic_identity_path_may_answer_that_issue(self):
        """The surviving issue set must be what full_run's body-review gate admits."""
        from scripts.full_run import SEMANTIC_IDENTITY_ISSUES
        self.assertEqual(SEMANTIC_IDENTITY_ISSUES,
                         ('identity_or_week_unresolved', 'candidate_identity_unresolved',
                          'identity_match_reason_missing'))
        # The headline fallback must never become a body-review request: it is settled
        # by falling back to sheet order, not by asking an agent to choose a lead.
        self.assertNotIn('headline_absent_sheet_order', SEMANTIC_IDENTITY_ISSUES)
        self.assertNotIn('headline_conflict_multiple', SEMANTIC_IDENTITY_ISSUES)

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
