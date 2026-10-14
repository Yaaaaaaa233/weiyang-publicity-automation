"""Actual Xiumi icon menu shape and recoverable component deletion contract."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from scripts import draft_workflow as d, check_runtime, full_run
from scripts.check_runtime import Stop


def menu():
    return [
        {'tag':'div','class':'prompt','own_text':'删除','selector':'trash > prompt'},
        {'tag':'button','class':'btn btn-danger op-bar-btn','parent_class':'op-dc-slot dc-cp-delete',
         'selector':'menu > li > slot > button','disabled':False,'own_text':'','rendered_text':''},
        {'tag':'span','class':'op-bar-icon delete','selector':'menu > li > slot > button > span'},
        {'tag':'span','own_text':'保存','selector':'save'}]


class DeleteControlTests(unittest.TestCase):
    def test_icon_button_is_selected_instead_of_drop_receiver(self):
        self.assertEqual(d.delete_control(menu())['tag'],'button')
        self.assertEqual(d.delete_control(menu())['own_text'],'')

    def test_drag_receiver_alone_is_not_a_button(self):
        with self.assertRaises(Stop) as caught:d.delete_control(menu()[:1])
        self.assertEqual(caught.exception.code,'delete_control_missing')

    def test_disabled_or_wrong_menu_is_refused(self):
        for field,value in [('disabled',True),('parent_class','unrelated')]:
            nodes=menu();nodes[1][field]=value
            with self.assertRaises(Stop):d.delete_control(nodes)

    def test_multiple_delete_buttons_remain_ambiguous(self):
        nodes=menu();other=copy.deepcopy(nodes[1:3])
        for n in other:n['selector']=n['selector'].replace('menu','other-menu')
        with self.assertRaises(Stop) as caught:d.delete_control(nodes+other)
        self.assertEqual(caught.exception.code,'delete_control_ambiguous')


class ScopeRediscoveryTests(unittest.TestCase):
    def reader(self,url=None):
        class B:
            def command(self,*args):return {'url':url or d.editor_url('123')}
        return B()

    def test_race_rediscovers_paths_instead_of_climbing_old_path(self):
        with patch.object(d,'_body_scope_once',side_effect=[Stop('inspection_scope_changed','race'),'fresh-scope']) as find, \
             patch.object(d.time,'sleep'):
            self.assertEqual(d.body_scope(self.reader(),'123'),'fresh-scope')
        self.assertEqual(find.call_count,2)

    def test_twelve_failed_discoveries_stop(self):
        with patch.object(d,'_body_scope_once',side_effect=Stop('inspection_scope_changed','race')) as find, \
             patch.object(d.time,'sleep'),self.assertRaises(Stop):d.body_scope(self.reader(),'123')
        self.assertEqual(find.call_count,12)

    def test_editor_can_finish_loading_after_the_old_three_attempt_limit(self):
        with patch.object(d,'_body_scope_once',side_effect=[Stop('inspection_scope_changed','loading')]*4+['ready']) as find, \
             patch.object(d.time,'sleep'):
            self.assertEqual(d.body_scope(self.reader(),'123'),'ready')
        self.assertEqual(find.call_count,5)

    def test_loading_deadline_stops_even_before_attempt_limit(self):
        with patch.object(d,'_body_scope_once',side_effect=Stop('inspection_scope_changed','loading')) as find, \
             patch.object(d.time,'monotonic',side_effect=[0,31]),self.assertRaises(Stop):
            d.body_scope(self.reader(),'123')
        self.assertEqual(find.call_count,1)

    def test_identity_change_is_not_retried(self):
        with patch.object(d,'_body_scope_once',side_effect=Stop('inspection_scope_changed','race')) as find, \
             self.assertRaises(Stop) as caught:d.body_scope(self.reader(d.editor_url('456')),'123')
        self.assertEqual(caught.exception.code,'draft_identity_changed');self.assertEqual(find.call_count,1)

    def test_truncation_or_other_javascript_errors_are_not_retried(self):
        for code in ['truncated_page','javascript error','body_images_loading']:
            with patch.object(d,'_body_scope_once',side_effect=Stop(code,'fault')) as find,self.assertRaises(Stop):
                d.body_scope(self.reader(),'123')
            self.assertEqual(find.call_count,1)

    def test_body_poll_rediscovers_when_the_previously_valid_root_disappears(self):
        value={'id':'123','title':'test','text':'protected','paragraphs':[],
               'images':[],'backgrounds':[]}
        class B:
            def __init__(self):self.reads=[]
            def command(self,*args):return {'url':d.editor_url('123')}
            def snapshot(self,selector='body'):
                self.reads.append(selector)
                if selector=='old-root':raise Stop('inspection_scope_changed','race')
                return {'url':d.editor_url('123')}
            def wait(self,predicate,read=None):
                snap=read();assert predicate(snap);return snap
        b=B()
        with patch.object(d,'body_scope',side_effect=['old-root','new-root']),patch.object(d,'content',return_value=value):
            _,after=d.snapshot_body(b,'123')
        self.assertEqual(after,value)
        self.assertEqual(b.reads,['old-root','new-root','new-root'])


class InspectFailureClassificationTests(unittest.TestCase):
    def test_only_the_known_inspect_uniqueness_fault_is_classified_as_a_race(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(check_runtime,'ROOT',Path(tmp).resolve()):
            b=check_runtime.Browser('local/evidence')
            cases=[('inspect','Inspection selector must match exactly one element.','inspection_scope_changed'),
                   ('inspect','Different JavaScript defect','javascript error'),
                   ('click-element','Inspection selector must match exactly one element.','javascript error')]
            for command,message,code in cases:
                result=subprocess.CompletedProcess([],1,json.dumps({'ok':False,'error':'javascript error','message':message}),'')
                with patch.object(check_runtime.subprocess,'run',return_value=result),self.assertRaises(Stop) as caught:
                    b.command(command,'fixture')
                self.assertEqual(caught.exception.code,code)


class DeletionLifecycleTests(unittest.TestCase):
    def fixture(self,effect=True,missing=False):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name).resolve();folder=root/'local/evidence';folder.mkdir(parents=True)
        for m in [d,check_runtime]:
            p=patch.object(m,'ROOT',root);p.start();self.addCleanup(p.stop)
        value={'id':'123','title':'test','text':'protected','paragraphs':[{'text':'protected','style':''}],
               'images':[{'src':'tail','width':10,'height':10}],'backgrounds':[]}
        (root/'local/baseline.json').write_text(json.dumps(value),encoding='utf-8')
        record={'copy_id':'123','phase':'needs_format','baseline':'local/baseline.json'}
        obj=d.Drafts.__new__(d.Drafts);obj.folder=folder;obj.rules={};obj.index={'copies':{'1':record}}
        obj.index_path=root/'data/index.json'
        class B:
            deleted=False
            selected=False
            def __init__(self):self.events=[]
            def command(self,*args):self.events.append(args);return {}
            def editor_ui(self):return {'url':d.editor_url('123'),'nodes':menu()[:1] if missing else menu()}
            def click(self,node):
                self.events.append(('click',node['selector']))
                if node['selector']=='v > empty':self.selected=True
                if node['tag']=='button' and effect:self.deleted=True
        b=B();obj.browser=b
        def snapshot_body(*args):
            nodes=[{'class':'tn-page-vessel','selector':'v'},
                   {'class':'tn-comp-top-level','selector':'v > tail'},
                   {'tag':'img','selector':'v > tail > img'}]
            if not b.deleted:nodes.append({'class':'tn-comp-top-level'+(' tn-on-single-select' if b.selected else ''),'selector':'v > empty','tag':'div'})
            return {'nodes':nodes},copy.deepcopy(value)
        p=patch.object(d,'snapshot_body',side_effect=snapshot_body);p.start();self.addCleanup(p.stop)
        action={'kind':'delete_empty_component','selector':'v > empty','before_sha256':d.fingerprint(value)}
        return obj,b,record,action

    def test_empty_tail_neighbor_is_removed_saved_and_reloaded(self):
        obj,b,record,action=self.fixture();obj.action('1',action)
        self.assertEqual(b.events.count(('check-empty','v > empty')),1)
        self.assertIn(('click','menu > li > slot > button'),b.events)
        self.assertIn(('click','save'),b.events);self.assertIn(('reload',),b.events)
        self.assertNotIn('repair_intent',record)

    def test_selection_changes_body_and_blocks_delete(self):
        obj,b,record,action=self.fixture()
        original=d.snapshot_body.side_effect
        def changed(*args):
            snap,value=original(*args)
            if b.selected:value['text']='unexpected edit'
            return snap,value
        d.snapshot_body.side_effect=changed
        with self.assertRaises(Stop) as caught:obj.action('1',action)
        self.assertEqual(caught.exception.code,'delete_selection_unverified')
        self.assertNotIn(('click','menu > li > slot > button'),b.events)
        self.assertIn('repair_intent',record)

    def test_wrong_selected_component_blocks_delete(self):
        obj,b,record,action=self.fixture()
        original=d.snapshot_body.side_effect
        def changed(*args):
            snap,value=original(*args)
            if b.selected:snap['nodes'][-1]['selector']='v > different'
            return snap,value
        d.snapshot_body.side_effect=changed
        with self.assertRaises(Stop) as caught:obj.action('1',action)
        self.assertEqual(caught.exception.code,'delete_selection_unverified')
        self.assertNotIn(('click','menu > li > slot > button'),b.events)

    def test_credit_focus_uses_verified_path_not_text_disambiguated_selector(self):
        obj,b,record,action=self.fixture()
        original=d.snapshot_body.side_effect
        def snapshot(*args):
            snap,value=original(*args)
            snap['nodes'].append({'tag':'span','class':'','editable':True,'editable_attribute':'true',
                'selector':'v > credit > p > span','stable_selector':'shared span',
                'rendered_text':'文案｜测试部门'})
            return snap,value
        d.snapshot_body.side_effect=snapshot
        action.update(kind='replace_credit',selector='v > credit > p > span')
        obj.action('1',action)
        self.assertIn(('replace-credit','v > credit > p > span','v > credit > p > span','文案｜测试部门','文案丨测试部门'),b.events)
        self.assertNotIn(('key','Tab'),b.events)
        self.assertNotIn(('focus','shared span'),b.events)
        self.assertNotIn(('key','SelectAll'),b.events)

    def test_no_effect_retains_intent_and_cannot_repeat_delete(self):
        obj,b,record,action=self.fixture(effect=False)
        with self.assertRaises(Stop) as caught:obj.action('1',action)
        self.assertEqual(caught.exception.code,'delete_outcome_unverified')
        with self.assertRaises(Stop) as caught:obj.action('1',action)
        self.assertEqual(caught.exception.code,'repair_outcome_unknown')
        self.assertEqual(b.events.count(('click','menu > li > slot > button')),1)
        self.assertNotIn(('click','save'),b.events)

    def test_missing_button_never_clicks_drop_receiver(self):
        obj,b,record,action=self.fixture(missing=True)
        with self.assertRaises(Stop):obj.action('1',action)
        self.assertNotIn(('click','trash > prompt'),b.events)
        self.assertIn('repair_intent',record)


class SummaryTests(unittest.TestCase):
    def test_summary_counts_only_verified_articles_and_actual_receipt(self):
        class Log:
            def result(self,*args,**kwargs):self.args=args;self.kwargs=kwargs
        log=Log();full_run.record_summary(log,{'phase':'agent_format_required','reservation_count':2,
             'formatted_articles':[{'format_verified':False}], 'notification':{'status':'accepted'}})
        self.assertEqual(log.kwargs['reservation_count'],2)
        self.assertEqual(log.kwargs['formatted_count'],0)
        self.assertEqual(log.kwargs['notification_status'],'accepted')


class CreditEditingHostTests(unittest.TestCase):
    def fixture(self):
        host={'tag':'div','selector':'v > host','editable_attribute':'true','rendered_text':'文案｜部门'}
        para={'tag':'p','selector':'v > host > p','rendered_text':'文案｜部门'}
        text={'tag':'span','selector':'v > host > p > span','editable':True,'rendered_text':'文案｜部门'}
        return [host,para,text],text

    def test_inherited_span_focuses_exact_single_credit_host(self):
        nodes,text=self.fixture()
        self.assertIs(d.credit_focus_target(nodes,text),nodes[0])

    def test_shared_host_is_allowed_only_with_bounded_leaf_range_command(self):
        nodes,text=self.fixture();nodes[0]['rendered_text']+='\n排版｜另一人'
        self.assertIs(d.credit_focus_target(nodes,text),nodes[0])

    def test_host_with_another_paragraph_or_media_is_refused(self):
        for mode in ['another_text','media']:
            nodes,text=self.fixture()
            if mode=='another_text':nodes[0]['rendered_text']+='\n正文'
            else:nodes.append({'tag':'img','selector':'v > host > img'})
            with self.assertRaises(Stop):d.credit_focus_target(nodes,text)

    def test_missing_explicit_host_is_refused_before_any_input(self):
        nodes,text=self.fixture();nodes[0].pop('editable_attribute')
        with self.assertRaises(Stop):d.credit_focus_target(nodes,text)

    def test_truncated_host_is_refused(self):
        nodes,text=self.fixture();nodes[0]['rendered_text_truncated']=True
        with self.assertRaises(Stop):d.credit_focus_target(nodes,text)
