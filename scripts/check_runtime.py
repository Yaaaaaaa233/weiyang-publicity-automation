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


IMAGE_STATE = {'checked': False, 'result': None}
RUNTIME_STATE = {'checked': False, 'result': None, 'failure': None}


def runtime_probe(*args, seconds=600):
    """Run a runtime_window command; return its payload, or None when it cannot report.

    None means the probe itself failed to produce a verdict, which is different from a
    payload whose `ok` is False. Only the latter is evidence about the container.
    """
    try:
        process = subprocess.run([sys.executable, str(ROOT / 'scripts/runtime_window.py'), *args],
                                 cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                 timeout=seconds)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(process.stdout)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def browser_runtime_preflight(force=False):
    """Bring the container up before a run spends its read on it, or name the fault.

    Docker Desktop is closed while the machine is used for other work, so the run window
    owns the lifecycle. Only a proven absence blocks: an unreachable container cannot run
    a browser command at all, and reporting that here beats a generic bridge error that
    arrives after a long read. A probe that cannot report, or a container the bridge can
    still reach, must not invent a blocker.
    """
    if os.environ.get('WEIYANG_BROWSER_RUNTIME', '1') == '0':
        return None
    if not RUNTIME_STATE['checked'] or force:
        payload = runtime_probe('up')
        failure = None
        # Confirm independently before blocking: a probe that reports a fault must not
        # stop a container the ordinary bridge can still reach.
        if payload is not None and payload.get('ok') is not True and bridge('doctor') is None:
            failure = payload
        RUNTIME_STATE.update(checked=True, result=payload, failure=failure)
    failure = RUNTIME_STATE['failure']
    if failure:
        path = write('local/browser-image-drift/runtime-' + uuid.uuid4().hex + '.json', failure)
        raise Stop('browser_runtime_unavailable',
                   'Docker browser runtime is unavailable: ' + str(failure.get('message')) +
                   '. Record: ' + str(path.relative_to(ROOT)))
    result = RUNTIME_STATE['result']
    return result.get('result') if result and result.get('ok') is True else None


def bridge(*args, seconds=180):
    """Run a browserctl bridge command; return its result or None when unreachable."""
    try:
        process = subprocess.run([sys.executable, str(ROOT / 'scripts/browserctl.py'), *args],
                                 cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                 timeout=seconds)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(process.stdout)
    except ValueError:
        return None
    if process.returncode or payload.get('ok') is not True:
        return None
    return payload.get('result')


def browser_image_preflight(force=False):
    """Refuse to spend a run on a container whose browser.py predates the repository.

    browser/Dockerfile bakes scripts/browser.py into the image, so a stale copy rejects
    subcommands the repository already implements. That used to surface only as a generic
    bridge error after a long WPS read had already been spent, and buried the real cause.
    Only a proven mismatch blocks the run: an unreachable bridge returns None so the
    ordinary browser command still reports the genuine failure.
    """
    if not IMAGE_STATE['checked'] or force:
        result = bridge('doctor')
        if result and not result.get('match') and os.environ.get('WEIYANG_BROWSER_SELFHEAL', '1') != '0':
            # Rebuild needs network and recreates the container; a copy-in does not.
            bridge('doctor', '--heal')
            result = bridge('doctor')
        IMAGE_STATE.update(checked=True, result=result)
    result = IMAGE_STATE['result']
    if result and not result.get('match'):
        path = write('local/browser-image-drift/drift-' + uuid.uuid4().hex + '.json', result)
        raise Stop('browser_image_stale', 'Container browser.py predates the repository; rebuild the image. '
                                          'Record: ' + str(path.relative_to(ROOT)))
    return result


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
        if log and args[0] in ('inspect','inspect-editor-ui','screenshot','copy-editor') and log.capacity()['exceeded']:
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

    def snapshot(self, frame=None, selector='body'):
        """Read one rendered subtree.

        `selector` must match exactly one element (the bridge enforces that). It defaults to the
        whole page, which is what most callers want -- but a whole-page read is capped at 3000
        visible elements and that cap stopped the 2026-10-12 18:10 round on a large compilation
        draft. Callers that need only part of the page should pass a narrower selector; the
        truncation guard below still applies to whatever they ask for, so scoping can never be
        used to smuggle a partial read past the check.
        """
        args = ['inspect',selector,'check-'+uuid.uuid4().hex+'.json']
        if frame:
            args += ['--frame',frame]
        result = self.command(*args)
        snapshot = read(result['local_artifact'])
        if snapshot.get('nodes_truncated') or snapshot.get('text_truncated'):
            raise Stop('truncated_page','Rendered page was truncated')
        return snapshot

    def editor_ui(self):
        """Rendered editor controls only; explicitly excludes article body evidence."""
        result = self.command('inspect-editor-ui','editor-ui-'+uuid.uuid4().hex+'.json')
        snapshot = read(result['local_artifact'])
        if snapshot.get('nodes_truncated') is not False or snapshot.get('text_truncated') is not False:
            raise Stop('truncated_page','Editor controls were truncated')
        if snapshot.get('ui_only') is not True or snapshot.get('excluded_selector') != '.tn-page-vessel':
            raise Stop('editor_ui_unverified','Bridge did not return the explicit controls-only view')
        return snapshot

    def click(self, node, frame=None):
        args = ['click-element',node.get('stable_selector') or node['selector'],'--expect-text',node.get('rendered_text',node['own_text'])]
        if frame:
            args += ['--frame',frame]
        return self.command(*args)

    def wait(self, predicate, frame=None, seconds=30, read=None):
        """Poll until `predicate` holds.

        `read` overrides how each snapshot is taken, so a caller that already knows a narrower
        scope can poll that scope instead of the whole page (see snapshot_body). It defaults to
        this browser's own snapshot.
        """
        read = read or (lambda: self.snapshot(frame))
        deadline = time.monotonic()+seconds
        while True:
            snapshot = read()
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
        browser_runtime_preflight()
        browser_image_preflight()
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
