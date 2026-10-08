"""Export only scoped usage from DSH v4 session logs; never export message content."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def stamp(ms):
    if type(ms) is not int or ms < 0:
        raise ValueError('Invalid epoch milliseconds')
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def export(events, start, end, turns):
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('Explicit timezone-aware execution window required')
    session = None
    starts = {}
    records = {}
    unknown = []
    sequences = {}
    ended_turns = set()
    for event in events:
        kind = event.get('type')
        if kind == 'session':
            if session is not None and session != event.get('id'):
                raise ValueError('Mixed sessions')
            session = event.get('id')
            continue
        seq = event.get('seq')
        if type(seq) is not int:
            raise ValueError('Missing persisted sequence')
        # Hash only for duplicate/conflict detection; content is never output.
        fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        if seq in sequences:
            if sequences[seq] != fingerprint:
                raise ValueError('Conflicting persisted sequence')
            continue
        sequences[seq] = fingerprint
        data = event.get('data', {})
        key = (data.get('turn'), data.get('step'))
        if kind == 'turn/end' and datetime.fromisoformat(stamp(event['time'])) <= end:
            ended_turns.add(data.get('turn'))
        if kind == 'step/start':
            if key in starts:
                raise ValueError('Ambiguous step start')
            starts[key] = event['time']
        if 'usage' not in data:
            continue
        finished = datetime.fromisoformat(stamp(event['time']))
        if not start <= finished <= end:
            continue
        if kind != 'assistant/message':
            unknown.append({'seq': seq, 'kind': kind, 'reason': 'usage_not_attributed_to_model_step'})
            continue
        if data.get('turn') not in turns:
            continue
        if not session or key not in starts:
            raise ValueError('Missing session or exact step start')
        began = datetime.fromisoformat(stamp(starts[key]))
        if began < start or began > finished:
            raise ValueError('Call crosses execution boundary')
        message = data.get('message', {})
        identity = message.get('id')
        model = message.get('source', {}).get('model')
        if not isinstance(identity, str) or not identity or not isinstance(model, str) or not model:
            raise ValueError('Missing persisted message identity or actual model')
        usage = data['usage']
        fields = {'input_tokens': 'inputTokens', 'cache_read_tokens': 'cacheReadTokens',
                  'output_tokens': 'outputTokens', 'cache_write_tokens': 'cacheWriteTokens'}
        tokens = {target: usage.get(source) for target, source in fields.items()}
        if any(v is not None and (type(v) is not int or v < 0) for v in tokens.values()):
            raise ValueError('Invalid per-call usage')
        if all(tokens[k] is not None for k in ('input_tokens', 'cache_read_tokens', 'output_tokens')):
            if usage.get('totalTokens') != sum(tokens[k] for k in ('input_tokens', 'cache_read_tokens', 'output_tokens')):
                raise ValueError('Observed DSH token identity changed')
        # Local persisted call identity, not a claimed provider HTTP request ID.
        call_id = 'dsh-' + hashlib.sha256((session + '\0' + identity).encode()).hexdigest()
        record = {'request_id': call_id, 'model': model, 'source': 'dsh',
                  'started_at': began.isoformat(), 'finished_at': finished.isoformat(), **tokens}
        if call_id in records and records[call_id] != record:
            raise ValueError('Conflicting call usage')
        records[call_id] = record
    values = list(records.values())
    metadata = {'schema_version': 1, 'source_format': 'DSH session v4',
                'identity_kind': 'hash_of_persisted_session_and_message_id',
                'provider_request_id_available': False, 'window_start': start.isoformat(),
                'window_end': end.isoformat(), 'turns': sorted(turns), 'requests': len(values),
                'closed_turns': sorted(turns & ended_turns),
                'status': 'partial' if unknown or turns - ended_turns or any(v is None for r in values for k,v in r.items() if k.endswith('_tokens')) else 'measured',
                'unattributed_usage': unknown}
    if not values: metadata['status'] = 'unknown'
    return values, metadata


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--start', type=datetime.fromisoformat, required=True)
    p.add_argument('--end', type=datetime.fromisoformat, required=True)
    p.add_argument('--turn', type=int, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    raw = args.session.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if args.session.suffix == '.zstd':
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
            raw = reader.read()
    records, metadata = export((json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()),
                               args.start, args.end, set(args.turn))
    metadata['source_sha256'] = digest
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive writes: a rerun must inspect its existing export, never silently replace it.
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    with args.output.with_suffix('.meta.json').open('x', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(json.dumps({'status':metadata['status'], 'requests':len(records), 'unattributed':len(metadata['unattributed_usage'])}))


if __name__ == '__main__':
    main()
