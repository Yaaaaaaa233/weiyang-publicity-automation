"""Read-only Xiumi composition preflight. Never submits or verifies a WeChat draft.

Consume browser.py snapshots and a manually confirmed private reservation plan.
Keep manuscript details in ignored artifacts; stdout contains counts only.
"""
import argparse
from datetime import date
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def has_class(node, name):
    return name in node.get('class', '').split()


def inside(node, parent):
    prefix = parent.get('selector', '')
    return bool(prefix) and node.get('selector', '').startswith(prefix + ' > ')


def check(snapshot, plan):
    if not isinstance(snapshot, dict) or not isinstance(plan, dict):
        raise ValueError('Snapshot and plan must be objects.')
    rows = plan.get('articles')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Plan needs a nonempty articles list.')
    date.fromisoformat(plan['date'])
    if not isinstance(plan.get('target_account'), str) or not plan['target_account'].strip():
        raise ValueError('Plan needs a target account.')
    attempts = plan.get('prior_sync_attempts')
    if type(attempts) is not int or attempts < 0:
        raise ValueError('Declare prior_sync_attempts as a nonnegative integer.')
    for row in rows:
        if not isinstance(row, dict) or type(row.get('row')) is not int or row['row'] < 1:
            raise ValueError('Each article needs its positive source row number.')
        if type(row.get('is_headline')) is not bool:
            raise ValueError('Headline decisions must be explicitly confirmed booleans.')
        if not isinstance(row.get('sync_title'), str) or not row['sync_title'].strip():
            raise ValueError('Each article needs its complete expected sync title.')
    if len({r['row'] for r in rows}) != len(rows):
        raise ValueError('Duplicate reservation rows.')
    if len({r['sync_title'] for r in rows}) != len(rows):
        raise ValueError('Resolve duplicate titles with distinguishable test copies first.')
    nodes, backgrounds = snapshot.get('nodes'), snapshot.get('backgrounds')
    if not isinstance(nodes, list) or not isinstance(backgrounds, list):
        raise ValueError('Snapshot needs nodes and backgrounds lists.')
    checks = []

    def record(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})

    record('snapshot_complete', snapshot.get('nodes_truncated') is False and
           snapshot.get('text_truncated') is False and bool(snapshot.get('text')))
    record('xiumi_page', urlsplit(snapshot.get('url', '')).hostname == 'xiumi.us')
    headlines = [r for r in rows if r['is_headline']]
    record('one_confirmed_headline', len(headlines) == 1)
    source_order = sorted(rows, key=lambda r: r['row'])
    expected = headlines + [r for r in source_order if not r['is_headline']]
    roots = [n for n in nodes if has_class(n, 'x3-package')]
    record('one_composition_root', len(roots) == 1)
    plates = []
    if len(roots) == 1:
        plates = [n for n in nodes if has_class(n, 'x3-slice-plate') and inside(n, roots[0])]
        plates.sort(key=lambda n: n['rect']['y'])
    record('article_count', len(plates) == len(rows))
    record('distinct_row_positions', len({n['rect']['y'] for n in plates}) == len(plates))
    actual_titles = []
    cover_checks = []
    for index, plate in enumerate(plates, 1):
        titles = [n for n in nodes if inside(n, plate) and n['tag'] == 'input' and has_class(n, 'inner')]
        record('one_title_' + str(index), len(titles) == 1)
        actual_titles.append(titles[0].get('value') if len(titles) == 1 else None)
        covers = [b for b in backgrounds if inside(b, plate) and has_class(b, 'bg-image')]
        present = (len(covers) == 1 and bool(covers[0].get('image')) and
                   covers[0]['image'] != 'none' and covers[0]['rect']['width'] > 0 and
                   covers[0]['rect']['height'] > 0)
        record('cover_style_' + str(index), present)
        cover_checks.append(present)
    record('headline_first_then_source_order', actual_titles == [r['sync_title'] for r in expected])
    accounts = [n['own_text'] for n in nodes if n['tag'] == 'label' and has_class(n, 'wx-username')]
    record('one_expected_target', accounts == [plan['target_account']])
    for option in ('preview-check', 'create-new-check'):
        controls = [n for n in nodes if n['tag'] == 'input' and has_class(n, option)]
        record(option + '_disabled', len(controls) == 1 and controls[0].get('checked') is False)
    composition_passed = all(c['passed'] for c in checks)
    return {
        'composition_checks_passed': composition_passed,
        'no_prior_sync_attempt_recorded': attempts == 0,
        'ready_for_supervised_submission_review': composition_passed and attempts == 0,
        'checks': checks, 'expected_rows': [r['row'] for r in expected],
        'actual_titles': actual_titles, 'covers_with_nonempty_styles': sum(cover_checks),
        'prior_sync_attempts': attempts,
        'limits': ['封面只检查非空 CSS 样式，加载、内容及比例仍需视觉核对。',
                   '不认证同题版本或预约表读取正确性；计划由使用者确认。',
                   '不提交同步、不判断公众号草稿生成，也不授予执行权限。',
                   '此前已提交时先核对实际结果，不自动重放；本检查不是浏览器锁。'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'sync-preflight.json')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'artifacts').resolve()) or output.suffix != '.json':
        raise ValueError('Write a JSON report under artifacts/.')
    result = check(json.loads(args.snapshot.read_text(encoding='utf-8')),
                   json.loads(args.plan.read_text(encoding='utf-8')))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'composition_checks_passed': result['composition_checks_passed'],
                      'ready_for_supervised_submission_review': result['ready_for_supervised_submission_review'],
                      'passed_checks': sum(c['passed'] for c in result['checks']),
                      'failed_checks': sum(not c['passed'] for c in result['checks']),
                      'prior_sync_attempts': result['prior_sync_attempts'], 'report': str(output)}, ensure_ascii=False))
    return 0 if result['ready_for_supervised_submission_review'] else 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'message': 'Invalid snapshot, plan or report path.'}))
        sys.exit(1)
