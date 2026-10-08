"""Send a reviewed text alert to a configured WeCom group, with durable no-replay.

Default is dry-run. Webhook credentials and receipt records stay private.
An API acknowledgement means accepted, not that a human read the message.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def configured_webhook():
    value = os.environ.get('WECOM_WEBHOOK_URL', '').strip()
    if not value:
        path = ROOT / '.env'
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                key, separator, content = line.partition('=')
                if separator and key.strip() == 'WECOM_WEBHOOK_URL':
                    value = content.strip().strip('\"\'')
    parsed = urlsplit(value)
    query = parse_qs(parsed.query)
    if (parsed.scheme != 'https' or parsed.netloc != 'qyapi.weixin.qq.com'
            or parsed.path != '/cgi-bin/webhook/send' or parsed.fragment
            or set(query) != {'key'} or len(query['key']) != 1 or not query['key'][0].strip()):
        raise ValueError('Missing or invalid private WECOM_WEBHOOK_URL')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_once(webhook, message):
    body = json.dumps({'msgtype': 'text', 'text': {'content': message}}, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(webhook, data=body, headers={'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=15) as response:
        result = json.loads(response.read(65536).decode('utf-8'))
    if not isinstance(result, dict) or type(result.get('errcode')) is not int:
        raise ValueError('Invalid acknowledgement')
    return result['errcode']


def deliver(message, webhook, execute=False, sender=post_once):
    if not isinstance(message, str) or not message.strip() or len(message.encode('utf-8')) > 1800:
        raise ValueError('Alert must be nonempty and at most 1800 UTF-8 bytes; shorten the reviewed text')
    # Different run filenames do not allow the same group/message to be resent.
    key = hashlib.sha256((webhook + '\0' + message).encode('utf-8')).hexdigest()
    directory = ROOT / 'data' / 'wecom-notifications'
    receipt = directory / (key + '.json')
    if receipt.exists():
        record = json.loads(receipt.read_text(encoding='utf-8'))
        return {'status': record['status'], 'duplicate': True, 'receipt': str(receipt),
                'execute_requested': execute}
    if not execute:
        return {'status': 'dry_run', 'duplicate': False, 'execute_requested': False}
    directory.mkdir(parents=True, exist_ok=True)
    record = {'status': 'dispatch_reserved', 'created_at': datetime.now(timezone.utc).isoformat(),
              'message_sha256': hashlib.sha256(message.encode('utf-8')).hexdigest()}
    try:
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {'status': 'dispatch_reserved', 'duplicate': True, 'receipt': str(receipt),
                'execute_requested': True}
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        file.write(json.dumps(record, ensure_ascii=False, indent=2))
        file.flush()
        os.fsync(file.fileno())
    # A crash or uncertain reply leaves the reservation in place. Never auto-replay.
    try:
        error_code = sender(webhook, message)
        record['status'] = 'accepted' if error_code == 0 else 'rejected'
        record['api_error_code'] = error_code
    except Exception as exc:
        record['status'] = 'outcome_unknown'
        record['error_type'] = type(exc).__name__  # Exception strings can contain the secret URL.
    record['finished_at'] = datetime.now(timezone.utc).isoformat()
    temporary = receipt.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        file.write(json.dumps(record, ensure_ascii=False, indent=2))
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(receipt)
    return {'status': record['status'], 'duplicate': False, 'receipt': str(receipt),
            'execute_requested': True}


def split_message(message, limit=1800):
    """Deterministic lossless UTF-8 splitting, with context on every part."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError('Empty message')
    if len(message.encode('utf-8')) <= limit:
        return [message]
    heading = message.split('\n', 1)[0]
    # Keep date and execution identity visible when a part is viewed separately.
    lines = message.splitlines(keepends=True)
    if len(lines) > 1 and lines[1].startswith('运行：'):
        heading += '\n' + lines[1].rstrip('\n')
    budget = limit - len((heading + '\n分段 9999/9999\n').encode('utf-8'))
    if budget < 16:
        raise ValueError('Notification heading too large')
    chunks, current, size = [], [], 0
    for char in message:
        n = len(char.encode('utf-8'))
        if size + n > budget:
            chunks.append(''.join(current)); current, size = [], 0
        current.append(char); size += n
    if current:
        chunks.append(''.join(current))
    if len(chunks) > 9999:
        raise ValueError('Too many notification parts')
    return [heading + '\n分段 ' + str(i) + '/' + str(len(chunks)) + '\n' + chunk
            for i, chunk in enumerate(chunks, 1)]


def deliver_result(message, webhook, execute=False, sender=post_once):
    """Send all parts in order. Unknown/rejected part blocks the remaining batch."""
    parts = split_message(message)
    if len(parts) == 1:
        return deliver(parts[0], webhook, execute, sender)
    results = []
    for part in parts:
        result = deliver(part, webhook, execute, sender)
        results.append(result)
        if result['status'] not in ('accepted', 'dry_run'):
            break
    status = results[-1]['status']
    return {'status': status, 'duplicate': all(r['duplicate'] for r in results),
            'execute_requested': execute, 'parts_total': len(parts),
            'parts_processed': len(results), 'parts': results,
            'receipts': [r['receipt'] for r in results if r.get('receipt')]}


def private_file(path):
    path = path.resolve()
    if not any(path.is_relative_to((ROOT / folder).resolve()) for folder in ('local', 'artifacts', 'data')):
        raise ValueError('Input must be in a private project directory')
    return path


ISSUES = {
    'wps_login_required': 'WPS 登录异常，请重新登录预约表',
    'xiumi_login_required': '秀米登录异常，请重新登录常用收稿账号',
    'identity_match_reason_missing': '标题不同但同稿核验依据未记录',
    'manuscript_missing': '未收到稿件', 'row_not_checked': '尚未完成核对',
    'multiple_versions_unresolved': '同题多版本未确定',
    'identity_or_week_unresolved': '稿件身份或周次未确定',
    'same_manuscript_for_multiple_reservations': '同一稿件被匹配给多条预约',
    'headline_unresolved': '头条标记未确定',
    'reservation_remarks_or_order_unresolved': '预约备注或顺序存在待确认事项',
    'xiumi_unavailable_wrong_account_or_incomplete_search': '秀米不可用、账号未确认或搜索不完整',
    'xiumi_evidence_missing': '秀米核对证据缺失',
    'wrong_date_or_timezone': '观察日期或时区错误',
    'wrong_observation_date': '观察不是指定日期',
    'stale_or_future_observation': '观察时间过期或异常',
    'invalid_observation_time': '观察时间无效',
    'reservation_unreadable_or_incomplete': '预约表不可读或读取不完整',
    'reservation_date_not_confirmed': '预约日期未确认',
    'reservation_evidence_missing': '预约核对证据缺失',
    'historical_test_requires_past_date': '历史测试日期须早于执行日期',
}


def notification_heading(report):
    if report.get('historical_test') is True:
        heading = '未央宣传运营提醒｜历史测试｜预约 ' + report['date'] + '｜执行 ' + report['run_date']
    else:
        heading = '未央宣传运营提醒｜' + report['date']
    if report.get('run_id'):
        heading += '\n运行：' + str(report['run_id'])
    return heading


def title_warning_lines(report):
    warnings = report.get('warnings', [])
    if not warnings:
        return []
    lines = ['标题差异提醒（已核验为同一篇，不阻止后续处理）：']
    for warning in warnings:
        if warning.get('code') != 'title_difference_matched':
            raise ValueError('Unknown nonblocking warning')
        lines.extend(['- 预约：' + warning['reservation_title'],
                      '  秀米：' + warning['xiumi_title'],
                      '  依据：' + warning['reason']])
    lines.append('请人工留意以上标题差异，无需等待人工确认再继续。')
    return lines


def alert_from_report(report, observation):
    if (report.get('status') not in ('needs_attention', 'check_failed')
            or report.get('needs_attention') is not True or not report.get('issues')):
        raise ValueError('No actionable daily-check issues')
    if report.get('date') != observation.get('date'):
        raise ValueError('Report and observation dates differ')
    articles = observation.get('reservation', {}).get('articles', [])
    by_row = {a['row']: a for a in articles}
    heading = notification_heading(report)
    lines = [heading, '本轮检查有待处理事项：']
    for item in report['issues']:
        description = ISSUES.get(item['code'], '其他检查异常（请查看本机报告）')
        row = item.get('row')
        if row is not None:
            article = by_row.get(row, {})
            title = article.get('title', '未定位预约')
            unit = article.get('unit', '')
            lines.append('- ' + description + '：' + (unit + ' ' if unit else '') + title)
        else:
            lines.append('- ' + description)
    lines.append('本轮整组整理暂未开始，请确认以上问题。')
    lines.extend(title_warning_lines(report))
    return '\n'.join(lines)


def result_from_report(report, observation):
    if report.get('status') in ('needs_attention', 'check_failed'):
        return alert_from_report(report, observation)
    if (report.get('date') != observation.get('date') or report.get('needs_attention') is not False
            or report.get('issues') != []):
        raise ValueError('Invalid normal-result report')
    articles = observation.get('reservation', {}).get('articles')
    if not isinstance(articles, list):
        raise ValueError('Missing reservation articles')
    heading = notification_heading(report)
    if report.get('status') == 'no_reservations' and not articles:
        return heading + '\n本轮已完整检查预约表：无预约，无需整理稿件。'
    if report.get('status') == 'ready_to_format' and articles and report.get('ready_to_format') is True:
        return '\n'.join([heading, '预约 ' + str(len(articles)) + ' 篇，已收齐并完成稿件匹配。',
                          '基础整理待完成，尚未确认整理结果。'] + title_warning_lines(report))
    raise ValueError('Unknown or contradictory daily result')


def completion_from_report(report, observation, completion):
    """Render evidenced post-format/sync state, never infer success from receipt gate."""
    result_from_report(report, observation)  # Dates and normal-result consistency.
    if (report.get('status') != 'ready_to_format' or not report.get('run_id')
            or completion.get('date') != report['date'] or completion.get('run_id') != report['run_id']):
        raise ValueError('Completion must belong to this ready-to-format run')
    articles = {a['row']: a for a in observation['reservation']['articles']}
    order = report.get('ordered_rows', [])
    expected = [a['row'] for a in sorted(articles.values(), key=lambda a: (not a['is_headline'], a['row']))]
    if order != expected or sum(a['is_headline'] for a in articles.values()) != 1:
        raise ValueError('Incomplete or wrong article order')
    matches = {m['row']: m['candidates'] for m in observation['xiumi']['matches']}
    formats = completion.get('formatted_articles', [])
    if not isinstance(formats, list) or len(formats) != len(order):
        raise ValueError('Missing formatting results')
    by_row = {a['row']: a for a in formats}
    if set(by_row) != set(order):
        raise ValueError('Missing or duplicate formatting rows')

    def evidence(entry):
        paths = entry.get('evidence_paths')
        if (not isinstance(paths, list) or not paths
                or any(not isinstance(p, str) or not private_file(ROOT / p).is_file() for p in paths)):
            raise ValueError('Missing private completion evidence')

    draft_ids = []
    expected_warnings = []
    for row in order:
        candidates = matches[row]
        item = by_row[row]
        if (len(candidates) != 1 or candidates[0]['identity_confirmed'] is not True
                or item.get('source_id') != candidates[0]['id']
                or not isinstance(item.get('draft_id'), str) or not item['draft_id'].strip()
                or any(item.get(key) is not True for key in
                       ('saved_reopened', 'body_images_verified', 'format_verified'))):
            raise ValueError('Formatting not verified against the selected manuscript')
        evidence(item)
        draft_ids.append(item['draft_id'])
        candidate = candidates[0]
        if candidate['title'] != articles[row]['title']:
            reason = candidate.get('identity_reason')
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('Title difference requires a recorded identity reason')
            expected_warnings.append({'code': 'title_difference_matched', 'row': row,
                                      'reservation_title': articles[row]['title'],
                                      'xiumi_title': candidate['title'], 'draft_id': candidate['id'],
                                      'reason': reason})
    if sorted(report.get('warnings', []), key=lambda w: w['row']) != sorted(expected_warnings, key=lambda w: w['row']):
        raise ValueError('Report does not preserve actual title differences')
    if len(set(draft_ids)) != len(draft_ids):
        raise ValueError('Repeated formatted manuscript')
    sync = completion.get('sync', {})
    if (sync.get('ordered_draft_ids') != draft_ids or sync.get('date') != report['date']
            or sync.get('status') not in ('submitted', 'confirmed')):
        raise ValueError('Missing or inconsistent synchronization result')
    evidence(sync)
    if sync['status'] == 'confirmed':
        if sync.get('confirmed_by') not in ('user', 'platform'):
            raise ValueError('Synchronization requires an explicit confirmation source')
        outcome = '已校验基础格式并转存到微信公众号草稿箱。'
    elif sync.get('dispatch_result') == 'unknown':
        outcome = '已校验基础格式；转存尝试的结果未知，尚不能报告转存完成。'
    else:
        outcome = '已校验基础格式并发起转存；转存结果待确认，尚不能报告转存完成。'
    lines = [notification_heading(report), '今日预约 ' + str(len(order)) + ' 篇：']
    if report.get('historical_test') is True:
        lines[-1] = '历史测试预约 ' + str(len(order)) + ' 篇：'
    for i, row in enumerate(order, 1):
        article = articles[row]
        lines.append(str(i) + '、' + article['title'] + ('（头条）' if article['is_headline'] else ''))
    lines.append(outcome)
    lines.extend(title_warning_lines(report))
    if not report.get('warnings'):
        lines.append('秀米标题与预约标题均一致。')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--message-file', type=Path)
    group.add_argument('--report', type=Path)
    parser.add_argument('--observation', type=Path)
    parser.add_argument('--completion', type=Path,
                        help='Private evidenced formatting and sync result for this report/run')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.message_file:
        if args.completion:
            raise ValueError('Completion requires report and observation')
        message = private_file(args.message_file).read_text(encoding='utf-8').strip()
    else:
        if not args.observation:
            raise ValueError('Daily report requires its corresponding observation')
        report = json.loads(private_file(args.report).read_text(encoding='utf-8'))
        observation = json.loads(private_file(args.observation).read_text(encoding='utf-8'))
        if args.completion:
            completion = json.loads(private_file(args.completion).read_text(encoding='utf-8'))
            message = completion_from_report(report, observation, completion)
        else:
            message = result_from_report(report, observation)
    result = deliver_result(message, configured_webhook(), execute=args.execute)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('accepted', 'dry_run') else 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'ok': False, 'message': 'Notification not completed; check private config, input and receipts.'}))
        sys.exit(1)
