"""Resumable daily workflow: check, agent identity interface, independent copies, sync, notify.

Default is read/preview. --execute enables authorized copy and draft-box operations.
Agent work requests are machine checkpoints, not a request for human title approval.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

try:
    from . import daily_run, daily_check, notify_wecom, sync_workflow, draft_workflow
    from .check_runtime import Browser, ROOT, read, write, private_path, Stop
    from .read_wps_reservation import SHANGHAI
    from .run_log import RunLog
except ImportError:
    import daily_run, daily_check, notify_wecom, sync_workflow, draft_workflow
    from check_runtime import Browser, ROOT, read, write, private_path, Stop
    from read_wps_reservation import SHANGHAI
    from run_log import RunLog


def sync_call(command, run, **kwargs):
    return sync_workflow.run(SimpleNamespace(command=command,run=run,**kwargs))

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);daily_run.save_state(path,value)

def send_notification(folder, state, message, field='notification'):
    intent={'message_sha256':hashlib.sha256(message.encode('utf-8')).hexdigest(),'field':field}
    if state.get('notification_intent') and state['notification_intent']!=intent:
        raise Stop('notification_outcome_unknown','Resolve original notification; do not replace its text')
    state['notification_intent']=intent;save(folder/'state.json',state)
    result=notify_wecom.deliver_result(message,notify_wecom.configured_webhook(),True)
    state[field]=result;state.pop('notification_intent');save(folder/'state.json',state)
    return result

FORMAT_NOTES={
    'title_separator':'标题分隔符待修正','credit_separator':'落款分隔符待修正',
    'head_identity_or_position':'头图素材或位置待核对','tail_identity_or_position':'尾图素材或位置待核对',
    'head_spacing_unverified':'头图前后间距待校验','trailing_component_unverified':'尾部空组件待核对',
    'cover_unverified':'封面待核验','format_rule_evidence_missing':'格式规则证据缺失'}


def validate_body_decisions(decisions, request):
    """Bind semantic judgments to bodies collected for this exact row/candidate/run."""
    if decisions.get('run_id')!=request['run_id'] or decisions.get('date')!=request['date']:
        raise ValueError('Identity decision belongs to another body collection')
    rows={r['row']:r['candidates'] for r in request['matches']}
    for item in decisions.get('matches',[]):
        candidates=rows.get(item['row'],[])
        selected=[c for c in candidates if c['id']==item['id'] and c['title']==item['title']]
        if len(selected)!=1:raise ValueError('Decision is not a body-observed candidate')
        selected_path=selected[0]['body_observation']
        if selected_path not in item.get('evidence_paths',[]):raise ValueError('Selected body evidence missing')
        for key in ('topic','department','date','week'):
            if selected_path not in item.get('body_checks',{}).get(key,{}).get('evidence_paths',[]):
                raise ValueError('Each identity facet must reference the selected actual body observation')
        reviews={r['id']:r for r in item.get('candidate_reviews',[])}
        if len(candidates)>1:
            for candidate in candidates:
                if candidate['body_observation'] not in reviews.get(candidate['id'],{}).get('evidence_paths',[]):
                    raise ValueError('Every candidate comparison must reference its own collected body')


def finish_submission(folder, state, log, execute=True):
    """Recover a reserved submit without revisiting or editing any draft."""
    checked=state['check_state']
    report=read(private_path(checked['report']));observation=read(private_path(checked['observation']))
    daily_check.check_evidence(observation)
    sync_path=ROOT/'local/sync-workflow'/state['sync_run']/'state.json'
    actual=read(sync_path)
    if not actual.get('submission_reserved'):raise Stop('submission_not_reserved','No pending submission')
    formats=state['formatted_articles']
    completion={'date':state['date'],'run_id':state['run_id'],'formatted_articles':formats,
                'sync':{'date':state['date'],'status':'confirmed' if actual['phase']=='confirmed' else 'submitted',
                        'ordered_draft_ids':[a['draft_id'] for a in formats],
                        'dispatch_result':'returned' if actual.get('click_command_returned') else 'unknown',
                        'evidence_paths':[str(sync_path.relative_to(ROOT)),actual['handoff_path']]}}
    if completion['sync']['status']=='confirmed':completion['sync']['confirmed_by']='user'
    completion_path=folder/'completion.json'
    if completion_path.exists() and read(completion_path)!=completion:raise Stop('completion_changed','Existing result differs; use explicit confirmation')
    if not completion_path.exists():write(completion_path,completion)
    message=notify_wecom.completion_from_report(report,observation,completion)
    message_path=folder/'completion-message.txt'
    if message_path.exists() and message_path.read_text(encoding='utf-8')!=message:raise Stop('completion_message_changed','Pending notification text changed')
    if not message_path.exists():message_path.write_text(message,encoding='utf-8')
    state.update(completion=str(completion_path.relative_to(ROOT)),message=str(message_path.relative_to(ROOT)))
    save(folder/'state.json',state)
    if execute:
        with log.stage('completion_notification'):
            state['notification']=send_notification(folder,state,message)
    state['phase']='completed' if actual['phase']=='confirmed' else 'awaiting_confirmation'
    log.result(state['phase'],reservation_count=len(report['ordered_rows']),formatted_count=len(formats),
               sync_status=completion['sync']['status'],notification_status=state.get('notification',{}).get('status'),protected=state['phase']!='completed')
    log.evidence(completion_path);save(folder/'state.json',state);return state


def execute(config, run_id, execute=False, decisions=None, action=None, recovery=None):
    day=datetime.now(SHANGHAI).date().isoformat()
    log=RunLog(run_id,day,config.get('node_id'),root=ROOT)
    folder=private_path('local/full-run/'+run_id);folder.mkdir(parents=True,exist_ok=True)
    path=folder/'state.json'
    state=read(path) if path.exists() else {'date':day,'run_id':run_id,'phase':'created',
                                         'config_sha256':draft_workflow.fingerprint(config)}
    if state['date']!=day or state['config_sha256']!=draft_workflow.fingerprint(config):
        raise Stop('run_changed','Run date/config changed; resume the original identity and configuration')
    if state['phase'] in ('completed','awaiting_confirmation','no_reservations'):
        return state  # No business operation is replayed at a terminal checkpoint.
    with sync_workflow.runner_lock(ROOT/'data/full-run.lock'):
        try:
            if state.get('sync_run'):
                existing=ROOT/'local/sync-workflow'/state['sync_run']/'state.json'
                if existing.exists() and read(existing).get('submission_reserved'):
                    return finish_submission(folder,state,log,execute)
            if decisions:
                if not state.get('identity_request'):raise Stop('body_collection_missing','Collect actual candidate bodies before supplying identity decisions')
                validate_body_decisions(read(private_path(decisions)),read(private_path(state['identity_request'])))
            with log.stage('reservation_matching'):
                checked=daily_run.execute(config,run_id,'preview',decisions,log=log)
            state.update(check_state=checked,phase=checked['phase']);save(path,state)
            observation=read(private_path(checked['observation']))
            report=read(private_path(checked['report']))
            fresh=daily_check.evaluate(observation,day)
            if fresh['status']!=report['status'] or fresh['issues']!=report['issues']:
                raise Stop('observation_stale','Start a fresh check; do not edit drafts from an expired observation')
            daily_check.check_evidence(observation)
            if report['status'] in ('check_failed','no_reservations'):
                state['message']=checked['message']
                if execute:
                    state['notification']=send_notification(folder,state,private_path(checked['message']).read_text(encoding='utf-8'))
                log.result(report['status'],reservation_count=0 if report['status']=='no_reservations' else None,
                           notification_status=state.get('notification',{}).get('status'),protected=report['status']!='no_reservations')
                save(path,state);return state
            if not report['ready_to_format']:
                # Only semantic identity issues can go to the agent. Missing drafts and login failures are final check outcomes.
                semantic=bool(report['issues']) and all(i['code'] in ('identity_or_week_unresolved','multiple_versions_unresolved','identity_match_reason_missing') for i in report['issues'])
                if semantic:
                    with daily_run.local_lock(uuid.uuid4().hex):
                        ledger=daily_run.Ledger(config,day,'full-body-'+uuid.uuid4().hex)
                        browser=Browser(folder/'body-evidence',task_owner=ledger.owner);browser.log=log
                        try:
                            request=state.get('identity_request')
                            if not request:
                                rows=[];collected={}
                                with browser.reserved(), log.stage('body_collection'):
                                    browser.command('start')
                                    for match in observation['xiumi']['matches']:
                                        candidates=[]
                                        for c in match['candidates']:
                                            if c.get('available') is False:continue
                                            if c['id'] not in collected:
                                                collected[c['id']]=draft_workflow.observe(browser,c)
                                            value,evidence=collected[c['id']]
                                            candidates.append({'id':c['id'],'title':c['title'],'body_observation':evidence,
                                                               'body_sha256':draft_workflow.body_signature(value)})
                                        rows.append({'row':match['row'],'candidates':candidates})
                                request=str(write(folder/('identity-request-'+uuid.uuid4().hex+'.json'),
                                                  {'run_id':run_id,'date':day,'reservation':observation['reservation'],
                                                   'matches':rows,'required_checks':['topic','department','date','week','unique_version']}).relative_to(ROOT))
                                state['identity_request']=request
                            ledger.checkpoint('needs_manual','agent_identity_requested',browser.evidence+[request],run_id)
                            state['phase']='agent_identity_required'
                        finally:state['handoff']=ledger.finish()
                else:state['phase']='needs_attention'
                if execute and not semantic:
                    state['notification']=send_notification(folder,state,private_path(checked['message']).read_text(encoding='utf-8'))
                # A machine identity request remains a checkpoint; the agent can answer it immediately.
                log.result(state['phase'],reservation_count=len(observation['reservation']['articles']),
                           notification_status=state.get('notification',{}).get('status'),protected=True)
                save(path,state);return state
            if not execute:
                state['phase']='ready_to_execute';save(path,state)
                log.result('ready_to_execute',reservation_count=len(report['ordered_rows']),protected=True)
                return state
            rules=read(private_path(config['format_rules']))
            rules_hash=draft_workflow.fingerprint(rules)
            if state.get('format_rules_sha256') and state['format_rules_sha256']!=rules_hash:
                raise Stop('format_rules_changed','Do not change formatting policy midway through an execution')
            state['format_rules_sha256']=rules_hash;save(path,state)
            if len(report['ordered_rows'])>8:raise Stop('composition_limit','More than eight articles require explicit business handling')
            with daily_run.local_lock(uuid.uuid4().hex):
                ledger=daily_run.Ledger(config,day,'full-drafts-'+uuid.uuid4().hex)
                browser=Browser(folder/'draft-evidence',task_owner=ledger.owner);browser.log=log
                try:
                    with browser.reserved(),log.stage('copy_format_verify'):
                        browser.command('start')
                        drafts=draft_workflow.Drafts(browser,folder/'draft-evidence',config['xiumi_account'],day,
                                                    config.get('copy_marker','【自动整理'+day.replace('-','')+'】'),rules,
                                                    known_ids=[c['id'] for c in read(private_path(observation['xiumi']['catalog_path']))] if observation['xiumi'].get('catalog_path') else [])
                        if recovery: drafts.attach(recovery['source_id'],recovery['draft_id'])
                        if action:
                            state['repair_result']=drafts.reconcile_repair(action['source_id']) if action.get('kind')=='reconcile' else drafts.action(action['source_id'],action)
                            state['phase']='agent_format_required';save(path,state);log.result('agent_format_required',protected=True);return state
                        by_row={m['row']:m['candidates'][0] for m in observation['xiumi']['matches']}
                        articles={a['row']:a for a in observation['reservation']['articles']}
                        formatted=[]
                        for row in report['ordered_rows']:
                            formatted.append(drafts.prepare(articles[row],by_row[row]))
                            state['formatted_articles']=formatted;save(path,state)
                            if not formatted[-1]['format_verified']:
                                state['phase']='agent_format_required'
                                ledger.checkpoint('needs_manual','agent_format_requested',formatted[-1]['evidence_paths'],run_id)
                                state['notification']=send_notification(folder,state,
                                    notify_wecom.notification_heading(report)+'\n预约 '+str(len(report['ordered_rows']))+' 篇，已收齐。\n基础整理未完成：'+'、'.join(FORMAT_NOTES.get(k,'格式待核验') for k in formatted[-1]['format_issues'])+'。\n已保留副本与检查点，待完成格式处理。',
                                    )
                                log.result('agent_format_required',reservation_count=len(report['ordered_rows']),protected=True)
                                save(path,state);return state
                        evidence=[p for a in formatted for p in a['evidence_paths']]
                        ledger.checkpoint('ready_for_review','format_verified',evidence,run_id)
                finally:state['handoff']=ledger.finish();save(path,state)
            sync_run='full-'+hashlib.sha256(run_id.encode()).hexdigest()[:24]
            state['sync_run']=sync_run;save(path,state)
            plan={'date':day,'composer_url':config['composer_url'],'target_account':config['target_account'],
                  'plan_confirmed':True,'prior_sync_attempts':0,
                  'articles':[{'row':a['row'],'is_headline':a['is_headline'],
                               'source_title':a['sync_title'],'sync_title':a['sync_title']} for a in formatted]}
            plan_path=folder/'sync-plan.json'
            if plan_path.exists() and read(plan_path)!=plan:raise Stop('sync_plan_changed','Original composition plan changed')
            if not plan_path.exists():write(plan_path,plan)
            sync_path=ROOT/'local/sync-workflow'/sync_run/'state.json'
            with daily_run.local_lock(uuid.uuid4().hex),log.stage('composition_sync'):
                previous=read(sync_path) if sync_path.exists() else None
                if not previous or not previous.get('submission_reserved'):
                    sync_call('prepare',sync_run,plan=plan_path,resume=previous is not None,run_log=log)
                    # Submission reservation in the existing sync ledger is authoritative.
                    sync_call('submit',sync_run,execute=True,run_log=log)
                actual=read(sync_path)
            return finish_submission(folder,state,log,True)
        except Exception as exc:
            state.update(phase='failed',error_code=getattr(exc,'code',type(exc).__name__))
            save(path,state);log.result('failed',protected=True)
            # Send only actual failure, no invented counts or completion state.
            if execute and not state.get('notification_intent'):
                msg=notify_wecom.notification_heading({'date':day,'run_id':run_id})+'\n执行中断：'+state['error_code']+'。\n整理或转存结果尚未全部确认；保留检查点，不重放未知操作。'
                state['notification']=send_notification(folder,state,msg);save(path,state)
            raise


def confirm(run_id, result, note):
    """Record an actual operator confirmation; never query the WeChat backend or resubmit."""
    if result not in ('ok','problem') or not isinstance(note,str) or not note.strip():
        raise ValueError('Actual operator result and a nonempty note are required')
    folder=private_path('local/full-run/'+run_id);path=folder/'state.json';state=read(path)
    if state.get('confirmation'):
        if state['confirmation']['result']!=result or state['confirmation']['note']!=note:
            raise Stop('confirmation_changed','Original confirmation differs')
        if state.get('confirmation_notification',{}).get('status')!='accepted':
            message=(folder/'confirmation-message.txt').read_text(encoding='utf-8')
            state['confirmation_notification']=send_notification(folder,state,message,'confirmation_notification')
            save(path,state)
        return state
    if state['phase']!='awaiting_confirmation':raise Stop('confirmation_not_pending','No pending draft-box result')
    with sync_workflow.runner_lock(ROOT/'data/full-run.lock'):
        sync_path=ROOT/'local/sync-workflow'/state['sync_run']/'state.json'
        actual=read(sync_path)
        if actual['phase'] not in ('confirmed','problem_reported'):
            sync_call('confirm',state['sync_run'],result=result,note=note)
        actual=read(sync_path)
        expected='confirmed' if result=='ok' else 'problem_reported'
        if actual['phase']!=expected:raise Stop('confirmation_changed','Sync ledger has a different confirmation')
        # Keep the original submission report/message immutable; confirmation is a new result.
        state['confirmation']={'result':result,'note':note,'at':datetime.now(SHANGHAI).isoformat()}
        checked=state['check_state'];report=read(private_path(checked['report']));observation=read(private_path(checked['observation']))
        if result=='ok':
            completion=read(private_path(state['completion']))
            completion['sync'].update(status='confirmed',confirmed_by='user',evidence_paths=[str(sync_path.relative_to(ROOT)),actual['handoff_path']])
            completion_path=folder/'confirmed-completion.json'
            if completion_path.exists():
                if read(completion_path)!=completion:raise Stop('confirmation_changed','Confirmation evidence changed')
            else:write(completion_path,completion)
            state['confirmed_completion']=str(completion_path.relative_to(ROOT))
            message=notify_wecom.completion_from_report(report,observation,completion).replace('今日预约','原预约')
            message='未央宣传运营补充确认｜业务日期 '+state['date']+'｜确认日期 '+datetime.now(SHANGHAI).date().isoformat()+'\n'+message
        else:
            message=notify_wecom.notification_heading(report)+'\n使用者核对公众号草稿箱发现问题，转存尚未验收。保留原提交，不自动重发。'
        message_path=folder/'confirmation-message.txt';message_path.write_text(message,encoding='utf-8')
        state['phase']='completed' if result=='ok' else 'needs_attention'
        save(path,state)  # Persist the human result before external notification.
        state['confirmation_notification']=send_notification(folder,state,message,'confirmation_notification')
        log=RunLog(run_id,state['date'],root=ROOT)
        log.result(state['phase'],sync_status='confirmed' if result=='ok' else 'problem_reported',
                   notification_status=state['confirmation_notification']['status'],protected=result!='ok')
        save(path,state);return state


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=private_path,required=True)
    p.add_argument('--run',required=True);p.add_argument('--execute',action='store_true')
    p.add_argument('--confirm',choices=('ok','problem'));p.add_argument('--note')
    p.add_argument('--decisions',type=private_path);p.add_argument('--action',type=private_path);p.add_argument('--recover-copy',type=private_path)
    args=p.parse_args()
    if (args.action or args.recover_copy) and not args.execute:raise ValueError('Copy repair requires --execute')
    if args.confirm:
        if not args.execute or args.decisions or args.action or args.recover_copy:raise ValueError('Confirmation requires --execute and no other operations')
        state=confirm(args.run,args.confirm,args.note)
    else:
        state=execute(read(args.config),args.run,args.execute,args.decisions,
                      read(args.action) if args.action else None,read(args.recover_copy) if args.recover_copy else None)
    print(json.dumps({k:state.get(k) for k in ('run_id','date','phase','identity_request','completion','sync_run','notification','confirmation_notification')},ensure_ascii=False))
    receipt=state.get('confirmation_notification') or state.get('notification',{})
    return 0 if state['phase'] in ('completed','awaiting_confirmation','no_reservations','ready_to_execute') and receipt.get('status') not in ('outcome_unknown','rejected','dispatch_reserved') else 2

if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,KeyError,RuntimeError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code',type(exc).__name__),'detail':'Inspect private state; no automatic replay'}));sys.exit(2)
