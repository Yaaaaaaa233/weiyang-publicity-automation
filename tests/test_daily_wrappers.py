import copy
from contextlib import closing, contextmanager
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import browser, check_runtime, daily_run, taskctl, wps_read, xiumi_scan
from scripts.read_wps_reservation import SHANGHAI


class WrappersTests(unittest.TestCase):
    def test_late_english_login_overlay_stops_before_tools_click(self):
        class Fake:
            clicks=[]
            def snapshot(self):
                return {'nodes':[{'tag':'span', 'own_text':'', 'rendered_text':'Sign In Now'}]}
            def click(self, node): self.clicks.append(node)
        fake=Fake()
        with self.assertRaises(check_runtime.Stop) as raised:
            wps_read.click_navigation(fake, ('Tools','效率'))
        self.assertEqual(raised.exception.code, 'wps_login_required')
        self.assertEqual(fake.clicks, [])

    def test_intercepted_click_reobserves_login_without_replaying(self):
        class Fake:
            snapshots=0
            clicks=0
            def snapshot(self):
                self.snapshots+=1
                if self.snapshots==2:
                    return {'text':'Inviting you to log in to edit the document', 'nodes':[]}
                return {'nodes':[{'id':'ToolsTab','tag':'div','class':'tab-btn','own_text':'',
                                  'rendered_text':'Tools','selector':'tools'}]}
            def click(self, node):
                self.clicks+=1
                raise check_runtime.Stop('element click intercepted', 'blocked')
        fake=Fake()
        with self.assertRaises(check_runtime.Stop) as raised:
            wps_read.click_navigation(fake, ('Tools','效率'))
        self.assertEqual(raised.exception.code, 'wps_login_required')
        self.assertEqual(fake.clicks, 1)
        self.assertFalse(wps_read.login_required({'text':'Loading', 'nodes':[{'class':'shadow'}]}))

    def test_native_windows_browser_commands_refuse_before_creating_files(self):
        with tempfile.TemporaryDirectory() as d, patch.object(browser,'ROOT',Path(d)/'not-created'), \
                patch.object(browser,'fcntl',None), patch('sys.argv',['browser.py','start']):
            with self.assertRaises(browser.BrowserError) as raised:
                browser.main()
            self.assertEqual(raised.exception.code,'unsupported platform')
            self.assertFalse(browser.ROOT.exists())

    def test_navigation_clicks_observed_tab_parent_and_reobserves_rejected_preflight(self):
        class Fake:
            snapshots=0
            clicks=[]
            def snapshot(self):
                self.snapshots+=1
                return {'nodes':[{'id':'ToolsTab','tag':'div','class':'tab-btn','own_text':'',
                                  'rendered_text':'Tools','selector':'moving-parent'},
                                 {'id':'','tag':'span','class':'text','own_text':'Tools',
                                  'rendered_text':'Tools','selector':'covered-child'}]}
            def click(self,node):
                self.clicks.append(node['selector'])
                if len(self.clicks)==1:
                    raise check_runtime.Stop('ambiguous click','preflight rejected')
        fake=Fake()
        wps_read.click_navigation(fake,('Tools','效率'))
        self.assertEqual(fake.clicks,['#ToolsTab','#ToolsTab'])
        self.assertEqual(fake.snapshots,2)

    def test_old_complete_log_and_mixed_logs_are_rejected(self):
        def snapshot(lines):
            return {'nodes':[{'class':'concent','rendered_text':v} for v in lines]}
        old=['WY_BEGIN wps-1000000 date chunks','WY_END wps-1000000 checksum']
        self.assertFalse(wps_read.execution_finished(snapshot(old),2000,2001))
        current=['WY_BEGIN wps-2000000 date chunks','WY_END wps-2000000 checksum']
        self.assertTrue(wps_read.execution_finished(snapshot(current),2000,2001))
        self.assertFalse(wps_read.execution_finished(snapshot(current+old),2000,2001))
        self.assertFalse(wps_read.execution_finished(snapshot(current[:1]),2000,2001))

    def test_source_change_never_clicks_run_or_overwrites_source(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source.json'; source.write_text(json.dumps({'source':'changed code'}))
            top={'url':'https://observed/document','text':'',
                 'nodes':[{'tag':'iframe','id':'excelIde','own_text':''}]}
            ide={'nodes':[{'selector':'shared','class':'listItem','own_text':'','tag':'div'},
                          {'selector':'shared > heading','class':'','own_text':'Document Shared Scripts','tag':'div'},
                          {'selector':'shared > script','class':'','own_text':wps_read.NAME,'tag':'div'},
                          {'selector':'textarea','class':'inputarea','own_text':'','tag':'textarea'}]}
            class Fake:
                clicks=[]
                def wait(self,predicate,frame=None,*args):
                    result=ide if frame else top
                    assert predicate(result)
                    return result
                def click(self,node,frame=None): self.clicks.append(node['own_text'])
                def command(self,*args):
                    assert args[0]=='copy-editor'
                    return {'local_artifact':str(source)}
            fake=Fake()
            with patch.object(wps_read,'find_window'), self.assertRaises(check_runtime.Stop) as raised:
                wps_read.run(fake,'observed',datetime.now(SHANGHAI).date().isoformat())
            self.assertEqual(raised.exception.code,'shared_source_changed')
            self.assertEqual(fake.clicks,[wps_read.NAME])
            self.assertEqual(json.loads(source.read_text())['source'],'changed code')

    def test_browser_workflow_lease_blocks_other_commands_and_stale_takeover(self):
        with tempfile.TemporaryDirectory() as d, patch.object(browser,'LEASE',Path(d)/'lease.json'):
            browser.workflow_lease('lease-acquire','a'*32)
            with self.assertRaises(browser.BrowserError):
                browser.workflow_lease('observe',None)
            with self.assertRaises(browser.BrowserError):
                browser.workflow_lease('lease-acquire','b'*32)
            with self.assertRaises(browser.BrowserError):
                browser.workflow_lease('lease-release','b'*32)
            browser.workflow_lease('observe','a'*32)
            browser.workflow_lease('lease-release','a'*32)
            browser.workflow_lease('observe',None)

    def test_only_shared_group_can_select_script(self):
        nodes=[{'selector':'shared','class':'listItem','own_text':''},
               {'selector':'shared > heading','class':'title-text','own_text':'Document Shared Scripts'},
               {'selector':'personal','class':'listItem','own_text':''},
               {'selector':'personal > script','class':'','own_text':wps_read.NAME}]
        matches,_=wps_read.shared_script({'nodes':nodes})
        self.assertEqual(matches,[])
        nodes.append({'selector':'shared > script','class':'','own_text':wps_read.NAME})
        self.assertEqual(len(wps_read.shared_script({'nodes':nodes})[0]),1)

    def test_loading_expanded_shared_group_is_not_treated_as_collapsed(self):
        nodes=[{'selector':'shared','class':'listItem','own_text':''},
               {'selector':'shared > heading','class':'','own_text':'Document Shared Scripts'},
               {'selector':'shared > arrow','class':'kd-icon-arrow_triangle_down','own_text':''},
               {'selector':'personal > arrow','class':'kd-icon-arrow_triangle_right','own_text':''}]
        self.assertFalse(wps_read.shared_collapsed({'nodes':nodes}))
        nodes[2]['class']='kd-icon-arrow_triangle_right'
        self.assertTrue(wps_read.shared_collapsed({'nodes':nodes}))

    def test_candidate_similarity_never_confirms_identity_and_versions_block(self):
        articles=[{'row':6,'title':'示例体育丨第二周活动'}]
        cards=[{'id':'1','title':'示例体育丨第三周活动','url':'observed'}]
        match=xiumi_scan.match_candidates(articles,cards)[0]
        self.assertEqual(match['candidates'],[])
        self.assertEqual(match['excluded_candidates'][0]['exclusion_reasons'],['different_week'])
        cards[0]['title']=articles[0]['title']
        self.assertTrue(xiumi_scan.match_candidates(articles,cards)[0]['candidates'][0]['identity_confirmed'])
        cards.append(dict(cards[0],id='2'))
        self.assertTrue(all(not c['identity_confirmed'] for c in xiumi_scan.match_candidates(articles,cards)[0]['candidates']))

    def test_wrong_library_account_and_unknown_pagination_cannot_be_complete(self):
        snapshot={'nodes':[{'class':'nickname','own_text':'expected','tag':'span'},
                           {'class':'','own_text':'已保存图文:1','tag':'div'}]}
        with self.assertRaises(check_runtime.Stop):
            xiumi_scan.parse_page(snapshot,'wrong')
        with self.assertRaises(check_runtime.Stop):
            xiumi_scan.parse_page(snapshot,'expected')

    def test_restoration_only_manuscript_cannot_be_auto_matched(self):
        article={'row':6,'title':'示例稿'}
        card={'id':'unavailable-slot-1-0','title':'示例稿','available':False,'url':None}
        match=xiumi_scan.match_candidates([article],[card])[0]
        self.assertFalse(match['candidates'][0]['identity_confirmed'])

    def test_decisions_need_current_run_candidate_and_real_evidence(self):
        with tempfile.TemporaryDirectory() as d, patch.object(taskctl,'ROOT',Path(d).resolve()):
            (Path(d)/'local').mkdir()
            (Path(d)/'local/proof.json').write_text('{}')
            observation={'date':'2026-10-05','run_id':'run',
                         'xiumi':{'evidence_paths':[],'matches':[{'row':6,'candidates':[{'id':'a','title':'正式标题','identity_confirmed':False}]}]}}
            decisions={'date':'2026-10-05','run_id':'run','matches':[{'row':6,'id':'a','title':'正式标题',
                       'identity_reason':'实际正文、部门和周次一致，唯一版本','evidence_paths':['local/proof.json']}]}
            decisions['matches'][0]['body_checks']={k:{'result':'matched','reason':'Observed current body facts','evidence_paths':['local/proof.json']} for k in ('topic','department','date','week','unique_version')}
            result=daily_run.apply_decisions(copy.deepcopy(observation),decisions)
            self.assertTrue(result['xiumi']['matches'][0]['candidates'][0]['identity_confirmed'])
            for key,value in (('run_id','old'),('date','2026-09-21')):
                invalid=copy.deepcopy(decisions); invalid[key]=value
                with self.assertRaises(ValueError):
                    daily_run.apply_decisions(copy.deepcopy(observation),invalid)
            decisions['matches'][0]['id']='not-observed'
            with self.assertRaises(ValueError):
                daily_run.apply_decisions(observation,decisions)


class DailyEntryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name).resolve()
        for module in (daily_run,check_runtime,taskctl):
            p=patch.object(module,'ROOT',self.root); p.start(); self.addCleanup(p.stop)
        p=patch('scripts.daily_check.ROOT',self.root); p.start(); self.addCleanup(p.stop)
        (self.root/'local').mkdir(); (self.root/'local/proof.json').write_text('{}')
        now=datetime.now(SHANGHAI)
        self.day=now.date().isoformat()
        self.observation={'date':self.day,'timezone':'Asia/Shanghai','run_id':'wps-fresh',
                          'observed_at':now.isoformat(),
                          'reservation':{'date':self.day,'status':'ok','complete':True,'conflicts_resolved':True,
                                         'evidence_paths':['local/proof.json'],
                                         'articles':[{'row':6,'title':'示例稿','is_headline':True,'remarks':''}]}}
        self.receipt={'status':'ok','account_confirmed':True,'search_complete':True,
                      'evidence_paths':['local/proof.json'],
                      'matches':[{'row':6,'candidates':[{'id':'a','title':'示例稿','identity_confirmed':True}]}]}
        class FakeBrowser:
            evidence=['local/proof.json']
            def __init__(self,*args): pass
            def command(self,*args): return {}
            @contextmanager
            def reserved(self): yield self
        p=patch.object(daily_run,'Browser',FakeBrowser); p.start(); self.addCleanup(p.stop)
        self.config={'xiumi_account':'fictional','wps_url':'observed','xiumi_library_url':'observed'}

    def test_pipeline_and_same_run_replay_suppression(self):
        with patch.object(wps_read,'run',return_value=self.observation) as reader, patch.object(xiumi_scan,'run',return_value=self.receipt):
            state=daily_run.execute(self.config,'run-a')
            self.assertEqual(state['phase'],'ready_to_format')
            self.assertEqual(state['notification']['status'],'not_sent')
            again=daily_run.execute(self.config,'run-a')
            self.assertEqual(again['task_id'],state['task_id'])
            self.assertEqual(reader.call_count,1)
            with closing(taskctl.connect(self.root/'data/tasks.sqlite')) as db:
                self.assertIsNone(taskctl.task(db,state['task_id'])['owner'])
                self.assertEqual(taskctl.task(db,state['task_id'])['status'],'ready_for_review')
            self.assertTrue((self.root/state['handoff']).exists())

    def test_prior_day_run_identity_is_rejected_before_browser_access(self):
        folder=self.root/'local/daily-runner/runs/old'; folder.mkdir(parents=True)
        (folder/'state.json').write_text(json.dumps({'date':'2000-01-01'}))
        with patch.object(wps_read,'run') as reader, self.assertRaises(ValueError):
            daily_run.execute(self.config,'old')
        reader.assert_not_called()

    def test_empty_day_skips_xiumi_and_generates_notice(self):
        self.observation['reservation']['articles']=[]
        with patch.object(wps_read,'run',return_value=self.observation), patch.object(xiumi_scan,'run') as scanner:
            state=daily_run.execute(self.config,'empty')
            self.assertEqual(state['phase'],'no_reservations')
            scanner.assert_not_called()
            self.assertIn('无预约',(self.root/state['message']).read_text(encoding='utf-8'))

    def test_login_failure_generates_specific_alert(self):
        with patch.object(wps_read,'run',side_effect=check_runtime.Stop('wps_login_required','login')):
            state=daily_run.execute(self.config,'login')
            self.assertEqual(state['phase'],'check_failed')
            self.assertIn('WPS 登录异常',(self.root/state['message']).read_text(encoding='utf-8'))

    def test_busy_lock_is_not_removed_or_taken_over(self):
        (self.root/'data').mkdir(); path=self.root/'data/daily-runner.lock'; path.write_text('existing')
        with self.assertRaises(check_runtime.Stop):
            daily_run.execute(self.config,'busy')
        self.assertEqual(path.read_text(),'existing')

    def test_notification_unknown_is_retained_and_never_resent_for_same_run(self):
        with patch.object(wps_read,'run',return_value=self.observation), patch.object(xiumi_scan,'run',return_value=self.receipt), \
                patch('scripts.notify_wecom.configured_webhook',return_value='private'), \
                patch('scripts.notify_wecom.deliver',return_value={'status':'outcome_unknown'}) as sender:
            state=daily_run.execute(self.config,'notify','send')
            self.assertEqual(state['notification']['status'],'outcome_unknown')
            daily_run.execute(self.config,'notify','send')
            self.assertEqual(sender.call_count,1)

    def test_reviewed_preview_can_send_once_without_rescanning(self):
        with patch.object(wps_read,'run',return_value=self.observation) as reader, patch.object(xiumi_scan,'run',return_value=self.receipt), \
                patch('scripts.notify_wecom.configured_webhook',return_value='private'), \
                patch('scripts.notify_wecom.deliver',return_value={'status':'accepted'}) as sender:
            daily_run.execute(self.config,'preview')
            state=daily_run.execute(self.config,'preview','send')
            daily_run.execute(self.config,'preview','send')
            self.assertEqual(reader.call_count,1)
            self.assertEqual(sender.call_count,1)
            self.assertEqual(state['notification']['status'],'accepted')


if __name__=='__main__':
    unittest.main()
