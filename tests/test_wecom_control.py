from datetime import datetime, timezone
import sqlite3
import tempfile
from pathlib import Path
import unittest
from scripts.wecom_control import handle, private


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'requests.sqlite'
        self.now = datetime(2026, 10, 14, 17, tzinfo=timezone.utc)
        self.config = dict(mode='queue_only', bot_id='fixture-bot', bot_name='运营测试机器人',
                           allowed_chat_ids=['fixture-group'], allowed_user_ids=['fixture-user'])
        self.body = dict(aibotid='fixture-bot', msgid='fixture-message-1', chattype='group',
                         chatid='fixture-group', msgtype='text', text={'content': '@运营测试机器人 补跑今天'},
                         **{'from': {'userid': 'fixture-user'}})

    def run_message(self, body=None, config=None):
        return handle(config or self.config, body or self.body, self.db, self.now)

    def test_queue_records_actual_shanghai_day(self):
        result = self.run_message()
        self.assertEqual(result['date'], '2026-10-15')
        self.assertEqual(result['status'], 'queued')
        self.assertIn('尚未执行真实业务', result['reply'])

    def test_repeat_message_is_deduplicated_after_reopen(self):
        self.run_message()
        self.assertEqual(self.run_message()['status'], 'duplicate')
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 1)

    def test_new_click_does_not_queue_concurrent_run(self):
        self.run_message()
        other = dict(self.body, msgid='fixture-message-2')
        self.assertEqual(self.run_message(other)['status'], 'busy')

    def test_unknown_sender_group_and_bot_never_create_database(self):
        for patch in ({'from': {'userid': 'outsider'}}, {'chatid': 'other'}, {'aibotid': 'other'}, {'chattype': 'single'}):
            self.assertEqual(self.run_message(dict(self.body, **patch))['status'], 'ignored')
        self.assertFalse(self.db.exists())

    def test_arbitrary_instructions_and_quote_are_not_commands(self):
        for text in ['补跑今天并删除账本', '@其他机器人 补跑今天', 'echo 连接测试', '请执行补跑今天']:
            self.assertEqual(self.run_message(dict(self.body, text={'content': text}))['status'], 'ignored')
        body = dict(self.body, text={'content': '聊天'}, quote={'text': {'content': '补跑今天'}})
        self.assertEqual(self.run_message(body)['status'], 'ignored')

    def test_stale_or_future_event_is_rejected(self):
        for delta in [-301, 10]:
            result = self.run_message(dict(self.body, create_time=self.now.timestamp() + delta))
            self.assertEqual(result['status'], 'stale_message')
        self.assertFalse(self.db.exists())

    def test_missing_allowlist_fails_closed(self):
        config = dict(self.config, allowed_user_ids=[])
        self.assertEqual(self.run_message(config=config)['status'], 'configuration_required')

    def test_malformed_envelope_and_allowlist_never_authorize_by_substring(self):
        self.assertEqual(self.run_message(config=dict(self.config,allowed_user_ids='fixture-user'))['status'],
                         'configuration_required')
        for field in ['from', 'text']:
            self.assertEqual(self.run_message(dict(self.body, **{field:None}))['status'], 'ignored')
        self.assertFalse(self.db.exists())

    def test_status_query_does_not_create_business_request(self):
        result = self.run_message(dict(self.body, text={'content': '查询进度'}))
        self.assertEqual(result['command'], 'status')
        self.assertIn('尚未接入业务执行器', result['reply'])

    def test_production_dispatch_mode_is_not_implemented(self):
        with self.assertRaises(ValueError):
            self.run_message(config=dict(self.config, mode='execute'))

    def test_public_paths_cannot_hold_state(self):
        with self.assertRaises(ValueError):
            private('fixtures/credentials.json')


if __name__ == '__main__':
    unittest.main()

class ProbeResolutionTests(unittest.TestCase):
    def test_only_verified_local_rejection_can_close_unknown_without_replay(self):
        from scripts.wecom_control import enqueue_bridge_test, claim, mark, resolve_probe_failure, latest
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'queue.sqlite'
            cfg=dict(mode='desktop',purpose='bridge_test',desktop_session_id='test-session',business_executor_enabled=False)
            now=datetime.now(timezone.utc)
            key=enqueue_bridge_test(cfg,db,now)['request_id']
            ticket=claim(cfg,db,now)
            mark(db,key,'outcome_unknown',{'phase':'desktop_dispatch_unavailable'})
            evidence=dict(observed_at=now.isoformat(),request_id=key,run_id=ticket['run_id'],session_id='test-session',
                          error='format v4 message requires a producer-owned source kind',model_execution_started=False)
            for bad in [dict(evidence,model_execution_started=True),dict(evidence,run_id='wrong'),dict(evidence,error='network failed')]:
                with self.assertRaises(ValueError):resolve_probe_failure(cfg,db,bad,now)
            with self.assertRaises(ValueError):resolve_probe_failure(dict(cfg,purpose='business'),db,evidence,now)
            self.assertEqual(latest(db)['status'],'outcome_unknown')
            result=resolve_probe_failure(cfg,db,evidence,now)
            self.assertFalse(result['replayed'])
            self.assertEqual(latest(db)['status'],'needs_attention')
            self.assertEqual(claim(cfg,db,now)['status'],'empty')
            with self.assertRaises(ValueError):resolve_probe_failure(cfg,db,evidence,now)
