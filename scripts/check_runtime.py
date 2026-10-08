"""Shared private I/O and bounded browser reads for the daily check wrappers."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


class Stop(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def private_path(path):
    path = (ROOT / path).resolve()
    if not any(path.is_relative_to(ROOT / d) for d in ('local','artifacts','data')):
        raise ValueError('Use a private project path')
    return path


def write(path, data):
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def has_class(node, name):
    return name in node.get('class','').split()


def one(nodes, predicate, code='changed_layout'):
    selected = [n for n in nodes if predicate(n)]
    if len(selected) != 1:
        raise Stop(code, 'Expected one currently rendered control; inspect the private evidence.')
    return selected[0]


class Browser:
    def __init__(self, folder, token=None, task_owner=None):
        self.folder = private_path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.task_owner = task_owner
        self.evidence = []

    def command(self, *args):
        started=time.monotonic()
        log=getattr(self,'log',None)
        if log and args[0] in ('inspect','screenshot','copy-editor') and log.capacity()['exceeded']:
            raise Stop('log_capacity_exceeded','Private runtime storage limit reached; review retention before adding evidence')
        env = os.environ.copy()
        if self.token:
            env['WEIYANG_BROWSER_LEASE'] = self.token
        try:
            proc = subprocess.run([sys.executable,str(ROOT/'scripts/browserctl.py'),*args],
                                  cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
        except subprocess.TimeoutExpired as exc:
            path = write(self.folder/('timeout-'+uuid.uuid4().hex+'.json'),
                         {'command':args[0],'status':'outcome_unknown'})
            self.evidence.append(str(path.relative_to(ROOT)))
            raise Stop('browser_command_timeout','Command outcome unknown; inspect before resuming') from exc
        path = write(self.folder/('action-'+uuid.uuid4().hex+'.json'),
                     {'command':args[0],'stdout':proc.stdout,'stderr':proc.stderr,'exit_code':proc.returncode})
        self.evidence.append(str(path.relative_to(ROOT)))
        if log:
            log.event('browser_'+args[0], 'finished' if proc.returncode==0 else 'failed',time.monotonic()-started)
            log.evidence(path)
        try:
            result = json.loads(proc.stdout)
        except ValueError as exc:
            raise Stop('browser_unavailable','Browser bridge did not return a valid response') from exc
        if proc.returncode or result.get('ok') is not True:
            raise Stop(result.get('error','browser_failed'),'Browser command failed; no automatic replay')
        value = result['result']
        if isinstance(value,dict) and value.get('local_artifact'):
            self.evidence.append(str(Path(value['local_artifact']).relative_to(ROOT)))
            if log:log.evidence(Path(value['local_artifact']))
        if isinstance(value,dict) and value.get('local_screenshot'):
            self.evidence.append(str(Path(value['local_screenshot']).relative_to(ROOT)))
            if log:log.evidence(Path(value['local_screenshot']))
        return value

    def snapshot(self, frame=None):
        args = ['inspect','body','check-'+uuid.uuid4().hex+'.json']
        if frame:
            args += ['--frame',frame]
        result = self.command(*args)
        snapshot = read(result['local_artifact'])
        if snapshot.get('nodes_truncated') or snapshot.get('text_truncated'):
            raise Stop('truncated_page','Rendered page was truncated')
        return snapshot

    def click(self, node, frame=None):
        args = ['click-element',node.get('stable_selector') or node['selector'],'--expect-text',node.get('rendered_text',node['own_text'])]
        if frame:
            args += ['--frame',frame]
        return self.command(*args)

    def wait(self, predicate, frame=None, seconds=30):
        deadline = time.monotonic()+seconds
        while True:
            snapshot = self.snapshot(frame)
            try:
                if predicate(snapshot):
                    return snapshot
            except Stop as exc:
                if exc.code not in ('pagination_unknown','library_total_unknown','changed_layout'):
                    raise
            if time.monotonic() >= deadline:
                raise Stop('page_timeout','Expected page state did not appear')
            time.sleep(.4)

    @contextlib.contextmanager
    def reserved(self):
        if self.token:
            yield self
            return
        database=ROOT/'data/tasks.sqlite'
        if database.exists():
            with contextlib.closing(sqlite3.connect(database)) as db:
                active=db.execute('SELECT owner FROM tasks WHERE owner IS NOT NULL AND expires>?',(time.time(),)).fetchall()
            if any(owner != self.task_owner for (owner,) in active):
                raise Stop('executor_busy','Another ledger executor has an active lease')
        self.token = uuid.uuid4().hex
        acquired = False
        try:
            self.command('lease-acquire'); acquired = True
            yield self
        finally:
            if acquired:
                self.command('lease-release')
            self.token = None


def find_window(browser, url):
    windows = browser.command('windows')['windows']
    candidates = [w for w in windows if w['url'] == url]
    if len(candidates) > 1:
        raise Stop('ambiguous_window','Multiple document windows exist; select one explicitly')
    if candidates:
        browser.command('switch-window',candidates[0]['handle'])
    else:
        browser.command('new-window',url)
