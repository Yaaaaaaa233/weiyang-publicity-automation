"""Failure recovery, irreversible action guards, notification and retention contracts."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import copy
import json
import os
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
    def test_hover_moves_pointer_over_one_element_without_click(self):
        calls=[]
        def call(method,path,body=None):
            calls.append((method,path,body))
            if path.endswith('/elements'):return [{browser.ELEMENT_KEY:'card-1'}]
            if path.endswith('/displayed'):return True
            return None
        with patch.object(browser,'call',call):browser.hover_element('sid','div.show-item')
        actions=[body for method,path,body in calls if path.endswith('/actions')]
        self.assertEqual(len(actions),1)
        move=actions[0]['actions'][0]['actions'][0]
        self.assertEqual(move['type'],'pointerMove')
        self.assertEqual(move['origin'],{browser.ELEMENT_KEY:'card-1'})
        self.assertFalse(any(path.endswith('/click') for _,path,_ in calls))

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
        content=(log.folder/'events.jsonl').read_text(encoding='utf-8')
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
        self.assertTrue((protected.folder/'events.jsonl').exists());self.assertEqual(ledger.read_text(encoding='utf-8'),'keep')
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
                if command=='observe-result':return json.loads(p.read_text())
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

class BrowserImageDriftTests(unittest.TestCase):
    """The image bakes browser.py, so a stale copy must be named and blocked, not buried."""
    def test_stale_subcommand_is_named_as_a_deployment_fault(self):
        from scripts import browserctl
        usage=("usage: browser.py [-h] [--lease-token L] {status,start,observe} ...\n"
               "browser.py: error: argument command: invalid choice: 'close-window' "
               "(choose from 'status', 'start', 'observe')")
        self.assertEqual(browserctl.classify_failure(usage),'browser_image_stale')
        self.assertEqual(browserctl.classify_failure('Artifact retrieval failed'),'host bridge error')
    def test_stale_container_copy_blocks_and_records_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'local').mkdir()
            stale={'repository_sha256':'a'*64,'container_sha256':'b'*64,'match':False}
            current={'repository_sha256':'a'*64,'container_sha256':'a'*64,'match':True}
            healed={'done':False};seen=[]
            def bridge(*args,**kwargs):
                seen.append(args)
                if args==('doctor','--heal'):healed['done']=True;return current
                return current if healed['done'] else stale
            with patch.object(check_runtime,'ROOT',root),patch.object(check_runtime,'bridge',bridge), \
                 patch.dict(os.environ,{'WEIYANG_BROWSER_SELFHEAL':'0'}):
                check_runtime.IMAGE_STATE.update(checked=False,result=None)
                with self.assertRaises(Stop) as caught:check_runtime.browser_image_preflight()
                self.assertEqual(caught.exception.code,'browser_image_stale')
                records=list((root/'local/browser-image-drift').glob('drift-*.json'))
                self.assertEqual(len(records),1)
                self.assertEqual(json.loads(records[0].read_text(encoding='utf-8'))['container_sha256'],'b'*64)
                self.assertEqual(seen,[('doctor',)])
            seen.clear()
            with patch.object(check_runtime,'ROOT',root),patch.object(check_runtime,'bridge',bridge):
                check_runtime.IMAGE_STATE.update(checked=False,result=None)
                self.assertTrue(check_runtime.browser_image_preflight()['match'])
                self.assertEqual(seen,[('doctor',),('doctor','--heal'),('doctor',)])
                self.assertTrue(check_runtime.browser_image_preflight()['match'])
                self.assertEqual(len(seen),3)
    def test_lease_acquisition_runs_the_preflight_before_any_browser_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'local').mkdir()
            order=[]
            with patch.object(check_runtime,'ROOT',root), \
                 patch.object(check_runtime,'browser_runtime_preflight',side_effect=lambda *a,**k:order.append('runtime')), \
                 patch.object(check_runtime,'browser_image_preflight',side_effect=lambda *a,**k:order.append('preflight')), \
                 patch.object(check_runtime.Browser,'command',side_effect=lambda *a,**k:order.append('command')):
                with check_runtime.Browser('local/lease-fixture').reserved():pass
            self.assertEqual(order,['runtime','preflight','command','command'])

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
    def fixture(self, uncertain=False, corrupt=False, copy_suffix=None, marker='【自动整理测试】'):
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
                if args[0]=='observe':return {'url':d.editor_url(self.current)}
                if args[0]=='type':self.title=args[1]
                if args[0]=='reload':self.title=self.saved_title
                return {}
            def editor_ui(self):
                return {'url':d.editor_url(self.current),'nodes':[{'tag':'span','own_text':'更多','selector':'more'},
                                 {'tag':'a','own_text':'另存一个图文','selector':'saveas'},
                                 {'tag':'span','own_text':'保存','selector':'save'}]}
            def click(self,node):
                events.append('click_'+node['selector'])
                if node['selector']=='saveas':
                    if uncertain:raise Stop('browser_command_timeout','unknown save-as')
                    self.current='2'
                    # The platform titles a save-as copy `<source> - 拷贝` instead of
                    # inheriting the source title verbatim.
                    if copy_suffix:self.title=sample()['title']+copy_suffix
                if node['selector']=='save':self.saved_title=self.title
            def wait(self,predicate,read=None):return read()
            def snapshot(self):raise AssertionError('Whole-page read forbidden')
        b=B();self.browser=b
        def snapshot_body(browser,identifier):
            value=sample(identifier);value['title']=browser.title
            if corrupt and identifier=='2':value['images'][1]['width']=999
            return b.editor_ui()|{'url':d.editor_url(identifier)},value
        patches=[patch.object(d,'ROOT',root),patch.object(check_runtime,'ROOT',root),
                 patch.object(d,'observe',return_value=(sample(),'local/proof.json')),patch.object(d,'snapshot_body',snapshot_body)]
        for p in patches:p.start();self.addCleanup(p.stop)
        drafts=d.Drafts(b,root/'local/copies','fixture','2026-01-01',marker,
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
    def test_created_id_survives_body_read_failure_without_replaying_save_as(self):
        drafts,events=self.fixture()
        original=d.snapshot_body
        def fail_after_creation(b,identifier):
            if identifier=='2':raise Stop('body_images_loading','read incomplete')
            return original(b,identifier)
        with patch.object(d,'snapshot_body',side_effect=fail_after_creation):
            for _ in range(2):
                with self.assertRaises(Stop):
                    drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.assertEqual(events.count('click_saveas'),1)
        saved=json.loads(drafts.index_path.read_text())['copies']['1']
        self.assertEqual(saved['copy_id'],'2')
        self.assertEqual(saved['phase'],'copy_created')

    def test_unknown_save_as_is_durable_and_no_second_click(self):
        drafts,events=self.fixture(uncertain=True);article={'row':1,'is_headline':True};candidate={'id':'1','title':sample()['title']}
        for _ in range(2):
            with self.assertRaises(Stop):drafts.prepare(article,candidate)
        self.assertEqual(events.count('click_saveas'),1)
        self.assertTrue(json.loads(drafts.index_path.read_text(encoding='utf-8'))['copies']['1']['copy_reserved'])
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
    def test_save_as_copy_title_suffix_is_renamed_instead_of_rejected(self):
        """Xiumi titles a save-as copy `<source> - 拷贝`, so the copy is never titled
        exactly like its source. Demanding that exact match hard-stopped the real
        2026-10-10 run at the copy step and no test covered it."""
        drafts,events=self.fixture(copy_suffix=' - 拷贝')
        result=drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.assertEqual(result['draft_id'],'2')
        self.assertTrue(result['saved_reopened'])
        self.assertIn('type',events)
        self.assertEqual(events.count('click_save'),1)
        self.assertEqual(self.browser.saved_title,'【自动整理测试】'+sample()['title'])
        self.assertEqual(drafts.index['copies']['1']['phase'],'verified')
    def test_repeated_copy_suffix_from_a_copy_of_a_copy_is_accepted(self):
        drafts,events=self.fixture(copy_suffix=' - 拷贝 - 拷贝')
        result=drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.assertEqual(result['draft_id'],'2')
        self.assertEqual(events.count('click_save'),1)
    def test_an_edited_copy_title_is_still_never_overwritten(self):
        drafts,events=self.fixture(copy_suffix=' 人工改过')
        with self.assertRaises(Stop) as caught:
            drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.assertEqual(caught.exception.code,'copy_title_changed')
        self.assertNotIn('type',events)
        self.assertNotIn('click_save',events)
    def test_a_suffixed_title_in_save_intent_is_still_an_unknown_save(self):
        """Accepting the platform suffix must not weaken the branch that refuses to
        repeat a rename whose save outcome was never observed."""
        drafts,events=self.fixture(copy_suffix=' - 拷贝')
        drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        drafts.index['copies']['1']['phase']='save_intent';drafts.persist()
        self.browser.saved_title=sample()['title']+' - 拷贝'
        before=len(events)
        with self.assertRaises(Stop) as caught:
            drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.assertEqual(caught.exception.code,'save_outcome_unknown')
        self.assertNotIn('type',events[before:])
    def test_attach_accepts_an_inherited_title(self):
        drafts,events=self.fixture(uncertain=True)
        with self.assertRaises(Stop):drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.browser.title=sample()['title']+' - 拷贝'
        drafts.attach('1','2')
        self.assertEqual(drafts.index['copies']['1']['copy_id'],'2')
        self.assertEqual(drafts.index['copies']['1']['phase'],'copy_recovered')
    def test_attach_still_refuses_an_edited_title(self):
        drafts,events=self.fixture(uncertain=True)
        with self.assertRaises(Stop):drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.browser.title=sample()['title']+' 人工改过'
        with self.assertRaises(Stop) as caught:drafts.attach('1','2')
        self.assertEqual(caught.exception.code,'copy_recovery_invalid')
    def test_a_production_copy_keeps_the_source_title_with_no_reader_visible_prefix(self):
        """A production copy must not carry a marker: the copy is what gets composed and
        synced, so the marker would become the reader-visible article title (see
        check_sync_composition, which compares the composer's title inputs to sync_title).
        Copies stay excluded from candidates by id through the private transfer registry."""
        drafts,events=self.fixture(copy_suffix=' - 拷贝',marker='')
        result=drafts.prepare({'row':1,'is_headline':True},{'id':'1','title':sample()['title']})
        self.assertEqual(result['sync_title'],sample()['title'])
        self.assertEqual(result['sync_title'],drafts.index['copies']['1']['source_title'])
        self.assertNotIn('【',result['sync_title'])
        self.assertTrue(result['saved_reopened'])
        self.assertEqual(events.count('click_save'),1)

class CopyMarkerTests(unittest.TestCase):
    """Only a test marker or an empty (production) marker is accepted."""
    def test_only_test_markers_or_empty_are_accepted(self):
        rules={'head_sources':['head'],'tail_sources':['tail'],'evidence_paths':[]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'local').mkdir()
            with patch.object(d,'ROOT',root),patch.object(check_runtime,'ROOT',root):
                for bad in ('随手写的前缀','【别的标记】','x'*61,None):
                    with self.subTest(marker=bad),self.assertRaises(ValueError):
                        d.Drafts(None,root/'local/copies','fixture','2026-01-01',bad,rules)
                for good in ('','【自动整理测试】','【同步验证x】'):
                    with self.subTest(marker=good):
                        self.assertEqual(
                            d.Drafts(None,root/'local/copies','fixture','2026-01-01',good,rules).marker,good)

class InheritedTitleTests(unittest.TestCase):
    """The suffix rule is the whole contract, so pin it directly."""
    def test_only_the_inherited_title_and_its_repeated_suffix_are_accepted(self):
        source='栏目丨稿件'
        for observed in (source, source+' - 拷贝', source+' - 拷贝 - 拷贝'):
            self.assertTrue(d.inherited_title(observed,source),observed)
        for observed in (source+' 手改', source+' - 拷贝手改', '别的标题',
                         ' - 拷贝'+source, source+' - 拷', source+' - 拷贝 '):
            self.assertFalse(d.inherited_title(observed,source),observed)
    def test_an_empty_or_missing_source_title_accepts_nothing(self):
        for observed in ('','x',' - 拷贝'):
            self.assertFalse(d.inherited_title(observed,''))
            self.assertFalse(d.inherited_title(observed,None))
            self.assertFalse(d.inherited_title(None,'栏目丨稿件'))
    def test_the_actual_20261010_stop_title_is_accepted(self):
        """Pinned from the real incident: copy 730556517 came back titled like this and
        the old exact-match guard hard-stopped the run at `copy_title_changed`."""
        source='未央实践丨秋光漫染肩头，来一场学期中实践吧！'
        observed='未央实践丨秋光漫染肩头，来一场学期中实践吧！ - 拷贝'
        self.assertTrue(d.inherited_title(observed,source))
        # The already-renamed managed title is handled by the expected-title branch.
        self.assertFalse(d.inherited_title('【自动整理20261010】'+source,source))

class NotificationIntentTests(unittest.TestCase):
    def test_local_receipt_failure_keeps_original_message_intent_and_blocks_alternate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);state={'run_id':'fixture'}
            with patch.object(n,'configured_webhook',return_value='fixture'), \
                 patch.object(n,'deliver_result',side_effect=OSError('disk full')) as sender:
                with self.assertRaises(OSError):full_run.send_notification(folder,state,'original result')
                self.assertIn('notification_intent',json.loads((folder/'state.json').read_text(encoding='utf-8')))
                with self.assertRaises(Stop):full_run.send_notification(folder,state,'alternate failure text')
                self.assertEqual(sender.call_count,1)
    def test_success_clears_intent_and_stores_actual_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state={'run_id':'fixture'}
            with patch.object(n,'configured_webhook',return_value='fixture'), \
                 patch.object(n,'deliver_result',return_value={'status':'accepted'}):
                full_run.send_notification(Path(tmp),state,'fixture result')
            self.assertNotIn('notification_intent',state);self.assertEqual(state['notification']['status'],'accepted')
