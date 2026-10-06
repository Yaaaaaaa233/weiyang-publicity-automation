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

    def test_multiple_or_unknown_headlines_require_review(self):
        self.payload['matches'][0]['rows'][0]['cells'][3] = '是'
        self.assertFalse(self.convert()['conflicts_resolved'])
        self.payload['matches'][0]['rows'][0]['cells'][3] = '待定'
        self.assertFalse(self.convert()['conflicts_resolved'])

    def test_instruction_semantics_not_auto_approved(self):
        result = reservation_from_payload(self.payload, '2026-10-04', ['local/logs.json'], self.now)
        self.assertFalse(result['conflicts_resolved'])
        self.assertEqual(result['ordered_rows'], [])


if __name__ == '__main__':
    unittest.main()
