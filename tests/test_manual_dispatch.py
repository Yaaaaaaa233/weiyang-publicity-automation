from contextlib import closing
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from scripts.wecom_control import handle, claim, mark, latest, connect, enqueue_bridge_test
from scripts.full_run import identity_review_rows


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'queue.sqlite'
        self.now = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
        self.config = dict(mode='desktop', purpose='bridge_test', bot_id='fixture-bot',
                           desktop_session_id='fixture-session', allowed_user_ids=['fixture-user'], allowed_chat_ids=['fixture-group'])
        self.body = dict(aibotid='fixture-bot', msgid='fixture-message', chattype='group', chatid='fixture-group',
                         msgtype='text', text={'content': '补跑今天'}, **{'from': {'userid': 'fixture-user'}})
        handle(self.config, self.body, self.db, self.now)

    def test_two_workers_only_claim_once(self):
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: claim(self.config, self.db, self.now), range(2)))
        self.assertEqual(sorted(r['status'] for r in results), ['busy', 'dispatch_reserved'])

    def test_unknown_dispatch_stays_blocked_after_restart(self):
        ticket = claim(self.config, self.db, self.now)
        mark(self.db, ticket['request_id'], 'outcome_unknown')
        self.assertEqual(claim(self.config, self.db, self.now)['status'], 'busy')
        with self.assertRaises(ValueError): mark(self.db, ticket['request_id'], 'running')

    def test_desktop_ack_is_not_business_completion(self):
        ticket = claim(self.config, self.db, self.now)
        mark(self.db, ticket['request_id'], 'running', {'message_id': 'fixture-inbox-id'})
        self.assertEqual(latest(self.db)['status'], 'running')
        mark(self.db, ticket['request_id'], 'bridge_verified')
        self.assertEqual(latest(self.db)['status'], 'bridge_verified')
        with self.assertRaises(ValueError): mark(self.db, ticket['request_id'], 'running')

    def test_past_day_is_not_run_as_today(self):
        self.assertEqual(claim(self.config, self.db, self.now+timedelta(days=1))['status'], 'expired')
        self.assertEqual(claim(self.config, self.db, self.now+timedelta(days=1))['status'], 'empty')

    def test_queue_only_is_never_claimed_by_desktop_worker(self):
        with closing(connect(self.db)) as db, db: db.execute("UPDATE requests SET mode='queue_only'")
        self.assertEqual(claim(self.config, self.db, self.now)['status'], 'empty')

    def test_business_requires_executor_configuration(self):
        config = dict(self.config, purpose='business', business_executor_enabled=False)
        self.assertEqual(claim(config, self.db, self.now)['status'], 'business_executor_disabled')
        self.assertEqual(latest(self.db)['status'], 'queued')

    def test_test_request_cannot_be_promoted_to_business_after_enqueue(self):
        config = dict(self.config, purpose='business', business_executor_enabled=True)
        self.assertEqual(claim(config, self.db, self.now)['status'], 'configuration_changed')
        self.assertEqual(latest(self.db)['status'], 'needs_attention')

    def test_target_session_change_needs_a_new_authorized_command(self):
        config = dict(self.config, desktop_session_id='other-session')
        self.assertEqual(claim(config, self.db, self.now)['status'], 'configuration_changed')

    def test_busy_click_does_not_hide_original_status(self):
        ticket = claim(self.config, self.db, self.now)
        mark(self.db, ticket['request_id'], 'outcome_unknown')
        handle(self.config, dict(self.body, msgid='second-message'), self.db, self.now)
        self.assertEqual(latest(self.db)['status'], 'outcome_unknown')
        result = handle(self.config, dict(self.body, msgid='query-message', text={'content':'查询进度'}), self.db, self.now)
        self.assertIn('结果未知', result['reply'])

    def test_old_queue_schema_migrates_without_losing_dedup(self):
        other = Path(self.tmp.name) / 'old.sqlite'
        import sqlite3
        with closing(sqlite3.connect(other)) as db, db:
            db.execute('CREATE TABLE requests (key TEXT PRIMARY KEY,date TEXT,observed_at TEXT,command TEXT,status TEXT)')
            db.execute("INSERT INTO requests VALUES ('old','2026-10-14','now','rerun_today','queued')")
        with closing(connect(other)) as db, db:
            old = db.execute('SELECT * FROM requests WHERE key=\'old\'').fetchone()
            self.assertEqual(old['mode'], 'queue_only')
            self.assertEqual(old['status'], 'queued')


class MixedIdentityTests(unittest.TestCase):
    def test_full_entry_collects_only_reviewable_rows_without_formatting_or_sync(self):
        import json
        from contextlib import nullcontext
        from unittest.mock import patch
        from scripts import full_run, daily_run, daily_check, check_runtime, draft_workflow
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); (root/'local').mkdir()
            day = datetime.now(full_run.SHANGHAI).date().isoformat()
            obs = {'date':day,'run_id':'fixture-mixed','reservation':{'articles':[{'row':22},{'row':23}]},
                   'xiumi':{'matches':[{'row':22,'candidates':[{'id':'new','title':'fixture headline'}]},
                                       {'row':23,'candidates':[]}]}}
            report = {'status':'needs_attention','ready_to_format':False,'issues':[
                {'code':'candidate_identity_unresolved','row':22},{'code':'already_transferred','row':23}]}
            for name,value in [('observation',obs),('report',report)]:
                (root/f'local/{name}.json').write_text(json.dumps(value))
            check = {'phase':'needs_attention','observation':'local/observation.json','report':'local/report.json'}
            class Ledger:
                owner = 'fixture-owner'
                def __init__(self,*args): pass
                def checkpoint(self,*args): pass
                def finish(self): return 'local/handoff.json'
            class Browser:
                evidence = []
                def __init__(self,*args,**kwargs): pass
                def reserved(self): return nullcontext()
                def command(self,*args): pass
            with patch.object(full_run,'ROOT',root), patch.object(check_runtime,'ROOT',root), \
                 patch.object(daily_run,'execute',return_value=check), patch.object(daily_run,'Ledger',Ledger), \
                 patch.object(daily_run,'local_lock',side_effect=lambda *_:nullcontext()), \
                 patch.object(full_run,'Browser',Browser), patch.object(daily_check,'evaluate',return_value=report), \
                 patch.object(daily_check,'check_evidence'), patch.object(draft_workflow,'body_signature',return_value='fixture-sha'), \
                 patch.object(draft_workflow,'observe',return_value=({},'local/body.json')) as observed, \
                 patch.object(draft_workflow,'Drafts') as drafts, patch.object(full_run,'sync_call') as sync:
                result = full_run.execute({'xiumi_account':'fixture'},'fixture-mixed',False)
                request = json.loads((root/result['identity_request']).read_text())
                self.assertEqual(result['phase'],'agent_identity_required')
                self.assertEqual([r['row'] for r in request['matches']],[22])
                self.assertEqual(observed.call_count,1)
                drafts.assert_not_called(); sync.assert_not_called()
                self.assertEqual(json.loads((root/'local/report.json').read_text()),report)

    def test_transferred_rows_do_not_hide_unresolved_new_headline(self):
        report = {'status':'needs_attention', 'ready_to_format':False, 'issues':[
            {'code':'candidate_identity_unresolved','row':22}, {'code':'already_transferred','row':23}]}
        before = copy.deepcopy(report)
        self.assertEqual(identity_review_rows(report), {22})
        self.assertEqual(report, before)  # The transfer issue and formatting gate remain intact.

    def test_login_failure_does_not_open_body_collection(self):
        self.assertEqual(identity_review_rows({'status':'check_failed','issues':[
            {'code':'candidate_identity_unresolved','row':22}]}), set())


class LocalProbeTests(unittest.TestCase):
    def test_local_probe_has_explicit_origin_and_cannot_authorize_business(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'queue.sqlite'
            config={'mode':'desktop','purpose':'bridge_test','business_executor_enabled':False,
                    'desktop_session_id':'fixture-session'}
            result=enqueue_bridge_test(config,db)
            self.assertEqual(result['origin'],'local_bridge_test')
            ticket=claim(config,db)
            self.assertEqual(ticket['origin'],'local_bridge_test')
            self.assertEqual(ticket['purpose'],'bridge_test')
            for change in ({'purpose':'business'},{'business_executor_enabled':True},{'desktop_session_id':''}):
                with self.assertRaises(ValueError): enqueue_bridge_test(dict(config,**change),db)


if __name__ == '__main__': unittest.main()
