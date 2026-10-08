"""Private per-execution logs. Bounded fields; no prompts, credentials or manuscript text."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = timezone(timedelta(hours=8))
FINAL = {'completed', 'no_reservations', 'failed', 'needs_attention', 'awaiting_confirmation', 'ready_to_format'}

def now():
    return datetime.now(SHANGHAI).isoformat()

def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)

def label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value):
        raise ValueError('Use a short non-sensitive identifier')
    return value

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

class RunLog:
    def __init__(self, run_id, day, node=None, root=None):
        label(run_id); datetime.fromisoformat(day)
        self.root = root or ROOT
        self.folder = self.root / 'local/run-logs' / run_id
        self.path = self.folder / 'summary.json'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.depth=0
        if self.path.exists():
            self.value = load(self.path)
            if self.value['date'] != day:
                raise ValueError('Run ID belongs to another business date')
        else:
            self.value = {'schema_version': 1, 'run_id': run_id, 'date': day,
                          'node': label(node) if node else 'unspecified', 'started_at': now(),
                          'updated_at': now(), 'status': 'running', 'stages': {},
                          'reservation_count': None, 'formatted_count': None,
                          'sync_status': 'not_started', 'notification_status': 'not_sent',
                          'human_interventions': 0, 'usage': {'status': 'unknown', 'requests': 0},
                          'evidence': [], 'evidence_bytes': 0, 'active': False,
                          'protected': True}
            atomic(self.path, self.value)

    def save(self):
        self.value['updated_at'] = now(); atomic(self.path, self.value)

    def event(self, stage, status, seconds=None, error_code=None):
        item = {'at': now(), 'stage': label(stage), 'status': label(status)}
        if seconds is not None:
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError('Invalid duration')
            item['seconds'] = round(seconds, 3)
        if error_code:
            item['error_code'] = label(error_code)
        with (self.folder / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(item, ensure_ascii=False) + '\n'); f.flush(); os.fsync(f.fileno())
        self.value['stages'][stage] = item; self.save()

    @contextmanager
    def stage(self, stage):
        started = time.monotonic(); self.depth+=1; self.value['active'] = True; self.event(stage, 'started')
        try:
            yield
        except Exception as exc:
            # Exception messages often contain secrets or manuscript data; never log them.
            self.event(stage, 'failed', time.monotonic()-started, type(exc).__name__)
            raise
        else:
            self.event(stage, 'finished', time.monotonic()-started)
        finally:
            self.depth-=1; self.value['active'] = self.depth>0; self.save()

    def result(self, status, reservation_count=None, formatted_count=None,
               sync_status=None, notification_status=None, protected=True):
        label(status)
        for key, value in {'reservation_count': reservation_count, 'formatted_count': formatted_count}.items():
            if value is not None:
                if type(value) is not int or value < 0:
                    raise ValueError('Invalid result count')
                self.value[key] = value
        for key, value in {'sync_status':sync_status, 'notification_status':notification_status}.items():
            if value is not None:
                self.value[key] = label(value)
        self.value.update(status=status, protected=bool(protected))
        self.value['log_bytes']=sum(p.stat().st_size for p in self.folder.iterdir() if p.is_file())
        if status in FINAL:self.value['finished_at']=now()
        self.save()

    def usage(self, record):
        # Explicit per-call contract; session cumulative counters are not accepted.
        required = {'request_id', 'model', 'started_at', 'finished_at', 'source',
                    'input_tokens', 'cache_read_tokens', 'output_tokens', 'cache_write_tokens'}
        if set(record) != required:
            raise ValueError('Usage requires an exact per-request record, not a cumulative total')
        for k in ('request_id', 'model', 'source'):
            label(record[k])
        start, end = (datetime.fromisoformat(record[k]) for k in ('started_at','finished_at'))
        if start.tzinfo is None or end.tzinfo is None or end < start or end>datetime.now(SHANGHAI)+timedelta(seconds=60):
            raise ValueError('Usage timestamps need timezone and chronological order')
        if start < datetime.fromisoformat(self.value['started_at']):
            raise ValueError('Usage predates this execution')
        for k in ('input_tokens','cache_read_tokens','output_tokens','cache_write_tokens'):
            if record[k] is not None and (type(record[k]) is not int or record[k] < 0):
                raise ValueError('Tokens must be nonnegative integers or unknown')
        path = self.folder/'usage.jsonl'
        existing = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []
        same = [r for r in existing if r['request_id'] == record['request_id']]
        if same:
            if same[0] != record:
                raise ValueError('Conflicting usage for the same request')
            return False
        if (self.folder/'usage.jsonl.gz').exists() or self.value.get('details_cleaned'):
            raise ValueError('Archived run cannot receive new usage; restore records first')
        with path.open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
        records = existing + [record]
        total = {'status':'measured', 'requests':len(records), 'cost':None, 'cost_status':'unknown'}
        for k in ('input_tokens','cache_read_tokens','output_tokens','cache_write_tokens'):
            total[k] = sum(r[k] for r in records) if all(r[k] is not None for r in records) else None
        if any(total[k] is None for k in ('input_tokens','cache_read_tokens','output_tokens','cache_write_tokens')):
            total['status']='partial'
        self.value['usage']=total; self.save(); return True

    def intervention(self, kind):
        if kind not in ('approval','login','unlock','manual_repair'):
            raise ValueError('Unknown intervention kind')
        self.value['human_interventions']+=1
        self.event('human_'+kind,'observed')

    def capacity(self, max_bytes=5*1024*1024*1024):
        if type(max_bytes) is not int or max_bytes<1:raise ValueError('Invalid storage limit')
        size=sum(p.stat().st_size for name in ('local','artifacts')
                 for p in (self.root/name).rglob('*') if p.is_file() and not p.is_symlink())
        return {'bytes':size,'max_bytes':max_bytes,'exceeded':size>=max_bytes}

    def evidence(self, source):
        source = Path(source).resolve()
        if not any(source.is_relative_to((self.root/d).resolve()) for d in ('artifacts','local')):
            raise ValueError('Evidence must be a private project artifact')
        if source.is_symlink() or not source.is_file() or source.suffix.lower() not in ('.json','.png','.txt'):
            raise ValueError('Unsupported evidence type')
        if any(s in source.name.lower() for s in ('cookie','credential','secret','.env','profile')):
            raise ValueError('Login and credential files are not evidence')
        # Content is not copied: existing evidence stays under its owning task's retention policy.
        value = {'path':str(source.relative_to(self.root)), 'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                 'bytes':source.stat().st_size}
        if not any(e['sha256']==value['sha256'] for e in self.value['evidence']):
            self.value['evidence'].append(value)
            self.value['evidence_bytes'] += value['bytes']; self.save()
        return value


def cleanup(execute=False, keep_days=90, compress_days=7, max_bytes=512*1024*1024, clock=None):
    """Touch only log-owned detail files. Never delete task or notification ledgers/evidence."""
    if keep_days < 1 or compress_days < 1 or max_bytes < 1:
        raise ValueError('Invalid retention policy')
    clock = clock or datetime.now(SHANGHAI)
    base = ROOT/'local/run-logs'; actions=[]; total=0
    if base.exists():
        for folder in base.iterdir():
            if folder.is_symlink() or not folder.is_dir(): continue
            total += sum(p.stat().st_size for p in folder.iterdir() if p.is_file() and not p.is_symlink())
            summary=folder/'summary.json'
            if not summary.exists(): continue
            value=load(summary)
            if value.get('active') or value.get('protected',True): continue
            age=(clock-datetime.fromisoformat(value['updated_at'])).total_seconds()/86400
            for name in ('events.jsonl','usage.jsonl','events.jsonl.gz','usage.jsonl.gz'):
                p=folder/name
                if not p.exists() or p.is_symlink(): continue
                action='delete' if age>=keep_days else 'compress' if age>=compress_days and p.suffix!='.gz' else None
                if not action: continue
                actions.append({'path':str(p.relative_to(ROOT)), 'action':action, 'bytes':p.stat().st_size})
                if execute:
                    if action=='compress':
                        target=p.with_suffix(p.suffix+'.gz')
                        with target.open('xb') as f: f.write(gzip.compress(p.read_bytes(),mtime=0))
                    p.unlink()
                    if action=='delete': value['details_cleaned']=True
            if execute and actions: atomic(summary,value)
    return {'execute':execute, 'actions':actions, 'total_bytes_before':total,
            'capacity_exceeded':total>max_bytes, 'max_bytes':max_bytes,
            'note':'Capacity warning never removes protected logs or task evidence.'}


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    u=sub.add_parser('usage');u.add_argument('--run',required=True);u.add_argument('--date',required=True);u.add_argument('--file',type=Path,required=True)
    i=sub.add_parser('intervention');i.add_argument('--run',required=True);i.add_argument('--date',required=True);i.add_argument('--kind',choices=('approval','login','unlock','manual_repair'),required=True)
    c=sub.add_parser('cleanup');c.add_argument('--execute',action='store_true');c.add_argument('--keep-days',type=int,default=90);c.add_argument('--compress-days',type=int,default=7);c.add_argument('--max-bytes',type=int,default=512*1024*1024)
    args=p.parse_args()
    if args.command=='usage':
        path=args.file.resolve()
        if not any(path.is_relative_to((ROOT/d).resolve()) for d in ('local','artifacts')):raise ValueError('Private usage input required')
        log=RunLog(args.run,args.date)
        count=sum(log.usage(r) for r in load(path));print(json.dumps({'imported':count,'usage':log.value['usage']}))
    elif args.command=='intervention':
        log=RunLog(args.run,args.date);log.intervention(args.kind);print(json.dumps({'human_interventions':log.value['human_interventions']}))
    else:print(json.dumps(cleanup(args.execute,args.keep_days,args.compress_days,args.max_bytes),ensure_ascii=False))

if __name__=='__main__':main()
