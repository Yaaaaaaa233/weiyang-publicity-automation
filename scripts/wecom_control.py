"""Authorized command inbox and desktop-agent dispatch journal.

Only authenticated SDK message envelopes should be passed here in a live listener.
The offline CLI is for local replay tests, not a public endpoint.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = timezone(timedelta(hours=8))
COMMANDS = {"连接测试": "connection_test", "补跑今天": "rerun_today", "查询进度": "status"}


def config_signature(config):
    names = ('mode', 'purpose', 'bot_id', 'allowed_user_ids', 'allowed_chat_ids',
             'desktop_session_id', 'business_executor_enabled')
    value = {name: config.get(name) for name in names}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def connect(database):
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, date TEXT, "
               "observed_at TEXT, command TEXT, status TEXT)")
    columns = {r[1] for r in db.execute('PRAGMA table_info(requests)')}
    if 'mode' not in columns:
        db.execute("ALTER TABLE requests ADD COLUMN mode TEXT NOT NULL DEFAULT 'queue_only'")
    if 'config_sha256' not in columns:
        db.execute("ALTER TABLE requests ADD COLUMN config_sha256 TEXT NOT NULL DEFAULT ''")
    if 'origin' not in columns:
        db.execute("ALTER TABLE requests ADD COLUMN origin TEXT NOT NULL DEFAULT 'wecom'")
    db.execute("CREATE TABLE IF NOT EXISTS dispatches (key TEXT PRIMARY KEY, run_id TEXT, "
               "session_id TEXT, message_id TEXT, updated_at TEXT, detail TEXT)")
    db.commit()
    return db


def enqueue_bridge_test(config, database, now=None):
    """Local operator-only probe; never impersonate an incoming WeCom event.

    This command can test the desktop leg before a real bot exists. It cannot
    dispatch business, and its origin remains explicit in the private journal.
    """
    if (config.get('mode') != 'desktop' or config.get('purpose') != 'bridge_test' or
            config.get('business_executor_enabled') is not False or not config.get('desktop_session_id')):
        raise ValueError('Local probe requires an explicitly bound, business-disabled bridge_test configuration')
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(SHANGHAI).date().isoformat()
    key = hashlib.sha256(('local-bridge-probe-' + uuid.uuid4().hex).encode()).hexdigest()
    with closing(connect(database)) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT key FROM requests WHERE status IN ('queued','dispatch_reserved','running','outcome_unknown')").fetchone():
            db.rollback()
            return {'status': 'busy'}
        db.execute("INSERT INTO requests (key,date,observed_at,command,status,mode,config_sha256,origin) "
                   "VALUES (?,?,?,?,?,?,?,?)", (key, day, now.isoformat(), 'rerun_today', 'queued',
                   'desktop', config_signature(config), 'local_bridge_test'))
        db.commit()
    return {'status': 'queued', 'origin': 'local_bridge_test', 'request_id': key,
            'note': 'Only a local desktop-leg test; no WeCom event was received and no business is authorized.'}


def claim(config, database, now=None):
    """Reserve before touching DSH. Reserved/unknown work is never auto-replayed."""
    if config.get('mode') != 'desktop':
        return {'status': 'disabled'}
    if not config.get('desktop_session_id') or config.get('purpose') not in ('bridge_test', 'business'):
        return {'status': 'configuration_required'}
    if config['purpose'] == 'business' and config.get('business_executor_enabled') is not True:
        return {'status': 'business_executor_disabled'}
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(SHANGHAI).date().isoformat()
    with closing(connect(database)) as db:
        db.execute('BEGIN IMMEDIATE')
        busy = db.execute("SELECT key FROM requests WHERE status IN ('dispatch_reserved','running','outcome_unknown')").fetchone()
        if busy:
            db.rollback()
            return {'status': 'busy'}
        row = db.execute("SELECT * FROM requests WHERE status='queued' AND mode='desktop' ORDER BY observed_at,key LIMIT 1").fetchone()
        if not row:
            db.rollback()
            return {'status': 'empty'}
        if row['date'] != day:
            db.execute("UPDATE requests SET status='expired' WHERE key=?", (row['key'],))
            db.commit()
            return {'status': 'expired'}
        if row['config_sha256'] != config_signature(config):
            db.execute("UPDATE requests SET status='needs_attention' WHERE key=?", (row['key'],))
            db.commit()
            return {'status': 'configuration_changed'}
        run = 'manual-' + day.replace('-', '') + '-' + row['key'][:16]
        db.execute("INSERT INTO dispatches VALUES (?,?,?,?,?,?)", (row['key'], run,
                   config['desktop_session_id'], None, now.isoformat(), '{}'))
        db.execute("UPDATE requests SET status='dispatch_reserved' WHERE key=?", (row['key'],))
        db.commit()
        return {'status': 'dispatch_reserved', 'request_id': row['key'], 'run_id': run,
                'date': day, 'session_id': config['desktop_session_id'],
                'purpose': config['purpose'], 'observed_at': row['observed_at'], 'origin': row['origin']}


def mark(database, key, status, detail=None):
    """Compare-and-set lifecycle; final or unknown dispatches cannot be reclaimed."""
    allowed = {'dispatch_reserved': {'running', 'outcome_unknown', 'needs_attention'},
               'running': {'bridge_verified', 'completed', 'no_reservations', 'needs_attention', 'outcome_unknown'}}
    detail = detail or {}
    with closing(connect(database)) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
        if not row or status not in allowed.get(row['status'], set()):
            raise ValueError('Invalid dispatch transition; observe existing state instead of replaying')
        db.execute('UPDATE requests SET status=? WHERE key=?', (status, key))
        db.execute('UPDATE dispatches SET message_id=COALESCE(?,message_id),updated_at=?,detail=? WHERE key=?',
                   (detail.get('message_id'), datetime.now(timezone.utc).isoformat(), json.dumps(detail), key))
        db.commit()
    return {'status': status}


def resolve_probe_failure(config, database, evidence, now=None):
    """Operator-only closure of a verified v4 rejection, never business retry permission."""
    now = now or datetime.now(timezone.utc)
    if config.get('purpose') != 'bridge_test' or config.get('business_executor_enabled') is not False:
        raise ValueError('Only a business-disabled local probe can be resolved')
    seen = datetime.fromisoformat(evidence['observed_at'])
    if seen.tzinfo is None or not 0 <= (now-seen).total_seconds() <= 300:
        raise ValueError('Fresh observation required')
    if (evidence.get('error') != 'format v4 message requires a producer-owned source kind' or
            evidence.get('model_execution_started') is not False or
            evidence.get('session_id') != config.get('desktop_session_id')):
        raise ValueError('Exact pre-model rejection evidence required')
    with closing(connect(database)) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT r.*,d.run_id,d.session_id,d.message_id,d.detail FROM requests r '
                         'JOIN dispatches d ON r.key=d.key WHERE r.key=?', (evidence['request_id'],)).fetchone()
        if (not row or row['origin'] != 'local_bridge_test' or row['status'] != 'outcome_unknown' or
                row['run_id'] != evidence.get('run_id') or row['session_id'] != evidence['session_id'] or
                row['message_id'] is not None or row['config_sha256'] != config_signature(config) or
                json.loads(row['detail']).get('phase') != 'desktop_dispatch_unavailable'):
            raise ValueError('Probe state does not match observed rejection; do not replay')
        detail = {'phase':'verified_pre_model_rejection', 'previous_detail':json.loads(row['detail']),
                  'operator_evidence':evidence, 'replay_authorized':False}
        db.execute("UPDATE requests SET status='needs_attention' WHERE key=?", (row['key'],))
        db.execute('UPDATE dispatches SET updated_at=?,detail=? WHERE key=?',
                   (now.isoformat(),json.dumps(detail),row['key']))
        db.commit()
    return {'status':'needs_attention','reason':'verified_pre_model_rejection','replayed':False}


def latest(database):
    with closing(connect(database)) as db:
        row = db.execute("SELECT r.status,r.date,d.run_id FROM requests r LEFT JOIN dispatches d "
                         "ON r.key=d.key WHERE r.command='rerun_today' AND r.status!='busy' ORDER BY r.observed_at DESC,r.rowid DESC LIMIT 1").fetchone()
        return dict(row) if row else {'status': 'empty'}


def private(path):
    result = (ROOT / path).resolve()
    if not any(result.is_relative_to(ROOT / name) for name in ("local", "data")):
        raise ValueError("Control configuration and state must be private")
    return result


def handle(config, body, database, now=None):
    """Authorize, parse an exact command, then durably deduplicate and enqueue.

    A queued request is not proof an agent started or completed a business execution.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Use an actual timezone-aware observation time")
    if config.get("mode") not in ('queue_only', 'desktop'):
        raise ValueError('Unknown control mode')
    users, groups = config.get("allowed_user_ids", []), config.get("allowed_chat_ids", [])
    if (not isinstance(users, list) or not isinstance(groups, list) or not users or not groups or
            not all(isinstance(v, str) and v for v in users+groups) or
            not isinstance(config.get('bot_id'), str) or not config['bot_id']):
        return {"status": "configuration_required"}
    if not isinstance(body, dict) or not isinstance(body.get('from'), dict) or not isinstance(body.get('text'), dict):
        return {'status': 'ignored'}
    if (body.get("aibotid") != config["bot_id"] or body.get("chattype") != "group" or
            body.get("chatid") not in groups or body.get("from", {}).get("userid") not in users):
        return {"status": "ignored"}
    if body.get("msgtype") != "text" or not isinstance(body.get("msgid"), str) or not body["msgid"]:
        return {"status": "ignored"}
    content = body.get("text", {}).get("content")
    if not isinstance(content, str) or len(content) > 128:
        return {"status": "ignored"}
    # An @ prefix is removed only if it matches the configured exact display name.
    text = content.strip()
    mention = "@" + config.get("bot_name", "")
    if config.get("bot_name") and text.startswith(mention) and text[len(mention):len(mention)+1].isspace():
        text = text[len(mention):].strip()
    command = COMMANDS.get(text)
    if not command:
        return {"status": "ignored"}
    created = body.get("create_time")
    if created is not None and (not isinstance(created, (int, float)) or
                               not 0 <= now.timestamp() - created <= 300):
        return {"status": "stale_message", "reply": "触发消息已过期，请重新发送指令。"}
    key = hashlib.sha256((body["aibotid"] + "\0" + body["msgid"]).encode()).hexdigest()
    day = now.astimezone(SHANGHAI).date().isoformat()
    with closing(connect(database)) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT status FROM requests WHERE key=?", (key,)).fetchone()
        if old:
            db.rollback()
            return {"status": "duplicate", "request_id": key[:16]}
        busy = db.execute("SELECT key FROM requests WHERE command='rerun_today' AND status IN ('queued','dispatch_reserved','running','outcome_unknown')").fetchone()
        status = "busy" if command == "rerun_today" and busy else "queued" if command == "rerun_today" else "received"
        db.execute("INSERT INTO requests (key,date,observed_at,command,status,mode,config_sha256) VALUES (?,?,?,?,?,?,?)",
                   (key, day, now.isoformat(), command, status, config['mode'], config_signature(config)))
        db.commit()
    replies = {
        "busy": "测试队列已有补跑请求；本条未另建任务，尚未执行真实业务。",
        "queued": "连接测试：今天的补跑请求已记入测试队列，尚未执行真实业务。",
        "received": "连接测试成功，尚未执行真实业务。" if command == "connection_test" else
                    ("测试队列有待处理请求，尚未执行真实业务。" if busy else "测试队列无待处理请求；尚未接入业务执行器。"),
    }
    if config['mode'] == 'desktop':
        if status == 'queued':
            replies[status] = ('已收到补跑请求，等待 DSH 专用会话接收；尚未开始业务。' if config.get('purpose') == 'business'
                               else '已收到桌面接续测试请求；本次只测试 agent 连接，不操作稿件或转存。')
        elif status == 'busy':
            replies[status] = '已有待处理或结果未明的任务，本条未重复启动。请使用“查询进度”。'
        elif command == 'status':
            state = latest(database)
            names = {'empty': '无请求', 'queued': '排队中，尚未执行', 'dispatch_reserved': '投递已预留，等待回执',
                     'running': 'DSH 已接收，完成情况待核对', 'outcome_unknown': '投递或执行结果未知，未自动重发',
                     'needs_attention': '执行受阻，请查看私有报告', 'bridge_verified': 'agent 接续测试通过，未执行业务',
                     'completed': '业务状态及结果通知已核对完成', 'no_reservations': '已确认无预约并发送通知',
                     'expired': '请求已跨日过期，未补跑'}
            replies[status] = '最近请求：' + names.get(state['status'], '状态待核对') + '。'
    return {"status": status, "request_id": key[:16], "date": day, "command": command, "reply": replies[status]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--database", default="local/wecom-control-test/requests.sqlite")
    parser.add_argument('--op', choices=('receive','claim','mark','latest','enqueue-test','resolve-probe-failure'), default='receive')
    args = parser.parse_args()
    config = json.loads(private(args.config).read_text(encoding="utf-8"))
    database = private(args.database)
    if args.op == 'enqueue-test': result = enqueue_bridge_test(config, database)
    elif args.op == 'resolve-probe-failure': result = resolve_probe_failure(config, database, json.load(sys.stdin))
    elif args.op == 'claim': result = claim(config, database)
    elif args.op == 'latest': result = latest(database)
    elif args.op == 'mark':
        body = json.load(sys.stdin)
        result = mark(database, body['request_id'], body['status'], body.get('detail'))
    else: result = handle(config, json.load(sys.stdin), database)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
