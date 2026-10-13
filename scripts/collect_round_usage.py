"""Close one round's usage loop: export the real DSH calls, then import them.

docs/run-logging.md requires the measurement window to be established before the model
starts, and requires exact per-call records rather than session cumulative counters.
Closing that loop used to be three manual commands with a hand-copied window. This runs
the same two scripts from one set of arguments and reports the merged result, so the
three-day trial can account every round the same way.

The private session file and the raw export are never packaged or printed beyond the
token fields they already carry.
"""
import argparse
from datetime import datetime
from pathlib import Path
import json
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]

try:
    from . import check_runtime
    from .check_runtime import private_path
except ImportError:
    import check_runtime
    from check_runtime import private_path


def project_root():
    """One root for the private paths, the called scripts and the reported location.

    Resolving it at call time keeps the export path, the import target and the reported
    relative path consistent even when the root is redirected.
    """
    return check_runtime.ROOT


def stamp(value):
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError('Measurement window needs an explicit timezone')
    return moment


def session_path(value):
    """The raw session log is private evidence; never require it to live in the repository."""
    path = Path(value).resolve()
    if not path.exists():
        raise ValueError('Session log does not exist')
    root = project_root()
    if path.is_relative_to(root) and not path.is_relative_to(root / 'local'):
        raise ValueError('A session log inside the repository must be under local/')
    return path


def call(command):
    process = subprocess.run([sys.executable, *[str(part) for part in command]],
                             cwd=project_root(), capture_output=True, text=True, encoding='utf-8')
    return process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--turn', type=int, action='append', required=True)
    parser.add_argument('--output')
    parser.add_argument('--export-only',action='store_true',help='Measure the actual agent window without modifying a later-started business RunLog')
    args = parser.parse_args()

    start, end = stamp(args.start), stamp(args.end)
    if end <= start:
        raise ValueError('Measurement window must end after it starts')
    session = session_path(args.session)
    root = project_root()
    output = private_path(args.output or
                          'local/round-usage/%s-%s.json' % (args.run, uuid.uuid4().hex))

    exported = call([root / 'scripts/dsh_usage_export.py',
                     '--session', session, '--start', args.start, '--end', args.end,
                     *sum([['--turn', turn] for turn in args.turn], []),
                     '--output', output])
    if exported.returncode:
        raise RuntimeError('Usage export failed: ' + (exported.stderr or exported.stdout).strip())

    metadata = json.loads(output.with_suffix('.meta.json').read_text(encoding='utf-8'))
    summary={'status':metadata['status'],'requests':metadata['requests'],
             'turns':metadata['turns'],'window_start':metadata['window_start'],
             'window_end':metadata['window_end'],'path':str(output.relative_to(root))}
    if args.export_only:
        return {'ok':True,'export':summary,'import':{'status':'not_requested',
                'reason':'independent_agent_measurement; business log timestamps unchanged'}},0
    imported = call([root / 'scripts/run_log.py', 'usage',
                     '--run', args.run, '--date', args.date, '--file', output])
    if imported.returncode:
        raise RuntimeError('Usage import failed: ' + (imported.stderr or imported.stdout).strip())
    return {'ok':True,'export':summary,'import':json.loads(imported.stdout)},0



if __name__ == '__main__':
    try:
        result, code = main()
    except (OSError, ValueError, RuntimeError) as exc:
        result, code = {'ok': False, 'error': 'usage_collection_failed', 'message': str(exc)}, 1
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(code)
