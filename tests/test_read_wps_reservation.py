import copy
from datetime import datetime
import json
import unittest

from scripts.read_wps_reservation import checksum, decode_frames, reservation_from_payload, utf16_length
from scripts.daily_check import evaluate


def frames(payload):
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    parts = [text[i:i+400] for i in range(0, len(text), 400)]
    run = payload['run_id']
    suffix = f'{run} {len(parts)} {utf16_length(text)} {checksum(text)}'
    return [f'WY_BEGIN {suffix}'] + [f'WY_PART {run} {i}/{len(parts)} {s}' for i, s in enumerate(parts, 1)] + [f'WY_END {suffix}', '执行完毕']


class WpsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.fromisoformat('2026-10-04T22:05:00+08:00')
        cells = lambda: [''] * 11
        headers = [cells(), cells()]
        headers[0][:4] = ['发布日期', '发布单位', '文章标题', '是否头条']
        headers[0][9] = '备注'
        headers[1][7:9] = ['宣传组意见', '是否发布']
        rows = [{'row': i, 'cells': cells()} for i in range(6, 14)]
        rows[0]['cells'][0] = '2026/10/4\n周日'
        for i in range(3):
            rows[i]['cells'][1:3] = ['虚构部门', '示例推送😀' + str(i)]
        rows[1]['cells'][3] = '是'
        self.payload = {'schema': 'weiyang.reservation-day.v1', 'ok': True,
                        'run_id': 'wps-fixture', 'generated_at': '2026-10-04T14:04:00Z',
                        'date': '2026-10-04', 'cols': 11,
                        'sheets_scanned': [{'sheet': '测试周', 'dates': [{'date': '2026-10-04', 'start_row': 6, 'end_row': 13}]}],
                        'matches': [{'sheet': '测试周', 'start_row': 6, 'end_row': 13,
                                     'instructions': '示例说明', 'headers': [{'row': 3, 'cells': headers[0]}, {'row': 4, 'cells': headers[1]}], 'rows': rows}]}

    def convert(self):
        return reservation_from_payload(self.payload, '2026-10-04', ['local/logs.json'], self.now, instructions_reviewed=True)

    def test_complete_transport_and_daily_order(self):
        self.assertEqual(decode_frames(frames(self.payload), 'wps-fixture'), self.payload)
        reservation = self.convert()
        xiumi = {'status': 'ok', 'account_confirmed': True, 'search_complete': True,
                  'evidence_paths': ['local/fixture.json'], 'matches': [{'row': r, 'candidates': [{'id': str(r), 'title': '虚构匹配', 'identity_confirmed': True}]} for r in (6, 7, 8)]}
        observation = {'date': '2026-10-04', 'timezone': 'Asia/Shanghai', 'observed_at': self.payload['generated_at'], 'reservation': reservation, 'xiumi': xiumi}
        self.assertEqual(evaluate(observation, '2026-10-04', self.now)['ordered_rows'], [7, 6, 8])

    def test_utf16_emoji_checksum(self):
        self.assertEqual(utf16_length('😀'), 2)
        self.assertEqual(checksum('😀'), 'cb31c4b8')

    def test_missing_reordered_duplicate_truncated_mixed_and_unfinished_logs(self):
        original = frames(self.payload)
        cases = []
        cases.append(original[:1] + original[2:])
        changed = original.copy(); changed[1], changed[2] = changed[2], changed[1]; cases.append(changed)
        cases.append(original[:2] + original[1:])
        changed = original.copy(); changed[1] = changed[1][:-1]; cases.append(changed)
        changed = original.copy(); changed[1] = changed[1].replace('wps-fixture', 'wps-other'); cases.append(changed)
        cases.append(original[:-1])
        cases.append(original + original)
        for logs in cases:
            with self.subTest(logs=logs[0]):
                with self.assertRaises(ValueError): decode_frames(logs, 'wps-fixture')

    def test_same_length_corruption_detected(self):
        logs = frames(self.payload)
        logs[1] = logs[1].replace('schema', 'schemb')
        with self.assertRaises(ValueError): decode_frames(logs, 'wps-fixture')

    def test_wrong_run_rejected(self):
        with self.assertRaises(ValueError): decode_frames(frames(self.payload), 'wps-other')

    def test_unknown_source_not_empty_day(self):
        self.payload.update(ok=False, error='date_not_found', matches=[])
        result = self.convert()
        self.assertFalse(result['complete'])
        self.assertEqual(result['status'], 'failed')

    def test_actual_empty_range_accepts_duty_only(self):
        for entry in self.payload['matches'][0]['rows']:
            entry['cells'][1:10] = [''] * 9
        self.payload['matches'][0]['rows'][0]['cells'][10] = '虚构值班人'
        self.assertEqual(self.convert()['articles'], [])
        self.assertTrue(self.convert()['complete'])

    def test_missing_rows_headers_columns_and_anchor_rejected(self):
        original = copy.deepcopy(self.payload)
        changes = [lambda b: b['rows'].pop(), lambda b: b['headers'][0]['cells'].__setitem__(2, '其他列'), lambda b: b['rows'][0]['cells'].pop(), lambda b: b['rows'][0]['cells'].__setitem__(0, '2026/10/3')]
        for change in changes:
            self.payload = copy.deepcopy(original)
            change(self.payload['matches'][0])
            with self.assertRaises(ValueError): self.convert()

    def test_stale_future_and_wrong_date_rejected(self):
        for stamp in ('2026-10-04T12:00:00Z', '2026-10-04T16:00:00Z', '2026-10-04T14:04:00'):
            self.payload['generated_at'] = stamp
            with self.assertRaises(ValueError): self.convert()

    def test_small_clock_skew_tolerated_but_real_future_still_rejected(self):
        """generated_at is stamped by the WPS cloud clock; seconds of skew must not fail the gate."""
        self.payload['generated_at'] = '2026-10-04T14:05:02Z'   # 2s ahead of the host clock
        self.assertEqual(self.convert()['status'], 'ok')
        self.payload['generated_at'] = '2026-10-04T14:07:30Z'   # 2.5min ahead: beyond the allowance
        with self.assertRaises(ValueError): self.convert()

    def test_multiple_dates_and_scan_mismatch_rejected(self):
        self.payload['matches'].append(copy.deepcopy(self.payload['matches'][0]))
        with self.assertRaises(ValueError): self.convert()
        self.payload['matches'].pop()
        self.payload['sheets_scanned'][0]['dates'][0]['start_row'] = 7
        with self.assertRaises(ValueError): self.convert()

    def test_partial_booking_fails_and_remarks_require_review(self):
        self.payload['matches'][0]['rows'][0]['cells'][2] = ''
        self.assertFalse(self.convert()['complete'])
        self.payload['matches'][0]['rows'][0]['cells'][2] = '虚构推送'
        self.payload['matches'][0]['rows'][0]['cells'][9] = '示例备注'
        self.assertFalse(self.convert()['conflicts_resolved'])

    def test_multiple_headlines_require_review(self):
        self.payload['matches'][0]['rows'][0]['cells'][3] = '是'
        result = self.convert()
        self.assertFalse(result['conflicts_resolved'])
        self.assertIn('headline_conflict_multiple', result['issues'])
        self.assertEqual(result['ordered_rows'], [])

    def test_unknown_headline_marker_is_a_data_error(self):
        self.payload['matches'][0]['rows'][0]['cells'][3] = '待定'
        result = self.convert()
        self.assertFalse(result['conflicts_resolved'])
        self.assertIn({'code': 'unknown_headline', 'row': 6}, result['issues'])

    def test_unmarked_day_resolves_to_sheet_order(self):
        """Operator decision 2026-10-12: no marked headline is not a conflict.

        EVERY headline marker is cleared, not just one.  Clearing a single marker leaves
        the fixture still holding exactly one headline, which passes for the wrong reason
        and hid a real defect: the reader kept `conflicts_resolved` false, ordered_rows
        stayed empty, and the checker then reported the derivative
        `reservation_remarks_or_order_unresolved` for a sheet whose remarks are empty.
        """
        for row in self.payload['matches'][0]['rows']:
            row['cells'][3] = ''
        result = self.convert()
        self.assertTrue(result['conflicts_resolved'])
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['ordered_rows'], [6, 7, 8])

    def test_unmarked_day_end_to_end_does_not_report_a_remarks_conflict(self):
        """The real-reader-to-real-checker path for an unmarked day.

        This is the regression for the defect above: it feeds the payload through the real
        reader and then the real checker, so a reader that withholds conflicts_resolved (or
        ordered_rows) fails here instead of only in production.
        """
        for row in self.payload['matches'][0]['rows']:
            row['cells'][3] = ''
        reservation = self.convert()
        # Titles must equal the reservation titles exactly; a reused placeholder would
        # raise identity_match_reason_missing and mask the claim under test.
        titles = {a['row']: a['title'] for a in reservation['articles']}
        xiumi = {'status': 'ok', 'account_confirmed': True, 'search_complete': True,
                 'evidence_paths': ['local/fixture.json'],
                 'matches': [{'row': r, 'candidates': [{'id': str(r), 'title': titles[r],
                                                        'identity_confirmed': True}]}
                             for r in (6, 7, 8)]}
        observation = {'date': '2026-10-04', 'timezone': 'Asia/Shanghai',
                       'observed_at': self.payload['generated_at'],
                       'reservation': reservation, 'xiumi': xiumi}
        report = evaluate(observation, '2026-10-04', self.now)
        codes = [item['code'] for item in report['issues']]
        self.assertEqual(codes, [])
        self.assertTrue(report['ready_to_format'])
        self.assertEqual(report['ordered_rows'], [6, 7, 8])
        self.assertEqual([w['code'] for w in report['warnings']], ['headline_absent_sheet_order'])

    def test_unmarked_day_order_is_by_row_not_by_input_position(self):
        """The fallback is defined by row number, not by the order rows arrived in.

        The rows must stay in row order on the wire (the reader rejects a reordered
        date block), so this assigns each row's content in reverse to prove the order
        comes from the row numbers rather than from the sequence of article records.
        """
        rows = self.payload['matches'][0]['rows']
        originals = copy.deepcopy(rows[:3])
        for index, row in enumerate(rows[:3]):
            source = originals[2 - index]['cells']
            row['cells'][1:3] = [source[1], source[2]]
            row['cells'][3] = ''
        result = self.convert()
        self.assertEqual([a['row'] for a in result['articles']], [6, 7, 8])
        self.assertEqual([a['title'] for a in result['articles']],
                         ['示例推送😀2', '示例推送😀1', '示例推送😀0'])
        self.assertEqual(result['ordered_rows'], [6, 7, 8])

    def test_articles_always_arrive_in_row_order(self):
        """The invariant that makes an explicit ordered_rows sort equivalent.

        ordered_rows used to be built by partitioning the article list in place, which
        is correct only while that list is itself in row order.  The reader enforces
        that order while parsing, so an explicit sort cannot change any reachable
        result -- and the explicit form cannot silently depend on the invariant either.
        This case pins the invariant so the equivalence is checked, not assumed.
        """
        rows = self.payload['matches'][0]['rows']
        for index, row in enumerate(rows):
            row['cells'][1] = '虚构部门'
            row['cells'][2] = '示例稿件' + str(index)
            if index:
                row['cells'][3] = ''
        self.convert()  # the reader itself refuses a reordered block
        self.assertEqual([a['row'] for a in self.convert()['articles']], list(range(6, 14)))

    def test_instruction_semantics_not_auto_approved(self):
        result = reservation_from_payload(self.payload, '2026-10-04', ['local/logs.json'], self.now)
        self.assertFalse(result['conflicts_resolved'])
        self.assertEqual(result['ordered_rows'], [])


if __name__ == '__main__':
    unittest.main()
