"""Validate rendered AirScript log frames; create a reservation observation only.

Browser actions remain with the agent. No tokens, network calls, draft matches,
formatting, or notifications are performed here.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re

SHANGHAI = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]


def utf16_length(text):
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def checksum(text):
    encoded = text.encode('utf-16-le', 'surrogatepass')
    value = 2166136261
    for i in range(0, len(encoded), 2):
        value = ((value ^ int.from_bytes(encoded[i:i+2], 'little')) * 16777619) & 0xffffffff
    return f'{value:08x}'


def decode_frames(lines, expected_run):
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError('logs must be an array of rendered log texts')
    frames = [line for line in lines if line.startswith(('WY_BEGIN ', 'WY_PART ', 'WY_END '))]
    if len(frames) < 3 or '执行完毕' not in lines:
        raise ValueError('execution not complete')
    match = re.fullmatch(r'WY_BEGIN (\S+) (\d+) (\d+) ([0-9a-f]{8})', frames[0])
    if not match or match[1] != expected_run:
        raise ValueError('wrong or missing run')
    run, count, length, digest = match.groups()
    count, length = int(count), int(length)
    if not 1 <= count <= 1000 or len(frames) != count + 2:
        raise ValueError('missing or duplicate frames')
    if frames[-1] != f'WY_END {run} {count} {length} {digest}':
        raise ValueError('end frame mismatch')
    chunks = []
    for i, frame in enumerate(frames[1:-1], 1):
        part = re.fullmatch(r'WY_PART (\S+) (\d+)/(\d+) ([\s\S]*)', frame)
        if not part or part[1] != run or int(part[2]) != i or int(part[3]) != count:
            raise ValueError('mixed, missing or reordered chunks')
        chunks.append(part[4])
    text = ''.join(chunks)
    if utf16_length(text) != length or checksum(text) != digest:
        raise ValueError('truncated or corrupted payload')
    # A split surrogate pair may be represented separately in a JSON log file.
    text = text.encode('utf-16-le', 'surrogatepass').decode('utf-16-le')
    payload = json.loads(text)
    if not isinstance(payload, dict) or payload.get('run_id') != run:
        raise ValueError('payload run mismatch')
    return payload


def reservation_from_payload(payload, expected_date, evidence_paths, now=None, instructions_reviewed=False):
    date.fromisoformat(expected_date)
    now = now or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        raise ValueError('now needs timezone')
    if payload.get('schema') != 'weiyang.reservation-day.v1' or payload.get('date') != expected_date:
        raise ValueError('wrong schema or business date')
    observed = datetime.fromisoformat(payload['generated_at'])
    if observed.tzinfo is None or not timedelta(0) <= now - observed <= timedelta(minutes=60):
        raise ValueError('stale or future output')
    base = {'date': expected_date, 'status': 'failed', 'complete': False,
            'conflicts_resolved': False, 'evidence_paths': evidence_paths,
            'articles': [], 'issues': []}
    if payload.get('ok') is not True:
        base['issues'].append(payload.get('error') or 'source_failed')
        return base
    matches = payload.get('matches')
    if payload.get('cols') != 11 or not isinstance(matches, list) or len(matches) != 1:
        raise ValueError('incomplete or ambiguous date block')
    block = matches[0]
    first, last = block['start_row'], block['end_row']
    rows = block['rows']
    if (type(first) is not int or type(last) is not int or first < 6 or last < first
            or [entry.get('row') for entry in rows] != list(range(first, last+1))):
        raise ValueError('date merge range incomplete')
    for entry in rows + block['headers']:
        if not isinstance(entry.get('cells'), list) or len(entry['cells']) != 11 or not all(isinstance(v, str) for v in entry['cells']):
            raise ValueError('column read incomplete')
    headers = block['headers']
    if ([entry['row'] for entry in headers] != [3, 4]
            or headers[0]['cells'][:4] != ['发布日期', '发布单位', '文章标题', '是否头条']
            or headers[0]['cells'][9] != '备注'
            or headers[1]['cells'][7:9] != ['宣传组意见', '是否发布']):
        raise ValueError('unsupported column layout')
    raw_date = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})(?:\s|$)', rows[0]['cells'][0].strip())
    if not raw_date or date(*map(int, raw_date.groups())).isoformat() != expected_date:
        raise ValueError('date anchor mismatch')
    if not isinstance(block.get('instructions'), str) or not block['instructions'].strip():
        raise ValueError('instructions missing')
    if instructions_reviewed is not True:
        base['issues'].append('instructions_need_review')
    scanned = payload.get('sheets_scanned')
    if not isinstance(scanned, list) or not scanned:
        raise ValueError('sheet scan evidence missing')
    anchors = [(s['sheet'], d['start_row'], d['end_row']) for s in scanned for d in s['dates'] if d['date'] == expected_date]
    if anchors != [(block['sheet'], first, last)]:
        raise ValueError('date scan inconsistent')
    for entry in rows:
        cells = entry['cells']
        if entry['row'] > first and cells[0].strip():
            raise ValueError('merged interior date not blank')
        if not any(value.strip() for value in cells[1:10]):
            continue  # Duty K and date A alone are not an article reservation.
        if not cells[1].strip() or not cells[2].strip():
            base['issues'].append({'code': 'partial_reservation', 'row': entry['row']})
        marker = cells[3].strip()
        if marker not in ('', '是', '否'):
            base['issues'].append({'code': 'unknown_headline', 'row': entry['row']})
        if cells[9].strip():
            base['issues'].append({'code': 'remarks_need_review', 'row': entry['row']})
        base['articles'].append({'row': entry['row'], 'unit': cells[1], 'title': cells[2],
                                 'is_headline': marker == '是', 'remarks': cells[9],
                                 'review_and_publish': cells[4:9]})
    if base['articles'] and sum(a['is_headline'] for a in base['articles']) != 1:
        base['issues'].append('headline_unresolved')
    base.update(status='ok', complete=True, sheet=block['sheet'],
                merge_range=[first, last], instructions=block['instructions'])
    if any(isinstance(issue, dict) and issue['code'] == 'partial_reservation' for issue in base['issues']):
        base.update(status='failed', complete=False)
    base['conflicts_resolved'] = not base['issues']
    base['ordered_rows'] = ([a['row'] for a in base['articles'] if a['is_headline']]
                            + [a['row'] for a in base['articles'] if not a['is_headline']]
                            if base['conflicts_resolved'] else [])
    return base


def private_path(value):
    path = (ROOT / value).resolve()
    if not any(path.is_relative_to(ROOT / folder) for folder in ('local', 'artifacts', 'data')):
        raise ValueError('real input/output must stay in private project folders')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', help='JSON array of current rendered div.concent texts')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--instructions-reviewed', action='store_true',
                        help='agent has read current instructions and resolved their sorting requirements')
    args = parser.parse_args()
    source, output = private_path(args.logs), private_path(args.output)
    payload = decode_frames(json.loads(source.read_text(encoding='utf-8')), args.run_id)
    reservation = reservation_from_payload(payload, args.date, [str(source.relative_to(ROOT))],
                                           instructions_reviewed=args.instructions_reviewed)
    observation = {'date': args.date, 'timezone': 'Asia/Shanghai',
                   'observed_at': payload['generated_at'], 'run_id': payload['run_id'],
                   'reservation': reservation}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as file:
        json.dump(observation, file, ensure_ascii=False, indent=2)
    print(json.dumps({'status': reservation['status'], 'count': len(reservation['articles']),
                      'conflicts_resolved': reservation['conflicts_resolved'],
                      'output': str(output.relative_to(ROOT))}))
    return 0 if reservation['status'] == 'ok' else 2


if __name__ == '__main__':
    raise SystemExit(main())
