"""Portable local task ledger. JSON details stay in ignored artifacts, not stdout.

Ownership coordinates ledger writes; browser tools do not yet enforce task leases.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATES = ('received', 'checking', 'needs_manual', 'verification_pending', 'ready_for_review', 'completed', 'failed')


def connect(path):
    if path.suffix != '.sqlite':
        raise ValueError('Task database filename must end in .sqlite.')
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
        step TEXT NOT NULL, checkpoint TEXT NOT NULL, version INTEGER NOT NULL,
        owner TEXT, expires REAL, updated REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS events (
        task_id TEXT NOT NULL REFERENCES tasks(id), seq INTEGER NOT NULL,
        kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL,
        PRIMARY KEY(task_id, seq));
    ''')
    return db


def task(db, task_id):
    row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        raise ValueError('Task not found.')
    return dict(row)


def owned(row, owner):
    if row['owner'] != owner or (row['expires'] or 0) <= time.time():
        raise RuntimeError('Task has no current lease for this owner. Reobserve and claim first.')


def event(db, row, kind, data):
    db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
               (row['id'], row['version'], kind, json.dumps(data, ensure_ascii=False), time.time()))


def evidence(paths):
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ValueError('evidence_paths must be a list of relative paths.')
    hashes = {}
    for path in paths:
        p = Path(path)
        if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] not in ('artifacts', 'local', 'data'):
            raise ValueError('Evidence must use project-relative runtime paths.')
        target = (ROOT / p).resolve()
        if not target.is_relative_to(ROOT) or not target.is_file():
            raise ValueError('Referenced evidence file is missing or outside the project.')
        digest = hashlib.sha256()
        with target.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        hashes[path] = digest.hexdigest()
    return hashes


def verify_evidence(checkpoint):
    actual = evidence(checkpoint.get('evidence_paths', []))
    expected = checkpoint.get('evidence_sha256')
    if actual and expected is None:
        raise ValueError('Checkpoint is missing evidence hashes. Reverify before handing off.')
    if expected is not None and expected != actual:
        raise ValueError('Evidence changed since checkpoint. Reverify and record a new checkpoint.')


def read_object(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Input JSON must be an object.')
    return value


def write_handoff(db, row, filename, export):
    if Path(filename).name != filename or not filename.endswith('.json'):
        raise ValueError('Use a plain filename ending in .json.')
    if export and row['owner'] and (row['expires'] or 0) > time.time():
        raise RuntimeError('Release the task lease before exporting a handoff.')
    history = [dict(r) for r in db.execute('SELECT * FROM events WHERE task_id=? ORDER BY seq', (row['id'],))]
    for item in history:
        item['data'] = json.loads(item['data'])
    checkpoint = json.loads(row['checkpoint'])
    verify_evidence(checkpoint)
    record = dict(row)
    record['source'] = json.loads(record['source'])
    record['checkpoint'] = checkpoint
    if export:
        record['owner'] = record['expires'] = None
    target = ROOT / 'artifacts' / filename
    target.parent.mkdir(exist_ok=True)
    payload = {'format': 1, 'kind': 'handoff' if export else 'observation',
               'task': record, 'events': history,
               'notice': 'Evidence files must accompany this record. Reobserve platform state before acting.'}
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    return str(target)


def summary(row):
    return {key: row[key] for key in ('id', 'status', 'step', 'version')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'tasks.sqlite')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('create'); p.add_argument('source', type=Path)
    sub.add_parser('list')
    p = sub.add_parser('claim'); p.add_argument('id'); p.add_argument('owner'); p.add_argument('--ttl', type=int, default=900)
    p = sub.add_parser('checkpoint'); p.add_argument('id'); p.add_argument('owner'); p.add_argument('file', type=Path); p.add_argument('--version', type=int, required=True)
    p = sub.add_parser('release'); p.add_argument('id'); p.add_argument('owner')
    for name in ('show', 'export'):
        p = sub.add_parser(name); p.add_argument('id'); p.add_argument('filename')
    p = sub.add_parser('import'); p.add_argument('file', type=Path)
    args = parser.parse_args()
    db = connect(args.db)
    try:
        db.execute('BEGIN IMMEDIATE')
        if args.command == 'list':
            result = {'tasks': [summary(dict(r)) for r in db.execute('SELECT * FROM tasks ORDER BY updated DESC')]}
        elif args.command == 'create':
            source = read_object(args.source)
            task_id = uuid.uuid4().hex
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                       (task_id, json.dumps(source, ensure_ascii=False), 'received', 'received', '{}', 1, None, None, time.time()))
            row = task(db, task_id); event(db, row, 'created', {})
            result = summary(row)
        elif args.command == 'import':
            payload = read_object(args.file)
            if payload.get('format') != 1 or payload.get('kind') != 'handoff':
                raise ValueError('Only a released format-1 handoff can be imported.')
            row = payload['task']
            uuid.UUID(hex=row['id'])
            if row.get('owner') is not None or row.get('expires') is not None:
                raise ValueError('Imported task cannot carry an active lease.')
            if row['status'] not in STATES or type(row['version']) is not int or row['version'] < 1:
                raise ValueError('Invalid imported status or version.')
            history = payload.get('events')
            if not isinstance(row['source'], dict) or not isinstance(row['checkpoint'], dict) or not isinstance(row['step'], str):
                raise ValueError('Invalid imported task metadata.')
            if not isinstance(history, list) or row['version'] > 10000 or len(history) != row['version']:
                raise ValueError('Imported history must contain every task version.')
            for seq, item in enumerate(history, 1):
                if not isinstance(item, dict) or item.get('seq') != seq or item.get('task_id') != row['id'] or not isinstance(item.get('data'), dict):
                    raise ValueError('Invalid imported event sequence.')
            verify_evidence(row['checkpoint'])
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',
                       (row['id'], json.dumps(row['source'], ensure_ascii=False), row['status'], row['step'],
                        json.dumps(row['checkpoint'], ensure_ascii=False), row['version'], None, None, row['updated']))
            for item in history:
                if item['task_id'] != row['id']:
                    raise ValueError('Event belongs to another task.')
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                           (row['id'], item['seq'], item['kind'], json.dumps(item['data'], ensure_ascii=False), item['created']))
            result = summary(task(db, row['id']))
        else:
            row = task(db, args.id)
            if args.command in ('show', 'export'):
                result = {**summary(row), 'file': write_handoff(db, row, args.filename, args.command == 'export')}
            else:
                if args.command == 'claim':
                    if not args.owner.strip() or not 60 <= args.ttl <= 3600:
                        raise ValueError('Use a nonempty owner and ttl between 60 and 3600 seconds.')
                    other = db.execute('SELECT id FROM tasks WHERE owner IS NOT NULL AND owner != ? AND expires > ? LIMIT 1', (args.owner, time.time())).fetchone()
                    if other:
                        raise RuntimeError('Another executor has an active lease. Stop and hand off first.')
                    db.execute('UPDATE tasks SET owner=?, expires=?, version=version+1, updated=? WHERE id=?',
                               (args.owner, time.time() + args.ttl, time.time(), args.id))
                    data = {'owner': args.owner, 'ttl': args.ttl}
                elif args.command == 'release':
                    owned(row, args.owner)
                    db.execute('UPDATE tasks SET owner=NULL, expires=NULL, version=version+1, updated=? WHERE id=?', (time.time(), args.id))
                    data = {'owner': args.owner}
                else:
                    owned(row, args.owner)
                    if args.version != row['version']:
                        raise RuntimeError('Task version changed. Read current evidence before updating.')
                    data = read_object(args.file)
                    if data.get('status') not in STATES or not isinstance(data.get('step'), str) or not isinstance(data.get('next_step'), str):
                        raise ValueError('Checkpoint needs status, step and next_step.')
                    data['evidence_sha256'] = evidence(data.get('evidence_paths', []))
                    db.execute('UPDATE tasks SET status=?, step=?, checkpoint=?, version=version+1, updated=? WHERE id=?',
                               (data['status'], data['step'], json.dumps(data, ensure_ascii=False), time.time(), args.id))
                row = task(db, args.id)
                event(db, row, args.command, data)
                result = summary(row)
        db.execute('COMMIT')
        return result
    except Exception:
        db.execute('ROLLBACK')
        raise
    finally:
        db.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        print(json.dumps({'ok': True, 'result': main()}, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False))
        sys.exit(1)
