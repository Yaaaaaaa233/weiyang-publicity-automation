"""Read-only daily receipt gate; consumes an agent's fresh, evidenced observations.

Does not read a sheet, operate Xiumi, format articles, or deliver notifications.
Real observations and reports belong in ignored local/ and artifacts/ directories.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = timezone(timedelta(hours=8))


def evaluate(observation, expected_date, now=None, historical_test=False):
    now = now or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        raise ValueError('now needs an offset')
    date.fromisoformat(expected_date)
    if not isinstance(observation, dict):
        raise ValueError('observation must be an object')
    issues = []
    warnings = []

    def issue(code, row=None):
        item = {'code': code}
        if row is not None:
            item['row'] = row
        issues.append(item)

    def report(status, rows=None):
        return {'date': expected_date, 'status': status,
                'run_id': observation.get('run_id') or observation.get('observed_at'),
                'run_date': now.astimezone(SHANGHAI).date().isoformat(),
                'historical_test': historical_test,
                'ready_to_format': status == 'ready_to_format',
                'needs_attention': status in ('check_failed', 'needs_attention'),
                'ordered_rows': rows or [], 'issues': issues, 'warnings': warnings,
                'limits': '依赖 agent 对实时页面及证据的准确转录；不证明已排版或已同步公众号。'}

    if observation.get('date') != expected_date or observation.get('timezone') != 'Asia/Shanghai':
        issue('wrong_date_or_timezone')
    run_date = now.astimezone(SHANGHAI).date().isoformat()
    if historical_test and expected_date >= run_date:
        issue('historical_test_requires_past_date')
    try:
        observed_at = datetime.fromisoformat(observation['observed_at'])
        observation_date = run_date if historical_test else expected_date
        if observed_at.tzinfo is None or observed_at.astimezone(SHANGHAI).date().isoformat() != observation_date:
            issue('wrong_observation_date')
        elif not timedelta(0) <= now - observed_at <= timedelta(minutes=60):
            issue('stale_or_future_observation')
    except (KeyError, ValueError, TypeError):
        issue('invalid_observation_time')
    reservation = observation.get('reservation', {})
    if not isinstance(reservation, dict):
        raise ValueError('reservation must be an object')
    if reservation.get('status') != 'ok' or reservation.get('complete') is not True:
        issue('wps_login_required' if reservation.get('status') == 'login_required'
              else 'reservation_unreadable_or_incomplete')
    if reservation.get('date') != expected_date:
        issue('reservation_date_not_confirmed')
    if not isinstance(reservation.get('evidence_paths'), list) or not reservation['evidence_paths']:
        issue('reservation_evidence_missing')
    if issues:
        return report('check_failed')
    articles = reservation.get('articles')
    if not isinstance(articles, list):
        raise ValueError('articles must be a list, including for an empty day')
    if not articles:
        return report('no_reservations')
    for article in articles:
        if (not isinstance(article, dict) or type(article.get('row')) is not int or article['row'] < 1
                or not isinstance(article.get('title'), str) or not article['title'].strip()
                or type(article.get('is_headline')) is not bool
                or not isinstance(article.get('remarks'), str)):
            raise ValueError('each reservation needs row, full title, headline decision and remarks')
    rows = {a['row'] for a in articles}
    if len(rows) != len(articles):
        raise ValueError('duplicate reservation rows')
    headlines = [a for a in articles if a['is_headline']]
    if len(headlines) != 1:
        issue('headline_unresolved')
    if reservation.get('conflicts_resolved') is not True:
        issue('reservation_remarks_or_order_unresolved')
    xiumi = observation.get('xiumi', {})
    if not isinstance(xiumi, dict):
        raise ValueError('xiumi must be an object')
    if (xiumi.get('status') != 'ok' or xiumi.get('account_confirmed') is not True
            or xiumi.get('search_complete') is not True):
        issue('xiumi_login_required' if xiumi.get('status') == 'login_required'
              else 'xiumi_unavailable_wrong_account_or_incomplete_search')
        return report('check_failed')
    if not isinstance(xiumi.get('evidence_paths'), list) or not xiumi['evidence_paths']:
        issue('xiumi_evidence_missing')
        return report('check_failed')
    matches = xiumi.get('matches')
    if not isinstance(matches, list):
        raise ValueError('matches must be a list')
    by_row = {}
    for match in matches:
        if not isinstance(match, dict) or type(match.get('row')) is not int:
            raise ValueError('each match needs a reservation row')
        row = match['row']
        if row not in rows or row in by_row:
            raise ValueError('unexpected or duplicate match row')
        candidates = match.get('candidates')
        if not isinstance(candidates, list):
            raise ValueError('candidates must be a list')
        for candidate in candidates:
            if (not isinstance(candidate, dict) or not isinstance(candidate.get('id'), str)
                    or not candidate['id'].strip() or not isinstance(candidate.get('title'), str)
                    or not candidate['title'].strip() or type(candidate.get('identity_confirmed')) is not bool):
                raise ValueError('candidate needs observed identity, title and explicit version confirmation')
        by_row[row] = candidates
    selected_ids = {}
    for article in articles:
        row = article['row']
        if row not in by_row:
            issue('row_not_checked', row)
            continue
        candidates = by_row[row]
        if not candidates:
            issue('manuscript_missing', row)
        elif len(candidates) > 1:
            issue('multiple_versions_unresolved', row)
        elif candidates[0]['identity_confirmed'] is not True:
            issue('identity_or_week_unresolved', row)
        else:
            candidate = candidates[0]
            selected_ids.setdefault(candidate['id'], []).append(row)
            if candidate['title'] != article['title']:
                reason = candidate.get('identity_reason')
                if not isinstance(reason, str) or not reason.strip():
                    issue('identity_match_reason_missing', row)
                else:
                    warnings.append({'code': 'title_difference_matched', 'row': row,
                                     'reservation_title': article['title'],
                                     'xiumi_title': candidate['title'], 'draft_id': candidate['id'],
                                     'reason': reason})
    for reused_rows in selected_ids.values():
        if len(reused_rows) > 1:
            for row in reused_rows:
                issue('same_manuscript_for_multiple_reservations', row)
    ordered = headlines + [a for a in sorted(articles, key=lambda a: a['row']) if not a['is_headline']]
    return report('needs_attention' if issues else 'ready_to_format', [a['row'] for a in ordered])


def check_evidence(observation):
    for section in ('reservation', 'xiumi'):
        data = observation.get(section, {})
        if not isinstance(data, dict):
            raise ValueError('invalid evidence section')
        paths = data.get('evidence_paths', [])
        if not isinstance(paths, list):
            raise ValueError('invalid evidence list')
        for item in paths:
            if not isinstance(item, str):
                raise ValueError('invalid evidence path')
            path = (ROOT / item).resolve()
            if (not any(path.is_relative_to((ROOT / folder).resolve()) for folder in ('local', 'artifacts', 'data'))
                    or not path.is_file()):
                raise ValueError('evidence must exist in a private project directory')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('observation', type=Path)
    parser.add_argument('--date', default=datetime.now(SHANGHAI).date().isoformat())
    parser.add_argument('--historical-test', action='store_true',
                        help='Explicit historical booking test; observations must still be fresh today.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'artifacts').resolve()) or output.suffix != '.json':
        raise ValueError('report must be a JSON file in artifacts/')
    if output.exists():
        raise ValueError('do not overwrite prior evidence; choose a fresh report filename')
    observation = json.loads(args.observation.read_text(encoding='utf-8'))
    result = evaluate(observation, args.date, historical_test=args.historical_test)
    check_evidence(observation)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'status': result['status'], 'needs_attention': result['needs_attention'],
                      'issue_count': len(result['issues']), 'report': str(output)}, ensure_ascii=False))
    return 2 if result['needs_attention'] else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__,
                          'message': 'Invalid observation or evidence; daily check did not complete.'}))
        sys.exit(1)
