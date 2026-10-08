"""Supervised Xiumi composition workflow. No hidden APIs or WeChat backend access.

Plans, snapshots, action logs and durable handoffs stay in ignored local directories.
prepare never submits; submit requires --execute and records intent before clicking.
"""
import argparse
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

try:
    from .check_sync_composition import check, has_class, inside
    from . import taskctl
    from .check_runtime import Browser as RuntimeBrowser
except ImportError:
    from check_sync_composition import check, has_class, inside
    import taskctl
    from check_runtime import Browser as RuntimeBrowser

ROOT = Path(__file__).resolve().parents[1]
OWNER = 'sync-workflow'


class Stop(RuntimeError):
    """A bounded, public message without manuscript details."""


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_plan(plan):
    # Reuse the independent checker's schema validation, without browser operations.
    check({'nodes': [], 'backgrounds': []}, plan)
    if plan.get('plan_confirmed') is not True:
        raise Stop('Resolve reservation and article choices before preparing a composition.')
    url = urlsplit(plan.get('composer_url', ''))
    if url.scheme != 'https' or url.hostname != 'xiumi.us' or url.username or url.password:
        raise Stop('Use the composition URL actually observed on the official Xiumi page.')
    if len([a for a in plan['articles'] if a['is_headline']]) != 1:
        raise Stop('Exactly one confirmed headline is required.')
    for article in plan['articles']:
        if not isinstance(article.get('source_title'), str) or not article['source_title'].strip():
            raise Stop('Each article needs its confirmed complete library title.')
    if len({a['source_title'] for a in plan['articles']}) != len(plan['articles']):
        raise Stop('Resolve same-title versions with distinct confirmed copies first.')
    return plan


def ordered(plan):
    rows = sorted(plan['articles'], key=lambda a: a['row'])
    return [a for a in rows if a['is_headline']] + [a for a in rows if not a['is_headline']]


def job_key(plan):
    # One batch per account and day, including across different run folder names.
    return digest({'date': plan['date'], 'account': plan['target_account']})


@contextmanager
def runner_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise Stop('Workflow lock exists. Check the previous executor before recovery.') from exc
    token = uuid.uuid4().hex
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid(), 'token': token}, stream)
        yield
    finally:
        if path.exists() and read(path).get('token') == token:
            path.unlink()


def target(node):
    """Prefer a stable attribute selector over a cached positional path.

    Snapshots are taken before an action, and xiumi's #/wxpack re-renders its
    top-level nodes while a selection is in progress. A cached ``:nth-of-type``
    chain can therefore match nothing (``ambiguous focus``) or the wrong element
    by action time; ``stable_selector`` pins the element by its own attributes.
    Older snapshots without the field keep working through the fallback.
    """
    return node.get('stable_selector') or node['selector']


def complete(snapshot):
    if snapshot.get('nodes_truncated') is not False or snapshot.get('text_truncated') is not False or not snapshot.get('text'):
        raise Stop('Snapshot is incomplete; inspect again instead of acting.')
    if urlsplit(snapshot.get('url', '')).hostname != 'xiumi.us':
        raise Stop('Current page is not Xiumi; log in or restore the correct window.')


def title_controls(snapshot):
    complete(snapshot)
    nodes = snapshot['nodes']
    roots = [n for n in nodes if has_class(n, 'x3-package')]
    if not roots and not any(has_class(n, 'x3-slice-plate') for n in nodes):
        return []
    if len(roots) != 1:
        raise Stop('Composition identity is ambiguous.')
    plates = sorted([n for n in nodes if has_class(n, 'x3-slice-plate') and inside(n, roots[0])],
                    key=lambda n: n['rect']['y'])
    if len({p['rect']['y'] for p in plates}) != len(plates):
        raise Stop('Composition row positions are ambiguous.')
    controls = []
    for plate in plates:
        values = [n for n in nodes if inside(n, plate) and n['tag'] == 'input' and has_class(n, 'inner')]
        if len(values) != 1:
            raise Stop('Composition title control is ambiguous.')
        controls.append(values[0])
    return controls


def prefix_length(snapshot, plan):
    controls, articles = title_controls(snapshot), ordered(plan)
    if len(controls) > len(articles):
        raise Stop('Unexpected articles already selected; do not delete them automatically.')
    for index, control in enumerate(controls):
        allowed = {articles[index]['sync_title']}
        # A crash may occur after selection and before the last title is adjusted.
        if index == len(controls) - 1:
            allowed.add(articles[index]['source_title'])
        if control['value'] not in allowed:
            raise Stop('Selected titles are not the expected prefix; manual recovery is required.')
    return len(controls)


def library_matches(snapshot, expected):
    complete(snapshot)
    nodes = snapshot['nodes']
    roots = [n for n in nodes if has_class(n, 'x3-package')]
    matches = []
    for card in nodes:
        if not has_class(card, 'article-container') or any(inside(card, r) for r in roots):
            continue
        titles = [n for n in nodes if inside(n, card) and has_class(n, 'title')]
        candidates = [n for n in nodes if n['tag'] != 'input' and n.get('own_text') == expected and
                      has_class(n, 'inner') and any(inside(n, t) for t in titles)]
        matches.extend(candidates)
    return matches


def library_title(snapshot, expected):
    matches = library_matches(snapshot, expected)
    if len(matches) != 1:
        raise Stop('Missing or ambiguous exact-title match. Confirm the article version first.')
    return matches[0]


def one(nodes, predicate, message):
    values = [n for n in nodes if predicate(n)]
    if len(values) != 1:
        raise Stop(message)
    return values[0]


class Browser(RuntimeBrowser):
    def __init__(self, folder):
        super().__init__(folder, task_owner=OWNER)

    def command(self, *args):
        started=time.monotonic()
        log=getattr(self,'log',None)
        if log and args[0] in ('inspect','screenshot') and log.capacity()['exceeded']:
            raise Stop('Private runtime evidence storage limit reached.')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/browserctl.py'), *args],
                                cwd=ROOT, env={**os.environ, **({'WEIYANG_BROWSER_LEASE':self.token} if self.token else {})},
                                capture_output=True, text=True, encoding='utf-8', timeout=180)
        path = self.folder / ('action-' + uuid.uuid4().hex + '.json')
        write(path, {'command': args[0], 'args': list(args[1:]), 'exit_code': result.returncode,
                     'stdout': result.stdout, 'stderr': result.stderr})
        self.evidence.append(str(path.relative_to(ROOT)))
        if log:
            log.event('sync_'+args[0],'finished' if result.returncode==0 else 'failed',time.monotonic()-started)
            log.evidence(path)
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise Stop('Browser bridge returned no valid response; inspect before retrying.') from exc
        if result.returncode or not payload.get('ok'):
            raise Stop('Browser action failed. Private action log saved; no automatic replay.')
        return payload['result']

    def snapshot(self):
        result = self.command('inspect', 'body', 'sync-' + uuid.uuid4().hex + '.json')
        path = Path(result['local_artifact'])
        self.evidence.append(str(path.relative_to(ROOT)))
        snapshot = read(path)
        complete(snapshot)
        if getattr(self,'log',None):self.log.evidence(path)
        return snapshot

    def wait(self, predicate, seconds=20):
        deadline = time.monotonic() + seconds
        while True:
            snapshot = self.snapshot()
            if predicate(snapshot):
                return snapshot
            if time.monotonic() >= deadline:
                raise Stop('Expected page state did not settle. Inspect without replaying the action.')
            time.sleep(0.5)

    def click_text(self, node, text):
        return self.command('click-element', target(node), '--expect-text', text)

    def menu(self, plan):
        snapshot = self.snapshot()
        if not any(has_class(n, 'wx-username') for n in snapshot['nodes']):
            heading = one(snapshot['nodes'], lambda n: n.get('own_text') == '同步到公众号',
                          'Sync menu is unavailable; restore the composition page.')
            self.click_text(heading, '同步到公众号')
            snapshot = self.wait(lambda s: any(has_class(n, 'wx-username') for n in s['nodes']))
        accounts = [n['own_text'] for n in snapshot['nodes'] if has_class(n, 'wx-username')]
        if accounts != [plan['target_account']]:
            raise Stop('Expected unique target account is unavailable. Do not authorize or choose another automatically.')
        return snapshot

    def search(self, title):
        snapshot = self.snapshot()
        def search_input(n):
            return n['tag'] == 'input' and n.get('placeholder') == '输入关键词后按回车键'
        if not any(search_input(n) for n in snapshot['nodes']):
            icon = one(snapshot['nodes'], lambda n: has_class(n, 'tn-tpl-search-icon'),
                       'Library search entry is ambiguous.')
            self.click_text(icon, '')
            snapshot = self.wait(lambda s: any(search_input(n) for n in s['nodes']))
        control = one(snapshot['nodes'], search_input, 'Search control is ambiguous.')
        # Shorter queries are only read-only search fallbacks. Selection still needs
        # one exact complete title, never fuzzy matching or the first returned card.
        queries = search_queries(title)
        for query in queries:
            self.command('focus', target(control))
            self.command('key', 'SelectAll')
            self.command('type', query)
            self.command('key', 'Enter')
            deadline = time.monotonic() + 12
            while True:
                first = self.snapshot()
                matches = library_matches(first, title)
                if len(matches) > 1:
                    raise Stop('Multiple exact-title versions found; confirm a distinct copy first.')
                if matches:
                    time.sleep(0.5)
                    second = self.snapshot()
                    settled = library_title(second, title)
                    if target(settled) != target(matches[0]):
                        raise Stop('Library results are still changing; inspect before selecting.')
                    return settled
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
            control = one(first['nodes'], search_input, 'Search control changed.')
        raise Stop('No exact-title match found after bounded searches; confirm the manuscript first.')


def assemble(browser, plan, record_action):
    browser.menu(plan)  # Verify account before selecting any manuscript.
    snapshot = browser.snapshot()
    count = prefix_length(snapshot, plan)
    articles = ordered(plan)

    def finish_title(index, snapshot):
        control = title_controls(snapshot)[index]
        expected = articles[index]['sync_title']
        if control['value'] == expected:
            return snapshot
        if control['value'] != articles[index]['source_title']:
            raise Stop('Title changed unexpectedly; do not overwrite it.')
        record_action('title_edit_intent', index)
        browser.command('focus', target(control))
        browser.command('key', 'SelectAll')
        browser.command('type', expected)
        browser.command('key', 'Tab')
        return browser.wait(lambda s: len(title_controls(s)) == index + 1 and
                            title_controls(s)[index]['value'] == expected)

    if count:
        snapshot = finish_title(count - 1, snapshot)
    for index in range(count, len(articles)):
        candidate = browser.search(articles[index]['source_title'])
        record_action('selection_intent', index)
        browser.click_text(candidate, articles[index]['source_title'])
        snapshot = browser.wait(lambda s: len(title_controls(s)) == index + 1)
        if prefix_length(snapshot, plan) != index + 1:
            raise Stop('Selected article was not confirmed; do not click again.')
        snapshot = finish_title(index, snapshot)
        record_action('selection_verified', index)
    return browser.menu(plan)


def search_queries(title):
    prefix = re.split(r'[丨|｜]', title, maxsplit=1)[0].strip()
    # A unique test prefix is useful for distinct copies whose long title contains spaces.
    marker = re.match(r'【[^】]+】', title)
    candidates = [title, marker.group(0) if marker else prefix]
    return list(dict.fromkeys(q for q in candidates if q))


class Ledger:
    def __init__(self, folder, plan):
        self.folder, self.plan = folder, plan
        self.state_path = folder / 'state.json'
        self.claimed = False
        with closing(taskctl.connect(ROOT / 'data/tasks.sqlite')) as db:
            jobs = [dict(r) for r in db.execute('SELECT * FROM tasks')
                    if json.loads(r['source']).get('workflow_job_key') == job_key(plan)]
        if len(jobs) > 1:
            raise Stop('Multiple records for the same account/day; reconcile them before continuing.')
        if jobs:
            source = json.loads(jobs[0]['source'])
            if source.get('run_folder') != str(folder.relative_to(ROOT)) or source.get('plan_sha256') != digest(plan):
                raise Stop('This account/day already has a job. Resume its original run; do not create another.')
            self.task_id = jobs[0]['id']
            if not self.state_path.is_file():
                self.state = {'task_id': self.task_id, 'phase': 'created', 'plan_sha256': digest(plan)}
            else:
                self.state = read(self.state_path)
                if self.state['task_id'] != self.task_id or self.state['plan_sha256'] != digest(plan):
                    raise Stop('Run state and ledger identity differ.')
        else:
            if self.state_path.exists():
                raise Stop('Run ledger is missing. Restore/import the handoff before continuing.')
            source = {'workflow_job_key': job_key(plan), 'run_folder': str(folder.relative_to(ROOT)),
                      'plan_sha256': digest(plan), 'date': plan['date'], 'scope': 'xiumi_supervised_sync'}
            write(folder / 'source.json', source)
            self.task_id = self.tool('create', str(folder / 'source.json'))['id']
            self.state = {'task_id': self.task_id, 'phase': 'created', 'plan_sha256': digest(plan)}
        self.save()

    def tool(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/taskctl.py'), *args],
                                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
        payload = json.loads(result.stdout)
        if result.returncode or not payload.get('ok'):
            raise Stop('Task ledger rejected this operation; check ownership and evidence.')
        return payload['result']

    def current(self):
        with closing(taskctl.connect(ROOT / 'data/tasks.sqlite')) as db:
            row = taskctl.task(db, self.task_id)
            row['checkpoint'] = json.loads(row['checkpoint'])
            history = [json.loads(r['data']) for r in db.execute(
                'SELECT data FROM events WHERE task_id=?', (self.task_id,))]
        taskctl.verify_evidence(row['checkpoint'])
        row['workflow_submission_reserved'] = any(v.get('submission_reserved') is True or
                                                  v.get('step') == 'submission_intent' for v in history)
        attempts = max([self.plan['prior_sync_attempts'], self.state.get('sync_attempt_count', 0)] +
                       [v.get('sync_attempt_count', 0) for v in history])
        return row, attempts

    def claim(self):
        result = self.tool('claim', self.task_id, OWNER, '--ttl', '3600')
        self.version, self.claimed = result['version'], True
        self.state.update({'version': self.version, 'lease_released': False})
        self.save()

    def save(self):
        write(self.state_path, self.state)

    def checkpoint(self, status, phase, evidence, **extra):
        row, attempts = self.current()
        value = {'status': status, 'step': phase, 'next_step': 'Inspect current platform state and this checkpoint before continuing.',
                 'evidence_paths': list(dict.fromkeys([str((self.folder / 'plan.json').relative_to(ROOT)),
                                                       *row['checkpoint'].get('evidence_paths', []), *evidence])),
                 'sync_attempt_count': attempts, 'submission_reserved': row['workflow_submission_reserved'], **extra}
        path = self.folder / ('checkpoint-' + uuid.uuid4().hex + '.json')
        write(path, value)
        result = self.tool('checkpoint', self.task_id, OWNER, str(path), '--version', str(self.version))
        self.version = result['version']
        self.state.update({'phase': phase, 'sync_attempt_count': value['sync_attempt_count'],
                           'submission_reserved': value['submission_reserved'], **extra})
        self.save()

    def finish(self):
        if self.claimed:
            result = self.tool('release', self.task_id, OWNER)
            self.version, self.claimed = result['version'], False
            filename = 'sync-workflow-' + self.task_id + '-v' + str(self.version) + '.json'
            exported = self.tool('export', self.task_id, filename)
            self.state.update({'version': self.version, 'handoff_path': str(Path(exported['file']).relative_to(ROOT)),
                               'lease_released': True})
            self.save()


def reserve_submission(ledger, evidence):
    row, attempts = ledger.current()
    if attempts or row.get('workflow_submission_reserved') or row['status'] == 'completed' or ledger.state['phase'] != 'prepared':
        raise Stop('Submission has already been reserved or this job is not prepared. Do not replay.')
    # Commit the uncertain outcome BEFORE touching the button. A crash is conservative.
    ledger.checkpoint('verification_pending', 'submission_intent', evidence,
                      sync_attempt_count=1, submission_reserved=True, final_platform_result='unconfirmed')
    return ledger.state


def run(args):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}', args.run):
        raise Stop('Use a short run name containing letters, digits, underscores or hyphens.')
    folder = ROOT / 'local/sync-workflow' / args.run
    if args.command == 'prepare':
        plan = validate_plan(read(args.plan))
        if folder.exists() and not args.resume:
            raise Stop('Run exists; use --resume after observing the current page.')
        if args.resume and not (folder / 'plan.json').is_file():
            raise Stop('Cannot resume without the original private plan and handoff.')
        if (folder / 'plan.json').is_file() and digest(read(folder / 'plan.json')) != digest(plan):
            raise Stop('Plan changed. Resolve the existing job instead of silently changing it.')
    else:
        if not (folder / 'state.json').is_file():
            raise Stop('Unknown run. Prepare a confirmed private plan first.')
        plan = validate_plan(read(folder / 'plan.json'))
    with runner_lock(ROOT / 'data/sync-workflow.lock'):
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / 'plan.json').exists():
            write(folder / 'plan.json', plan)
        ledger = Ledger(folder, plan)
        ledger.current()  # Verify evidence before claiming or browser operations.
        if args.command == 'submit' and not args.execute:
            raise Stop('Submission needs the explicit --execute option; prepare does not submit.')
        ledger.claim()
        browser = Browser(folder)
        browser.log=getattr(args,'run_log',None)
        try:
            with (browser.reserved() if args.command != 'confirm' else nullcontext()):
                if args.command == 'confirm':
                    row, attempts = ledger.current()
                    if not row['workflow_submission_reserved'] or not attempts or ledger.state['phase'] not in ('submission_intent', 'awaiting_confirmation'):
                        raise Stop('There is no pending submission to confirm.')
                    confirmation = folder / ('confirmation-' + uuid.uuid4().hex + '.json')
                    write(confirmation, {'source': 'operator_confirmation', 'result': args.result, 'note': args.note,
                                         'at': datetime.now(timezone.utc).isoformat()})
                    ledger.checkpoint('completed' if args.result == 'ok' else 'needs_manual',
                                      'confirmed' if args.result == 'ok' else 'problem_reported',
                                      [str(confirmation.relative_to(ROOT))], user_confirmed=args.result == 'ok')
                elif args.command == 'attach':
                    browser.command('start')
                    snapshot = browser.menu(plan)
                    count = prefix_length(snapshot, plan)
                    ledger.state['handle'] = browser.command('observe')['handle']
                    ledger.save()
                    row, _ = ledger.current()
                    keep_phase = row['workflow_submission_reserved'] or row['status'] == 'completed'
                    ledger.checkpoint(row['status'] if keep_phase else 'checking',
                                      ledger.state['phase'] if keep_phase else 'attached', list(browser.evidence),
                                      attached_prefix_count=count)
                else:
                    browser.command('start')
                    if args.command == 'prepare' and not args.resume:
                        opened = browser.command('new-window', plan['composer_url'])
                        ledger.state['handle'] = opened['handle']
                        ledger.save()
                        browser.wait(lambda s: any(n.get('own_text') == '同步到公众号' for n in s['nodes']))
                    else:
                        if not ledger.state.get('handle'):
                            raise Stop('Window identity is missing. Observe and recover it manually.')
                        browser.command('switch-window', ledger.state['handle'])
                    if args.command == 'prepare':
                        row, _ = ledger.current()
                        if row['workflow_submission_reserved'] or ledger.state['phase'] in ('confirmed', 'problem_reported'):
                            raise Stop('This run has reached submission; inspect its result instead of recomposing.')
                        snapshot = assemble(browser, plan, lambda phase, index: ledger.checkpoint(
                            'checking', phase, list(browser.evidence), article_index=index))
                    else:
                        snapshot = browser.menu(plan)
                    _, attempts = ledger.current()
                    actual_plan = {**plan, 'prior_sync_attempts': attempts}
                    result = check(snapshot, actual_plan)
                    report = ROOT / 'artifacts' / ('sync-preflight-' + uuid.uuid4().hex + '.json')
                    write(report, result)
                    browser.evidence.append(str(report.relative_to(ROOT)))
                    if not result['composition_checks_passed']:
                        raise Stop('Composition preflight failed. Resolve the private report before continuing.')
                    if args.command == 'prepare':
                        ledger.checkpoint('ready_for_review', 'prepared', list(browser.evidence),
                                          composition_checks_passed=True, submission_review_allowed=attempts == 0)
                    elif args.command == 'submit':
                        button = one(snapshot['nodes'], lambda n: has_class(n, 'ok-btn') and
                                     n.get('own_text') == '开始同步', 'Start-sync button is ambiguous.')
                        reserve_submission(ledger, list(browser.evidence))
                        browser.click_text(button, '开始同步')
                        # Capture the immediate result once, but do not infer success from it.
                        browser.snapshot()
                        ledger.checkpoint('verification_pending', 'awaiting_confirmation', list(browser.evidence),
                                          sync_attempt_count=1, final_platform_result='unconfirmed', click_command_returned=True)
                return {'ok': True, 'phase': ledger.state['phase'], 'task_id': ledger.task_id,
                        'sync_attempt_count': ledger.current()[1], 'run': args.run}
        except (Stop, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            row, _ = ledger.current()
            if ledger.state['phase'] not in ('confirmed', 'problem_reported'):
                reserved = row['workflow_submission_reserved']
                ledger.checkpoint('verification_pending' if reserved else 'needs_manual',
                                  'awaiting_confirmation' if reserved else 'needs_manual', list(browser.evidence),
                                  final_platform_result='unconfirmed')
            raise
        finally:
            ledger.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('plan', type=Path); p.add_argument('--run', required=True)
    p.add_argument('--resume', action='store_true')
    p = sub.add_parser('submit'); p.add_argument('--run', required=True); p.add_argument('--execute', action='store_true')
    p = sub.add_parser('inspect'); p.add_argument('--run', required=True)
    p = sub.add_parser('attach'); p.add_argument('--run', required=True)
    p.add_argument('--current-window', action='store_true', required=True)
    p = sub.add_parser('confirm'); p.add_argument('--run', required=True)
    p.add_argument('--result', choices=('ok', 'problem'), required=True)
    p.add_argument('--note', default='')
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__,
                          'message': str(exc) if isinstance(exc, Stop) else 'Invalid input or execution failed; inspect private state.'},
                         ensure_ascii=False))
        sys.exit(2)
