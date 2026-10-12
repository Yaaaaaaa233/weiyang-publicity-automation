import json
from datetime import datetime
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from scripts import check_runtime, transfer_registry as registry, xiumi_scan, sync_workflow, transfer_tags
from scripts.check_runtime import Stop

class CandidatePatchTests(unittest.TestCase):
    def test_low_score_related_recruitment_is_retained_for_body_review(self):
        article={'row':1,'title':'某大学联合支队招募推送','unit':'实践组'}
        cards=[{'id':'1','title':'未央实践丨2026-2027学年寒假赴某地实践支队长及骨干招募'},
               {'id':'2','title':'未央实践丨支队招募说明'}]
        result=xiumi_scan.match_candidates([article],cards)[0]
        self.assertEqual({c['id'] for c in result['candidates']},{'1','2'})
        self.assertTrue(all(not c['identity_confirmed'] for c in result['candidates']))
        self.assertEqual([c['review_rank'] for c in result['candidates']],[1,2])
        scores=[c['similarity'] for c in result['candidates']]
        self.assertEqual(scores,sorted(scores,reverse=True))
        self.assertTrue(any(c['similarity']<.45 for c in result['candidates']))
    def test_processed_exact_is_reported_without_replacing_with_other_draft(self):
        a={'row':1,'title':'活动招募'}
        r=xiumi_scan.match_candidates([a],[{'id':'1','title':'活动招募','tags':['转存好']},
                                          {'id':'2','title':'活动招募通知'}])[0]
        self.assertEqual(r['candidates'],[])
        self.assertEqual(r['processed_candidates'][0]['id'],'1')
        self.assertIn('already_transferred_or_reserved',r['search_audit'][0]['reasons'])
    def test_explicit_conflict_excluded_even_if_only_candidate(self):
        r=xiumi_scan.match_candidates([{'row':1,'title':'第五周学习周报'}],[{'id':'1','title':'第四周学习周报'}])[0]
        self.assertFalse(r['candidates'])
        self.assertIn('different_week',r['search_audit'][0]['reasons'])
        self.assertEqual(r['search_status'],'unmatched_conflicts_excluded')
    def test_department_conflict_remains_ranked_for_body_review_not_missing(self):
        title='实践丨2026-2027学年寒假赴澳门实践支队长及骨干招募'
        r=xiumi_scan.match_candidates([{'row':30,'title':title,'unit':'学习部'}],
             [{'id':'730146042','title':title,'available':True,'display_date':'2026-10-08'}])[0]
        self.assertEqual([c['id'] for c in r['candidates']],['730146042'])
        self.assertFalse(r['candidates'][0]['identity_confirmed'])
        self.assertEqual(r['candidates'][0]['review_rank'],1)
        self.assertTrue(r['body_review_required'])
        self.assertEqual(r['search_status'],'candidates_to_review')
        self.assertEqual(r['excluded_candidates'][0]['exclusion_reasons'],['different_department'])
        self.assertTrue(r['excluded_candidates'][0]['reviewable'])
    def test_exact_title_with_department_conflict_is_not_auto_confirmed(self):
        title='实践丨澳门大学联合支队招募'
        r=xiumi_scan.match_candidates([{'row':1,'title':title,'unit':'文艺部'}],
             [{'id':'1','title':title}])[0]
        self.assertEqual(len(r['candidates']),1)
        self.assertFalse(r['candidates'][0]['identity_confirmed'])
        self.assertTrue(r['body_review_required'])
    def test_hard_conflict_without_candidate_is_reported_as_conflict_not_missing(self):
        from scripts import daily_check
        day='2026-01-01';at='2026-01-01T12:00:00+08:00'
        observation={'date':day,'timezone':'Asia/Shanghai','observed_at':at,'run_id':'conflict-review',
                     'reservation':{'status':'ok','complete':True,'date':day,'evidence_paths':['artifacts/a.json'],
                                    'conflicts_resolved':True,'articles':[{'row':1,'title':'第五周学习周报',
                                    'is_headline':True,'remarks':''}]},
                     'xiumi':{'status':'ok','account_confirmed':True,'search_complete':True,
                              'evidence_paths':['artifacts/b.json'],'matches':[{'row':1,'candidates':[],
                              'excluded_candidates':[{'id':'1','title':'第四周学习周报','exclusion_reasons':['different_week']}],
                              'search_status':'unmatched_conflicts_excluded'}]}}
        report=daily_check.evaluate(observation,day,datetime.fromisoformat(at))
        self.assertIn({'code':'candidate_conflict_needs_review','row':1},report['issues'])
        self.assertNotIn({'code':'manuscript_missing','row':1},report['issues'])

class TransferProtectionTests(unittest.TestCase):
    def test_another_day_run_blocked_after_reservation_and_notification_failure(self):
        with TemporaryDirectory() as d,patch.object(check_runtime,'ROOT',Path(d)):
            articles=[{'source_id':'1','draft_id':'2'}]
            registry.reserve('account','first',articles)
            registry.update('account','first',status='confirmed',notification_status='outcome_unknown')
            registry.reserve('account','first',articles)
            with self.assertRaises(Stop):registry.reserve('account','next-day',articles)
            annotated=registry.annotate([{'id':'1'},{'id':'2'},{'id':'3'}],'account')
            self.assertEqual(annotated[0]['transfer_status'],'confirmed')
            self.assertEqual(annotated[1]['transfer_status'],'confirmed')
            self.assertNotIn('transfer_status',annotated[2])
            self.assertEqual(registry.entries('another-account'),{})
    def test_tag_adapter_missing_performs_no_browser_mutation(self):
        class Browser:
            def command(self,*args):raise AssertionError('must not touch browser')
        with self.assertRaises(Stop):transfer_tags.apply(Browser(),{},[],lambda *args:None)
    def test_success_requires_visible_explicit_dialog_not_article_or_progress(self):
        n={'selector':'body > dialog > span','own_text':'同步成功','rect':{'width':30,'height':20}}
        dialog={'selector':'body > dialog','role':'dialog'}
        self.assertTrue(sync_workflow.platform_success({'nodes':[dialog,n]}))
        self.assertFalse(sync_workflow.platform_success({'nodes':[n]}))
        self.assertFalse(sync_workflow.platform_success({'nodes':[dialog,dict(n,own_text='正在同步')]}))
        self.assertFalse(sync_workflow.platform_success({'nodes':[dialog,dict(n,rect={'width':0,'height':0})]}))
    def test_unknown_dispatch_never_polls_or_replays(self):
        class Ledger:state={'phase':'awaiting_confirmation','submission_reserved':True,'click_command_returned':False}
        class Browser:
            def command(self,*args):raise AssertionError('unknown dispatch must remain unknown')
        sync_workflow.observe_submission(Browser(),Ledger())

class CompletionGateTests(unittest.TestCase):
    def test_only_confirmed_and_accepted_can_tag_and_failure_never_resubmits(self):
        """Tagging is opt-in since 2026-10-10; this table pins its behaviour when enabled."""
        from contextlib import nullcontext
        from scripts import full_run, daily_check
        for sync_phase,receipt,tag_error,expected in [('confirmed','accepted',False,'completed'),
                                                     ('confirmed','outcome_unknown',False,'notification_pending'),
                                                     ('confirmed','accepted',True,'tagging_required'),
                                                     ('awaiting_confirmation','accepted',False,'awaiting_confirmation')]:
            with self.subTest(sync_phase=sync_phase,receipt=receipt,tag_error=tag_error),TemporaryDirectory() as d, \
                 patch.dict(os.environ,{'WEIYANG_TRANSFER_TAGS':'1'}):
                root=Path(d).resolve();folder=root/'local/full-run/fixture';folder.mkdir(parents=True)
                for name,value in [('report',{'ordered_rows':[1]}),('observation',{})]:
                    (root/f'local/{name}.json').write_text(json.dumps(value))
                sync=root/'local/sync-workflow/sync/state.json';sync.parent.mkdir(parents=True)
                sync.write_text(json.dumps({'phase':sync_phase,'submission_reserved':True,'confirmed_by':'platform',
                                            'click_command_returned':True,'handoff_path':'local/proof.json'}))
                state={'run_id':'fixture','date':'2026-01-01','sync_run':'sync','config':{'xiumi_account':'fixture'},
                       'check_state':{'report':'local/report.json','observation':'local/observation.json'},
                       'formatted_articles':[{'source_id':'1','draft_id':'2'}]}
                class Log:
                    def stage(self,*a):return nullcontext()
                    def result(self,*a,**k):pass
                    def evidence(self,*a):pass
                class Browser:
                    def __init__(self,*a):pass
                    def reserved(self):return nullcontext()
                    def command(self,*a):pass
                with patch.object(check_runtime,'ROOT',root),patch.object(full_run,'ROOT',root), \
                     patch.object(daily_check,'check_evidence'),patch.object(full_run,'Browser',Browser), \
                     patch.object(full_run,'sync_call') as calls,patch.object(full_run.notify_wecom,'completion_from_report',return_value='result'), \
                     patch.object(full_run,'send_notification',return_value={'status':receipt}), \
                     patch.object(transfer_tags,'apply',side_effect=Stop('tag_save_unverified','pending') if tag_error else None) as tags:
                    result=full_run.finish_submission(folder,state,Log())
                    self.assertEqual(result['phase'],expected)
                    self.assertEqual(tags.call_count,int(sync_phase=='confirmed' and receipt=='accepted'))
                    self.assertTrue(all(c.args[0]=='observe-result' for c in calls.call_args_list))
                    # Read-only resume preserves completion evidence/message, no fresh submission.
                    if expected!='completed':
                        again=full_run.finish_submission(folder,state,Log())
                        self.assertEqual(again['phase'],expected)
                        self.assertTrue(all(c.args[0]=='observe-result' for c in calls.call_args_list))

    def test_tagging_is_paused_by_default_and_writes_no_platform_mark(self):
        """Operator decision 2026-10-10: a production run leaves no artificial mark on the
        platform. The private registry is the record, so the run reaches completed without
        touching the tag adapter and without claiming a verified platform label."""
        from contextlib import nullcontext
        from scripts import full_run, daily_check
        with TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=False):
            os.environ.pop('WEIYANG_TRANSFER_TAGS',None)
            root=Path(d).resolve();folder=root/'local/full-run/fixture';folder.mkdir(parents=True)
            for name,value in [('report',{'ordered_rows':[1]}),('observation',{})]:
                (root/f'local/{name}.json').write_text(json.dumps(value))
            sync=root/'local/sync-workflow/sync/state.json';sync.parent.mkdir(parents=True)
            sync.write_text(json.dumps({'phase':'confirmed','submission_reserved':True,'confirmed_by':'platform',
                                        'click_command_returned':True,'handoff_path':'local/proof.json'}))
            state={'run_id':'fixture','date':'2026-01-01','sync_run':'sync','config':{'xiumi_account':'fixture'},
                   'check_state':{'report':'local/report.json','observation':'local/observation.json'},
                   'formatted_articles':[{'source_id':'1','draft_id':'2'}]}
            class Log:
                def stage(self,*a):return nullcontext()
                def result(self,*a,**k):pass
                def evidence(self,*a):pass
            class Browser:
                def __init__(self,*a):pass
                def reserved(self):return nullcontext()
                def command(self,*a):pass
            with patch.object(check_runtime,'ROOT',root),patch.object(full_run,'ROOT',root), \
                 patch.object(daily_check,'check_evidence'),patch.object(full_run,'Browser',Browser), \
                 patch.object(full_run,'sync_call'), \
                 patch.object(full_run.notify_wecom,'completion_from_report',return_value='result'), \
                 patch.object(full_run,'send_notification',return_value={'status':'accepted'}), \
                 patch.object(transfer_tags,'apply') as tags:
                result=full_run.finish_submission(folder,state,Log())
                registry_row=registry.entries('fixture')['1']
            self.assertEqual(result['phase'],'completed')
            self.assertEqual(tags.call_count,0)
            self.assertNotIn('tagged_ids',state)
            self.assertNotIn('tag_error',state)
            self.assertNotIn('tag_request',state)
            self.assertEqual(registry_row['tag_status'],'paused_by_operator')
            self.assertNotEqual(registry_row['status'],'tagged')

class LabelResumeTests(unittest.TestCase):
    def test_hover_then_add_exact_label_and_resume_without_retagging(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'local').mkdir();(root/'local/ui.json').write_text('{}')
            config={'xiumi_library_url':'library','xiumi_account':'fixture',
                    'transfer_tag_ui':{'panel_class':'tn-dialog-item-tag-edit','choice_class':'tag-item',
                                       'current_list_class':'item-tags','choice_list_class':'all-tags',
                                       'new_placeholder':'添加新标签，回车确认','save_text':'确定',
                                       'evidence_paths':['local/ui.json']}}
            applied=set();actions=[];proofs=[];global_tags={transfer_tags.LABEL}
            class Browser:
                evidence=['local/ui.json'];identifier=None;modal=False;hovered=False
                def command(self,*args):
                    actions.append(args)
                    if args[0]=='hover-element':self.hovered=True
                def click(self,node):
                    actions.append(('click',node['selector']))
                    if node['selector']=='card > action':self.modal=True
                    if node['selector']=='panel > all > choice':applied.add(self.identifier)
                    if node['selector']=='panel > save':self.modal=False
                def snapshot(self):
                    if self.modal:
                        current=[{'selector':'panel > current > tag','tag':'li','class':'tag-item',
                                  'own_text':transfer_tags.LABEL,'rendered_text':transfer_tags.LABEL}]
                        choices=[{'selector':'panel > all > choice','tag':'li','class':'tag-item',
                                  'own_text':'','rendered_text':label} for label in global_tags]
                        return {'nodes':[{'selector':'panel','class':'modal tn-dialog-item-tag-edit','own_text':''},
                                         {'selector':'panel > current','tag':'ul','class':'tag-list item-tags'},
                                         *(current if self.identifier in applied else []),
                                         {'selector':'panel > all','tag':'ul','class':'tag-list all-tags'},*choices,
                                         {'selector':'panel > save','tag':'button','class':'btn btn-green',
                                          'own_text':'确定','rendered_text':'确定'}]}
                    nodes=[{'selector':'card','class':'show-item','own_text':''},
                           {'selector':'card > link','href':'https://xiumi.us/studio/v5#/paper/for/'+self.identifier,'own_text':''}]
                    if self.hovered:
                        nodes.append({'selector':'card > action','class':'action-btn-small tag','own_text':'','rendered_text':''})
                    if self.identifier in applied:nodes.append({'selector':'card > label','own_text':transfer_tags.LABEL})
                    return {'nodes':nodes}
                def wait(self,predicate):
                    snapshot=self.snapshot()
                    if not predicate(snapshot):raise AssertionError('mocked browser state did not reach expected checkpoint')
                    return snapshot
            def lookup(browser,account,title,identifier):
                browser.identifier=identifier;browser.modal=False;browser.hovered=False
                return browser.snapshot()
            browser=Browser();articles=[{'source_id':'1','draft_id':'2','source_title':'source','sync_title':'copy'}]
            with patch.object(check_runtime,'ROOT',root),patch.object(transfer_tags,'lookup',lookup),patch.object(transfer_tags,'find_window'):
                transfer_tags.apply(browser,config,articles,lambda i,e:proofs.append(i))
                self.assertEqual(applied,{'1','2'});self.assertEqual(proofs,['1','2'])
                first_actions=list(actions)
                self.assertEqual([a[0] for a in actions].count('hover-element'),2)
                transfer_tags.apply(browser,config,articles,lambda i,e:proofs.append(i))
                self.assertEqual(actions,first_actions)
                self.assertEqual(actions.count(('click','panel > save')),2)
                self.assertEqual(actions.count(('click','panel > all > choice')),2)
