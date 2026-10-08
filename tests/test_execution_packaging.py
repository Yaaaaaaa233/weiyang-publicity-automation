"""Failure recovery, irreversible action guards, notification and retention contracts."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_runtime, browser, draft_workflow as d, full_run, notify_wecom as n, run_log as r

class ClickTests(unittest.TestCase):
    def driver(self, specs, changed=False):
        calls=[]
        def call(method,path,body=None):
            calls.append((method,path))
            if path.endswith('/elements'):return [{browser.ELEMENT_KEY:str(i)} for i in range(len(specs))]
            i=int(path.split('/element/')[1].split('/')[0]);prop=path.rsplit('/',1)[-1]
            if prop=='click':return None
            if prop=='text' and changed and sum(p.endswith('/text') for _,p in calls)>len(specs):return 'changed'
            return specs[i][prop]
        return call,calls
    def test_hidden_and_other_title_duplicates_do_not_block_unique_exact_visible(self):
        call,calls=self.driver([{'displayed':False,'text':'target','enabled':True},
                                {'displayed':True,'text':'other','enabled':True},
                                {'displayed':True,'text':'target','enabled':True}])
        with patch.object(browser,'call',call):browser.click_element('sid','cards','target')
        self.assertEqual([p for m,p in calls if p.endswith('/click')],['/session/sid/element/2/click'])
    def test_two_identical_visible_or_disabled_or_changed_never_click(self):
        for specs,changed in [([{'displayed':True,'text':'target','enabled':True}]*2,False),
                              ([{'displayed':True,'text':'target','enabled':False}],False),
                              ([{'displayed':True,'text':'target','enabled':True}],True)]:
            call,calls=self.driver(specs,changed)
            with patch.object(browser,'call',call),self.assertRaises(browser.BrowserError):browser.click_element('sid','cards','target')
            self.assertFalse(any(p.endswith('/click') for _,p in calls))

class NotificationBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=patch.object(n,'ROOT',Path(self.tmp.name));self.p.start();self.addCleanup(self.p.stop)
        self.url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fictional'
        self.message='未央宣传运营提醒｜2026-01-01\n运行：fictional-run\n'+'中文🙂\n'*1000
    def test_lossless_unicode_and_context_under_limit(self):
        parts=n.split_message(self.message)
        self.assertEqual(''.join(p.split('\n',3)[3] for p in parts),self.message)
        self.assertTrue(all(len(p.encode())<=1800 and 'fictional-run' in p for p in parts))
    def test_partial_unknown_blocks_later_parts_and_resume_never_resends(self):
        sent=[]
        def sender(url,message):
            sent.append(message)
            if len(sent)==2:raise TimeoutError()
            return 0
        first=n.deliver_result(self.message,self.url,True,sender)
        self.assertEqual(first['status'],'outcome_unknown');self.assertEqual(len(sent),2)
        second=n.deliver_result(self.message,self.url,True,sender)
        self.assertEqual(second['status'],'outcome_unknown');self.assertEqual(len(sent),2)
    def test_all_accepted_repeat_and_dry_run_do_not_dispatch(self):
        sent=[]
        sender=lambda url,message:(sent.append(message) or 0)
        self.assertEqual(n.deliver_result(self.message,self.url,False,sender)['status'],'dry_run')
        self.assertEqual(sent,[])
        first=n.deliver_result(self.message,self.url,True,sender)
        count=len(sent);self.assertEqual(first['status'],'accepted')
        again=n.deliver_result(self.message,self.url,True,sender)
        self.assertTrue(again['duplicate']);self.assertEqual(len(sent),count)

class RunLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.p=patch.object(r,'ROOT',self.root);self.p.start();self.addCleanup(self.p.stop)
    def test_usage_is_per_request_unknown_safe_and_conflicts_rejected(self):
        log=r.RunLog('fixture','2026-01-01');t=r.now()
        record={'request_id':'call-1','model':'fixture-model','started_at':t,'finished_at':t,'source':'provider',
                'input_tokens':10,'cache_read_tokens':20,'output_tokens':3,'cache_write_tokens':None}
        self.assertTrue(log.usage(record));self.assertFalse(log.usage(record))
        self.assertEqual(log.value['usage']['input_tokens'],10)
        self.assertEqual(log.value['usage']['status'],'partial');self.assertIsNone(log.value['usage']['cost'])
        with self.assertRaises(ValueError):log.usage(dict(record,output_tokens=4))
        with self.assertRaises(ValueError):log.usage({'totalTokens':9999})
    def test_secrets_and_exception_messages_are_not_in_structured_log(self):
        log=r.RunLog('fixture','2026-01-01')
        with self.assertRaises(RuntimeError):
            with log.stage('fixture'):raise RuntimeError('secret webhook and manuscript')
        content=(log.folder/'events.jsonl').read_text()
        self.assertNotIn('secret',content);self.assertIn('RuntimeError',content)
        self.assertFalse(log.value['active'])
    def test_retention_dry_run_and_protected_ledger_survival(self):
        log=r.RunLog('fixture','2026-01-01');log.event('check','finished')
        old=(datetime.now(r.SHANGHAI)-timedelta(days=100)).isoformat()
        log.value.update(updated_at=old,protected=False);r.atomic(log.path,log.value)
        protected=r.RunLog('pending','2026-01-01');protected.event('sync','started')
        protected.value['updated_at']=old;r.atomic(protected.path,protected.value)
        ledger=self.root/'data/permanent.json';ledger.parent.mkdir();ledger.write_text('keep')
        preview=r.cleanup();self.assertEqual(len(preview['actions']),1)
        self.assertTrue((log.folder/'events.jsonl').exists())
        r.cleanup(True)
        self.assertFalse((log.folder/'events.jsonl').exists());self.assertTrue(log.path.exists())
        self.assertTrue((protected.folder/'events.jsonl').exists());self.assertEqual(ledger.read_text(),'keep')
    def test_compression_preserves_detail_and_capacity_only_warns(self):
        log=r.RunLog('fixture','2026-01-01');log.event('check','finished')
        log.value.update(updated_at=(datetime.now(r.SHANGHAI)-timedelta(days=10)).isoformat(),protected=False)
        r.atomic(log.path,log.value);result=r.cleanup(True,max_bytes=1)
        self.assertTrue(result['capacity_exceeded']);self.assertTrue((log.folder/'events.jsonl.gz').exists())


def sample(identifier='1'):
    return {'id':identifier,'title':'栏目丨稿件','text':'正文 A | B\n审核丨甲',
            'paragraphs':[{'text':'正文 A | B','style':'font-size:16px;'}, {'text':'审核丨甲','style':''}],
            'images':[{'src':'head','width':720,'height':200},{'src':'content','width':300,'height':200},
                      {'src':'tail','width':640,'height':360}], 'backgrounds':[],
            'head_style':'margin-top: 0px; margin-bottom: 0px;', 'vessel_bottom':1000,'tail_bottom':1000,
            'cover_styles':['background-image: url(cover);']}

class DraftProtectionTests(unittest.TestCase):
    def test_preservation_detects_text_image_order_dimensions_and_styles(self):
        original=sample()
        for mode in ('text','image','order','style'):
            changed=copy.deepcopy(original)
            if mode=='text':changed['text']='wrong'
            elif mode=='image':changed['images'][1]['width']=301
            elif mode=='order':changed['images'].reverse()
            else:changed['paragraphs'][0]['style']='font-size:32px;'
            self.assertFalse(d.preservation(original,changed,{'head_sources':['head'],'tail_sources':['tail']}))
    def test_title_and_credit_separator_changes_do_not_change_internal_pipe(self):
        before=sample();before['text']='正文 A | B\n审核 | 甲';before['paragraphs'][1]['text']='审核 | 甲'
        after=sample();after['title']='【测试】栏目丨稿件'
        self.assertTrue(d.preservation(before,after,{}));self.assertIn('A | B',d.normalized_credits(before['text']))
    def test_only_evidenced_additive_head_tail_and_missing_cover_spacing_block(self):
        before=sample();before['images']=before['images'][1:2]
        self.assertTrue(d.preservation(before,sample(),{'head_sources':['head'],'tail_sources':['tail']}))
        self.assertFalse(d.preservation(before,sample(),{}))
        rules={'head_sources':['head'],'tail_sources':['tail'],'evidence_paths':['local/fixture.json']}
        self.assertEqual(d.format_issues(sample(),rules),[])
        value=sample();value['cover_styles']=[];value['head_style']='margin-bottom:10px;'
        self.assertIn('cover_unverified',d.format_issues(value,rules));self.assertIn('head_spacing_unverified',d.format_issues(value,rules))
    def test_unknown_save_as_does_not_open_or_click_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();baseline=root/'local/baseline.json';baseline.parent.mkdir();baseline.write_text(json.dumps(sample()))
            class B:
                def command(self,*args):raise AssertionError('No browser operation before unknown intent rejection')
            with patch.object(d,'ROOT',root),patch.object(check_runtime,'ROOT',root):
                drafts=d.Drafts(B(),root/'local/copies','account','2026-01-01','【自动整理测试】',{'evidence_paths':[]})
                drafts.index['copies']['1']={'row':1,'source_id':'1','copy_id':None,'copy_reserved':True,'baseline':'local/baseline.json'}
                with self.assertRaises(Stop):drafts.prepare({'row':1},{'id':'1','title':'栏目丨稿件'})
    def test_external_or_wrong_platform_location_is_rejected(self):
        for url in ('https://evil.example/#/paper/for/1','http://xiumi.us/#/paper/for/1','https://xiumi.us/#/user'):
            with self.assertRaises(Stop):d.paper_id(url)

from scripts.check_runtime import Stop

class FullRunRecoveryTests(unittest.TestCase):
    def test_uncertain_submit_resume_never_rechecks_edits_or_submits_again(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'local').mkdir()
            proof=root/'local/proof.json';proof.write_text('{}')
            day=datetime.now(r.SHANGHAI).date().isoformat();run='fixture-full'
            observation={'date':day,'timezone':'Asia/Shanghai','run_id':run,'observed_at':r.now(),
                         'reservation':{'date':day,'status':'ok','complete':True,'conflicts_resolved':True,
                                        'articles':[{'row':1,'title':'栏目丨稿件','is_headline':True,'remarks':''}],
                                        'evidence_paths':['local/proof.json']},
                         'xiumi':{'status':'ok','account_confirmed':True,'search_complete':True,
                                  'evidence_paths':['local/proof.json'],'matches':[{'row':1,'candidates':[
                                      {'id':'1','title':'栏目丨稿件','identity_confirmed':True}]}]}}
            report=full_run.daily_check.evaluate(observation,day)
            for name,value in [('observation',observation),('report',report),('rules',{'evidence_paths':['local/proof.json']})]:
                (root/f'local/{name}.json').write_text(json.dumps(value))
            check={'phase':'ready_to_format','observation':'local/observation.json','report':'local/report.json'}
            config={'xiumi_account':'fictional','format_rules':'local/rules.json','composer_url':'https://xiumi.us/#/wxpack',
                    'target_account':'fictional-target'}
            events=[]
            class Ledger:
                owner='fixture';task_id='fixture'
                def __init__(self,*args):pass
                def checkpoint(self,*args):pass
                def finish(self):return 'local/proof.json'
            class Browser:
                evidence=[]
                def __init__(self,*args,**kwargs):pass
                def reserved(self):return nullcontext()
                def command(self,*args):events.append(('browser',args[0]))
            class Drafts:
                def __init__(self,*args,**kwargs):pass
                def prepare(self,*args):
                    events.append(('copy',))
                    return {'row':1,'source_id':'1','draft_id':'2','sync_title':'【测试】栏目丨稿件','is_headline':True,
                            'saved_reopened':True,'body_images_verified':True,'format_verified':True,'evidence_paths':['local/proof.json']}
            def sync(command,run,**kwargs):
                events.append(('sync',command));p=root/'local/sync-workflow'/run/'state.json';p.parent.mkdir(parents=True,exist_ok=True)
                state={'phase':'prepared','submission_reserved':False,'sync_attempt_count':0,'handoff_path':'local/proof.json'}
                if command=='submit':
                    state.update(phase='awaiting_confirmation',submission_reserved=True,sync_attempt_count=1)
                p.write_text(json.dumps(state))
                if command=='submit':raise Stop('browser_command_timeout','unknown dispatch')
                return state
            from scripts import daily_run, daily_check
            with patch.object(full_run,'ROOT',root),patch.object(check_runtime,'ROOT',root),patch.object(n,'ROOT',root), \
                 patch.object(daily_check,'ROOT',root),patch.object(daily_run,'ROOT',root), \
                 patch.object(daily_run,'execute',return_value=check) as checked,patch.object(daily_run,'Ledger',Ledger), \
                 patch.object(full_run,'Browser',Browser),patch.object(d,'Drafts',Drafts),patch.object(full_run,'sync_call',sync), \
                 patch.object(n,'configured_webhook',return_value='fictional'),patch.object(n,'deliver_result',return_value={'status':'accepted'}):
                with self.assertRaises(Stop):full_run.execute(config,run,True)
                result=full_run.execute(config,run,True)
                self.assertEqual(result['phase'],'awaiting_confirmation')
                self.assertEqual(checked.call_count,1)
                self.assertEqual(events.count(('copy',)),1)
                self.assertEqual(events.count(('sync','submit')),1)
                self.assertEqual(full_run.execute(config,run,True),result)
                self.assertEqual(checked.call_count,1)
    def test_test_and_generated_copies_are_not_semantic_candidates(self):
        from scripts.xiumi_scan import match_candidates
        articles=[{'row':1,'title':'体育丨比赛报名','unit':'体育部'}]
        copies=[{'id':str(i),'title':prefix+'体育丨比赛报名'} for i,prefix in enumerate(('【自动整理20260101】','【全流程验证1007】'))]
        self.assertEqual(match_candidates(articles,copies)[0]['candidates'],[])

class ReportingAndIdentityTests(unittest.TestCase):
    def test_fresh_and_cached_tokens_have_separate_prices_and_missing_is_unknown(self):
        from scripts.run_report import estimate
        record={'model':'fixture','input_tokens':1000,'cache_read_tokens':2000,'output_tokens':10,'cache_write_tokens':0}
        price={'fixture':{'source':'fictional-price-source','as_of':'2026-01-01','currency':'CNY',
                          'input_tokens_per_million':10,'cache_read_tokens_per_million':1,
                          'output_tokens_per_million':20,'cache_write_tokens_per_million':0}}
        self.assertEqual(estimate([record],price)['amount'],'0.0122')
        self.assertEqual(estimate([dict(record,input_tokens=None)],price)['status'],'unknown')
        self.assertEqual(estimate([record],{})['status'],'unknown')
    def test_body_decisions_cannot_reference_another_candidate_or_run(self):
        request={'run_id':'fixture','date':'2026-01-01','matches':[{'row':1,'candidates':[
                    {'id':'1','title':'title','body_observation':'local/body-1.json'},
                    {'id':'2','title':'other','body_observation':'local/body-2.json'}]}]}
        decision={'run_id':'fixture','date':'2026-01-01','matches':[{'row':1,'id':'1','title':'title',
                    'evidence_paths':['local/body-1.json','local/body-2.json'],
                    'body_checks':{k:{'evidence_paths':['local/body-1.json']} for k in ('topic','department','date','week')},
                    'candidate_reviews':[{'id':'1','evidence_paths':['local/body-1.json']},
                                         {'id':'2','evidence_paths':['local/body-2.json']}]}]}
        full_run.validate_body_decisions(decision,request)
        bad=copy.deepcopy(decision);bad['matches'][0]['body_checks']['date']['evidence_paths']=['local/body-2.json']
        with self.assertRaises(ValueError):full_run.validate_body_decisions(bad,request)
        bad=copy.deepcopy(decision);bad['run_id']='other'
        with self.assertRaises(ValueError):full_run.validate_body_decisions(bad,request)
    def test_body_credit_like_text_is_preserved_outside_final_credit_block(self):
        value='审核 | 这是正文中的例子\n其他正文\n审核 | 甲'
        self.assertEqual(d.normalized_credits(value),'审核 | 这是正文中的例子\n其他正文\n审核丨甲')
    def test_empty_component_guard_denies_hidden_media_before_mutation(self):
        with patch.object(browser,'call',return_value={'empty':False}) as driver,self.assertRaises(browser.BrowserError):
            browser.empty_component('fixture','component')
        self.assertEqual(driver.call_count,1)
        self.assertIn('iframe',driver.call_args.args[2]['script'])

class CopyLifecycleTests(unittest.TestCase):
    def fixture(self, uncertain=False, corrupt=False):
        from urllib.parse import urlsplit
        root=Path(self.tmp.name).resolve();proof=root/'local/proof.json';proof.parent.mkdir();proof.write_text(json.dumps(sample()))
        events=[]
        class B:
            current='1';title=sample()['title'];saved_title=sample()['title'];evidence=[]
            def command(self,*args):
                events.append(args[0])
                if args[0]=='windows':return {'windows':[]}
                if args[0] in ('open','new-window'):
                    self.current=d.paper_id(args[1])
                    return {'handle':'new','previous_handle':'old'}
                if args[0]=='type':self.title=args[1]
                if args[0]=='reload':self.title=self.saved_title
                return {}
            def snapshot(self):
                return {'nodes':[{'tag':'span','own_text':'更多','selector':'more'},
                                 {'tag':'a','own_text':'另存一个图文','selector':'saveas'},
                                 {'tag':'span','own_text':'保存','selector':'save'}]}
            def click(self,node):
                events.append('click_'+node['selector'])
                if node['selector']=='saveas':
                    if uncertain:raise Stop('browser_command_timeout','unknown save-as')
                    self.current='2'
                if node['selector']=='save':self.saved_title=self.title
            def wait(self,predicate):return {'url':d.editor_url(self.current)}
        b=B()
        def snapshot_body(browser,identifier):
            value=sample(identifier);value['title']=browser.title
            if corrupt and identifier=='2':value['images'][1]['width']=999
            return b.snapshot()|{'url':d.editor_url(identifier)},value
        patches=[patch.object(d,'ROOT',root),patch.object(check_runtime,'ROOT',root),
                 patch.object(d,'observe',return_value=(sample(),'local/proof.json')),patch.object(d,'snapshot_body',snapshot_body)]
        for p in patches:p.start();self.addCleanup(p.stop)
        drafts=d.Drafts(b,root/'local/copies','fixture','2026-01-01','【自动整理测试】',
                       {'head_sources':['head'],'tail_sources':['tail'],'evidence_paths':['local/proof.json']},known_ids=['1','3'])
        return drafts,events
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def test_conforming_copy_save_reopen_and_repeat_never_create_again(self):
        drafts,events=self.fixture();article={'row':1,'is_headline':True};candidate={'id':'1','title':sample()['title']}
        result=drafts.prepare(article,candidate)
        self.assertTrue(result['format_verified']);self.assertEqual(result['draft_id'],'2')
        self.assertTrue(result['saved_reopened']);self.assertIn('close-window',events)
        drafts.prepare(article,candidate)
        self.assertEqual(events.count('click_saveas'),1);self.assertEqual(events.count('click_save'),1)
    def test_unknown_save_as_is_durable_and_no_second_click(self):
        drafts,events=self.fixture(uncertain=True);article={'row':1,'is_headline':True};candidate={'id':'1','title':sample()['title']}
        for _ in range(2):
            with self.assertRaises(Stop):drafts.prepare(article,candidate)
        self.assertEqual(events.count('click_saveas'),1)
        self.assertTrue(json.loads(drafts.index_path.read_text())['copies']['1']['copy_reserved'])
    def test_body_corruption_stops_before_title_or_save(self):
        drafts,events=self.fixture(corrupt=True)
        with self.assertRaises(Stop):drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.assertNotIn('type',events);self.assertNotIn('click_save',events)
    def test_existing_before_save_as_cannot_be_attached_as_test_copy(self):
        drafts,events=self.fixture(uncertain=True)
        with self.assertRaises(Stop):drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        before=len(events)
        with self.assertRaises(Stop):drafts.attach('1','3')
        self.assertEqual(len(events),before)

class NotificationIntentTests(unittest.TestCase):
    def test_local_receipt_failure_keeps_original_message_intent_and_blocks_alternate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);state={'run_id':'fixture'}
            with patch.object(n,'configured_webhook',return_value='fixture'), \
                 patch.object(n,'deliver_result',side_effect=OSError('disk full')) as sender:
                with self.assertRaises(OSError):full_run.send_notification(folder,state,'original result')
                self.assertIn('notification_intent',json.loads((folder/'state.json').read_text()))
                with self.assertRaises(Stop):full_run.send_notification(folder,state,'alternate failure text')
                self.assertEqual(sender.call_count,1)
    def test_success_clears_intent_and_stores_actual_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state={'run_id':'fixture'}
            with patch.object(n,'configured_webhook',return_value='fixture'), \
                 patch.object(n,'deliver_result',return_value={'status':'accepted'}):
                full_run.send_notification(Path(tmp),state,'fixture result')
            self.assertNotIn('notification_intent',state);self.assertEqual(state['notification']['status'],'accepted')
