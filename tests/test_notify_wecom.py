import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.notify_wecom import alert_from_report, result_from_report, completion_from_report, configured_webhook, deliver
from scripts.daily_check import evaluate
from datetime import datetime
import copy


class WecomNotificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = patch('scripts.notify_wecom.ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fictional'

    def test_dry_run_sends_nothing_and_reserves_nothing(self):
        def forbidden(*args):
            self.fail('Dry run must not dispatch')
        self.assertEqual(deliver('test', self.url, sender=forbidden)['status'], 'dry_run')
        self.assertFalse((self.root / 'data').exists())

    def test_receipt_is_reserved_before_network_and_duplicate_never_resends(self):
        calls = []
        def sender(url, message):
            calls.append(message)
            receipts = list((self.root / 'data/wecom-notifications').glob('*.json'))
            self.assertEqual(json.loads(receipts[0].read_text())['status'], 'dispatch_reserved')
            return 0
        result = deliver('test', self.url, execute=True, sender=sender)
        self.assertEqual(result['status'], 'accepted')
        self.assertTrue(deliver('test', self.url, execute=True, sender=sender)['duplicate'])
        self.assertEqual(len(calls), 1)
        self.assertNotIn(self.url, Path(result['receipt']).read_text())

    def test_uncertain_delivery_is_not_replayed_or_secret_logged(self):
        calls = []
        def sender(url, message):
            calls.append(message)
            raise TimeoutError(url)
        result = deliver('test', self.url, execute=True, sender=sender)
        self.assertEqual(result['status'], 'outcome_unknown')
        self.assertNotIn(self.url, Path(result['receipt']).read_text())
        deliver('test', self.url, execute=True, sender=sender)
        self.assertEqual(len(calls), 1)

    def test_api_rejection_is_not_reported_as_accepted(self):
        result = deliver('test', self.url, execute=True, sender=lambda *_: 93000)
        self.assertEqual(result['status'], 'rejected')

    def test_webhook_is_loaded_privately_and_destination_restricted(self):
        with patch.dict('os.environ', {'WECOM_WEBHOOK_URL': ''}):
            (self.root / '.env').write_text('WECOM_WEBHOOK_URL=' + self.url)
            self.assertEqual(configured_webhook(), self.url)
        for invalid in ('http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x',
                        'https://example.com/cgi-bin/webhook/send?key=x',
                        self.url + '&key=other', self.url + '#fragment'):
            with patch.dict('os.environ', {'WECOM_WEBHOOK_URL': invalid}):
                with self.assertRaises(ValueError):
                    configured_webhook()

    def test_missing_and_version_alert_contains_expected_title_and_unit(self):
        report = {'date': '2026-10-04', 'status': 'needs_attention', 'needs_attention': True,
                  'issues': [{'code': 'manuscript_missing', 'row': 6}]}
        observation = {'date': '2026-10-04', 'reservation': {
            'articles': [{'row': 6, 'title': '示例稿件', 'unit': '示例部门'}]}}
        self.assertIn('示例部门 示例稿件', alert_from_report(report, observation))
        report['status'] = 'no_reservations'
        with self.assertRaises(ValueError):
            alert_from_report(report, observation)

    def test_payload_must_be_nonempty_and_bounded(self):
        for invalid in ('', ' ' * 3, '测' * 601):
            with self.assertRaises(ValueError):
                deliver(invalid, self.url)

    def test_historical_alert_is_distinguished_from_current_business(self):
        report = {'date': '2026-09-21', 'run_date': '2026-10-04', 'historical_test': True,
                  'status': 'needs_attention', 'needs_attention': True,
                  'issues': [{'code': 'headline_unresolved'}]}
        observation = {'date': '2026-09-21', 'reservation': {'articles': []}}
        text = alert_from_report(report, observation)
        self.assertIn('历史测试', text)
        self.assertIn('预约 2026-09-21', text)
        self.assertIn('执行 2026-10-04', text)

    def test_no_reservations_generates_normal_result(self):
        report = {'date': '2026-10-04', 'status': 'no_reservations', 'needs_attention': False, 'issues': []}
        observation = {'date': '2026-10-04', 'reservation': {'articles': []}}
        self.assertIn('无预约', result_from_report(report, observation))
        observation['reservation']['articles'] = [{'row': 6}]
        with self.assertRaises(ValueError):
            result_from_report(report, observation)

    def test_received_all_does_not_claim_formatting_completed(self):
        report = {'date': '2026-09-21', 'run_date': '2026-10-04', 'historical_test': True,
                  'status': 'ready_to_format', 'ready_to_format': True, 'needs_attention': False, 'issues': []}
        observation = {'date': '2026-09-21', 'reservation': {'articles': [{'row': 6}, {'row': 7}]}}
        text = result_from_report(report, observation)
        self.assertIn('预约 2 篇', text)
        self.assertIn('基础整理待完成', text)
        self.assertIn('历史测试', text)
        self.assertNotIn('整理完成', text)

    def completion_fixture(self):
        (self.root / 'local').mkdir(exist_ok=True)
        (self.root / 'local/proof.json').write_text('{}')
        articles = [{'row':6,'title':'预约乙','is_headline':False,'remarks':''},
                    {'row':7,'title':'预约甲','is_headline':True,'remarks':''}]
        observation = {'date':'2026-10-05','timezone':'Asia/Shanghai','run_id':'fictional-run',
                       'observed_at':'2026-10-05T15:10:00+08:00',
                       'reservation':{'date':'2026-10-05','status':'ok','complete':True,
                                      'conflicts_resolved':True,'articles':articles,'evidence_paths':['local/proof.json']},
                       'xiumi':{'status':'ok','account_confirmed':True,'search_complete':True,
                                'evidence_paths':['local/proof.json'],
                                'matches':[{'row':a['row'],'candidates':[{'id':str(a['row']),
                                           'title':a['title'],'identity_confirmed':True}]} for a in articles]}}
        candidate = observation['xiumi']['matches'][0]['candidates'][0]
        candidate.update(title='乙的正式题名', identity_reason='部门、日期、主题及正文一致，唯一版本')
        report = evaluate(observation, '2026-10-05', datetime.fromisoformat('2026-10-05T15:15:00+08:00'))
        completion = {'date':report['date'],'run_id':report['run_id'],
                      'formatted_articles':[{'row':r,'source_id':str(r),'draft_id':'copy-'+str(r),
                                             'saved_reopened':True,'body_images_verified':True,'format_verified':True,
                                             'evidence_paths':['local/proof.json']} for r in (7,6)],
                      'sync':{'date':report['date'],'status':'confirmed','confirmed_by':'user',
                              'ordered_draft_ids':['copy-7','copy-6'],'evidence_paths':['local/proof.json']}}
        return report, observation, completion

    def test_matched_title_warning_does_not_request_approval(self):
        report, observation, _ = self.completion_fixture()
        text = result_from_report(report, observation)
        self.assertIn('无需等待人工确认', text)
        self.assertIn('乙的正式题名', text)
        self.assertIn('基础整理待完成', text)

    def test_completion_lists_headline_order_and_title_difference(self):
        report, observation, completion = self.completion_fixture()
        text = completion_from_report(report, observation, completion)
        self.assertLess(text.index('预约甲（头条）'), text.index('2、预约乙'))
        self.assertIn('已校验基础格式并转存到微信公众号草稿箱', text)
        self.assertIn('乙的正式题名', text)

    def test_submission_alone_does_not_claim_sync_completed(self):
        report, observation, completion = self.completion_fixture()
        completion['sync']['status'] = 'submitted'
        text = completion_from_report(report, observation, completion)
        self.assertIn('转存结果待确认', text)
        self.assertNotIn('已校验基础格式并转存到', text)

    def test_completion_rejects_missing_checks_evidence_wrong_run_and_order(self):
        original = self.completion_fixture()
        for mode in ('checks','evidence','run','order','unconfirmed','warnings'):
            report, observation, completion = copy.deepcopy(original)
            if mode == 'checks':
                completion['formatted_articles'][0]['body_images_verified'] = False
            elif mode == 'evidence':
                completion['sync']['evidence_paths'] = ['local/absent.json']
            elif mode == 'run':
                completion['run_id'] = 'wrong-run'
            elif mode == 'order':
                completion['sync']['ordered_draft_ids'].reverse()
            elif mode == 'unconfirmed':
                completion['sync']['confirmed_by'] = 'assumed'
            else:
                report['warnings'] = []
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                completion_from_report(report, observation, completion)

    def test_new_run_notices_differ_but_same_run_is_deterministic(self):
        report, observation, _ = self.completion_fixture()
        first = result_from_report(report, observation)
        self.assertEqual(first, result_from_report(report, observation))
        report['run_id'] = 'next-real-run'
        self.assertNotEqual(first, result_from_report(report, observation))
