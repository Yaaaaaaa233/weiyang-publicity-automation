"""One daily read/check/notify entry. No draft creation, formatting or synchronization."""
import argparse
from contextlib import contextmanager, closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

try:
    from . import taskctl, wps_read, xiumi_scan, daily_check, notify_wecom
    from .check_runtime import Browser, Stop, ROOT, read, write, private_path
    from .read_wps_reservation import SHANGHAI
except ImportError:
    import taskctl, wps_read, xiumi_scan, daily_check, notify_wecom
    from check_runtime import Browser, Stop, ROOT, read, write, private_path
    from read_wps_reservation import SHANGHAI


def save_state(path, data):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


@contextmanager
def local_lock(token):
    path=ROOT/'data/daily-runner.lock'
    path.parent.mkdir(exist_ok=True)
    try:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError as exc:
        raise Stop('runner_busy','Existing run lock requires inspection; no automatic takeover') from exc
    with os.fdopen(fd,'w') as f:
        f.write(token); f.flush(); os.fsync(f.fileno())
    try:
        yield
    finally:
        if path.read_text()==token:
            path.unlink()


class Ledger:
    def __init__(self, config, day, owner):
        self.owner=owner
        with closing(taskctl.connect(ROOT/'data/tasks.sqlite')) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM tasks WHERE owner IS NOT NULL AND expires>?',(time.time(),)).fetchone():
                db.execute('ROLLBACK')
                raise Stop('executor_busy','Another task holds an active lease')
            key=hashlib.sha256((config['xiumi_account']+'\0'+day).encode()).hexdigest()
            jobs=[dict(r) for r in db.execute('SELECT * FROM tasks')
                  if json.loads(r['source']).get('daily_check_job_key')==key]
            if len(jobs)>1:
                db.execute('ROLLBACK')
                raise Stop('duplicate_daily_job','Reconcile duplicate day/account jobs')
            if jobs:
                row=jobs[0]
            else:
                source={'daily_check_job_key':key,'date':day,'scope':'daily_read_check_notify'}
                task_id=uuid.uuid4().hex
                db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                           (task_id,json.dumps(source),'received','received','{}',1,None,None,time.time()))
                row=taskctl.task(db,task_id); taskctl.event(db,row,'created',{})
            taskctl.verify_evidence(json.loads(row['checkpoint']))
            self.task_id=row['id']
            db.execute('UPDATE tasks SET owner=?,expires=?,version=version+1,updated=? WHERE id=?',
                       (owner,time.time()+3600,time.time(),self.task_id))
            taskctl.event(db,taskctl.task(db,self.task_id),'claim',{'owner':owner,'ttl':3600})
            db.execute('COMMIT')

    def checkpoint(self, status, step, paths, run_id):
        with closing(taskctl.connect(ROOT/'data/tasks.sqlite')) as db:
            db.execute('BEGIN IMMEDIATE')
            row=taskctl.task(db,self.task_id); taskctl.owned(row,self.owner)
            value={'status':status,'step':step,'run_id':run_id,
                   'next_step':'Inspect report and fresh candidates; ready_to_format does not mean formatted.',
                   'evidence_paths':list(dict.fromkeys(paths))}
            value['evidence_sha256']=taskctl.evidence(value['evidence_paths'])
            db.execute('UPDATE tasks SET status=?,step=?,checkpoint=?,version=version+1,updated=? WHERE id=?',
                       (status,step,json.dumps(value,ensure_ascii=False),time.time(),self.task_id))
            taskctl.event(db,taskctl.task(db,self.task_id),'checkpoint',value)
            db.execute('COMMIT')

    def finish(self):
        with closing(taskctl.connect(ROOT/'data/tasks.sqlite')) as db:
            db.execute('BEGIN IMMEDIATE')
            row=taskctl.task(db,self.task_id); taskctl.owned(row,self.owner)
            db.execute('UPDATE tasks SET owner=NULL,expires=NULL,version=version+1,updated=? WHERE id=?',
                       (time.time(),self.task_id))
            row=taskctl.task(db,self.task_id)
            taskctl.event(db,row,'release',{'owner':self.owner})
            handoff=taskctl.write_handoff(db,row,'daily-run-'+self.task_id+'-v'+str(row['version'])+'.json',True)
            db.execute('COMMIT')
            return str(Path(handoff).relative_to(ROOT))


def apply_decisions(observation, decisions):
    """Agent may confirm semantic identity with fresh evidence, without human approval."""
    if decisions.get('run_id') != observation['run_id'] or decisions.get('date') != observation['date']:
        raise ValueError('Decisions belong to another run')
    try:
        from .draft_identity import validate_identity
    except ImportError:
        from draft_identity import validate_identity
    matches={m['row']:m for m in observation['xiumi']['matches']}
    seen=set()
    for item in decisions.get('matches',[]):
        row=item['row']
        if row not in matches or row in seen:
            raise ValueError('Unexpected or duplicate decision row')
        seen.add(row)
        evidence=item.get('evidence_paths',[])
        reason=item.get('identity_reason')
        if not evidence or not isinstance(reason,str) or not reason.strip():
            raise ValueError('Semantic decision needs actual evidence and a reason')
        taskctl.evidence(evidence)
        candidate=[c for c in matches[row]['candidates'] if c['id']==item['id'] and c['title']==item['title']]
        if len(candidate)!=1 or candidate[0].get('available') is False:
            raise ValueError('Decision is not a currently observed candidate')
        validate_identity(item,matches[row]['candidates'])
        matches[row]['candidates']=[dict(candidate[0],identity_confirmed=True,identity_reason=reason)]
        observation['xiumi']['evidence_paths'].extend(evidence)
    return observation


def execute(config, label, notification='preview', decisions_path=None):
    if not label.replace('-','').replace('_','').isalnum():
        raise ValueError('Invalid run label')
    day=datetime.now(SHANGHAI).date().isoformat()
    folder=private_path('local/daily-runner/runs/'+label)
    folder.mkdir(parents=True,exist_ok=True)
    state_path=folder/'state.json'
    prior=read(state_path) if state_path.exists() else None
    if prior and prior['date']!=day:
        raise ValueError('Run identity belongs to another date; use a new identity for a new check')
    if prior and not decisions_path:
        # Allow a reviewed preview to be sent once without repeating browser operations.
        if notification=='send' and prior.get('notification',{}).get('status')=='not_sent':
            observation=read(private_path(prior['observation']))
            report=read(private_path(prior['report']))
            fresh=daily_check.evaluate(observation,day)
            if fresh['status']!=report['status'] or fresh['issues']!=report['issues']:
                raise ValueError('Preview is stale or changed; create a fresh read/check run')
            daily_check.check_evidence(observation)
            with local_lock(uuid.uuid4().hex):
                ledger=Ledger(config,day,'daily-send-'+uuid.uuid4().hex)
                try:
                    message=private_path(prior['message']).read_text(encoding='utf-8')
                    if message!=notify_wecom.result_from_report(report,observation):
                        raise ValueError('Preview message was changed')
                    prior['notify_mode']='send'
                    prior['notification']=notify_wecom.deliver(message,notify_wecom.configured_webhook(),execute=True)
                    evidence=[prior[k] for k in ('observation','report','message')]
                    receipt=prior['notification'].get('receipt')
                    if receipt:
                        evidence.append(str(Path(receipt).relative_to(ROOT)))
                    status='ready_for_review' if report['ready_to_format'] else 'completed' if report['status']=='no_reservations' else 'needs_manual'
                    ledger.checkpoint(status,'notification_'+prior['notification']['status'],evidence,label)
                finally:
                    prior['handoff']=ledger.finish(); save_state(state_path,prior)
        return prior  # Repeated execution never repeats browser operations or a notification.
    if decisions_path and (not prior or prior.get('phase')!='needs_attention'):
        raise ValueError('Decisions require this run to be awaiting matching review')
    token=uuid.uuid4().hex
    with local_lock(token):
        ledger=Ledger(config,day,'daily-'+token)
        state=prior or {'run_id':label,'date':day,'phase':'running','task_id':ledger.task_id,'notify_mode':notification}
        if state['date']!=day or state['task_id']!=ledger.task_id:
            ledger.finish()
            raise ValueError('Run date or task changed')
        state['notify_mode']=notification
        save_state(state_path,state)
        browser=Browser(folder/'browser',None,ledger.owner)
        evidence=[]
        try:
            if decisions_path:
                observation=apply_decisions(read(private_path(prior['observation'])),read(private_path(decisions_path)))
                # Must still pass the 60 minute gate. Long reviews need a new real run.
            else:
                stage='wps'
                observation={'date':day,'timezone':'Asia/Shanghai','observed_at':datetime.now(SHANGHAI).isoformat(),
                             'run_id':label,'reservation':{'date':day,'status':'failed','complete':False,'articles':[],
                             'evidence_paths':[]}}
                try:
                    with browser.reserved():
                        browser.command('start')
                        observed=wps_read.run(browser,config['wps_url'],day,config.get('reviewed_instructions_sha256'))
                        observation.update(observed); observation['wps_run_id']=observed['run_id']; observation['run_id']=label
                        state['phase']='wps_read'
                        write(folder/'wps-observation.json',observation)
                        save_state(state_path,state)
                        if observation['reservation']['status']=='ok' and observation['reservation']['articles']:
                            stage='xiumi'
                            observation['xiumi']=xiumi_scan.run(browser,config,observation['reservation']['articles'])
                except Stop as exc:
                    section='reservation' if stage=='wps' else 'xiumi'
                    existing=observation.setdefault(section,{})
                    existing.update(status='login_required' if exc.code in ('wps_login_required','xiumi_login_required') else 'failed',
                                    error_code=exc.code,evidence_paths=list(browser.evidence))
                    if section=='reservation':
                        existing['complete']=False
            observation_path=write(folder/('observation-'+uuid.uuid4().hex+'.json'),observation)
            report=daily_check.evaluate(observation,day)
            daily_check.check_evidence(observation)
            report_path=write(ROOT/'artifacts'/('daily-run-'+label+'-'+uuid.uuid4().hex+'.json'),report)
            message=notify_wecom.result_from_report(report,observation)
            message_path=folder/('message-'+uuid.uuid4().hex+'.txt')
            with message_path.open('x',encoding='utf-8') as f:
                f.write(message)
            evidence=browser.evidence+[str(observation_path.relative_to(ROOT)),str(report_path.relative_to(ROOT)),str(message_path.relative_to(ROOT))]
            state.update(phase=report['status'],observation=str(observation_path.relative_to(ROOT)),
                         report=str(report_path.relative_to(ROOT)),message=str(message_path.relative_to(ROOT)))
            ledger_status='ready_for_review' if report['ready_to_format'] else 'completed' if report['status']=='no_reservations' else 'needs_manual'
            ledger.checkpoint(ledger_status,'checked',evidence,label)
            if notification=='send':
                state['notification']=notify_wecom.deliver(message,notify_wecom.configured_webhook(),execute=True)
                receipt=state['notification'].get('receipt')
                if receipt:
                    evidence.append(str(Path(receipt).relative_to(ROOT)))
                ledger.checkpoint(ledger_status,'notification_'+state['notification']['status'],evidence,label)
            else:
                state['notification']={'status':'not_sent','mode':notification}
            save_state(state_path,state)
        except Exception as exc:
            state.update(phase='failed',error_code=getattr(exc,'code',type(exc).__name__))
            write(folder/('failure-'+uuid.uuid4().hex+'.json'),state)
            save_state(state_path,state)
            ledger.checkpoint('failed','check_interrupted',browser.evidence,label)
            # Unexpected failures have unknown booking counts: report only the failed stage.
            if notification=='send':
                message='未央宣传运营提醒｜'+day+'\n运行：'+label+'\n检查中断（'+state['error_code']+'），预约数量及整理结果尚未确认，请查看本机检查点。'
                state['notification']=notify_wecom.deliver(message,notify_wecom.configured_webhook(),execute=True)
            raise
        finally:
            state['handoff']=ledger.finish()
            save_state(state_path,state)
    return state


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=private_path,required=True)
    p.add_argument('--run',required=True,help='Stable identity of this execution; same identity never replays')
    p.add_argument('--notify',choices=('preview','send'),default='preview')
    p.add_argument('--decisions',type=private_path,help='Agent identity decisions for this needs_attention run')
    args=p.parse_args()
    state=execute(read(args.config),args.run,args.notify,args.decisions)
    print(json.dumps({k:state.get(k) for k in ('run_id','phase','task_id','report','handoff','notification')},ensure_ascii=False))
    notification_ok=(state.get('notify_mode')!='send' or state.get('notification',{}).get('status')=='accepted')
    return 0 if state['phase'] in ('ready_to_format','no_reservations') and notification_ok else 2


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (Stop,ValueError,OSError,KeyError,RuntimeError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code',type(exc).__name__),'detail':'Inspect private state and locks; no automatic replay'}))
        sys.exit(2)
