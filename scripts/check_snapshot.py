"""Check a scoped, rendered browser snapshot. Write private details to a local report.

This checks observable text/image facts only, not article quality or publishing rules.
Uses Python's standard library on Windows and macOS.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def check(snapshot, rules):
    checks = []
    warnings = []

    def record(name, passed, detail):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})

    if not isinstance(snapshot, dict):
        raise ValueError('Snapshot must be an object.')
    for field in ('selector', 'text', 'nodes', 'images', 'backgrounds'):
        if field not in snapshot:
            raise ValueError('Missing snapshot field: ' + field)
    if not isinstance(snapshot['selector'], str) or not isinstance(snapshot['text'], str):
        raise ValueError('Snapshot selector and text must be strings.')
    if not all(isinstance(snapshot[field], list) for field in ('nodes', 'images', 'backgrounds')):
        raise ValueError('Snapshot nodes, images and backgrounds must be lists.')
    for image in snapshot['images']:
        if not isinstance(image, dict) or not isinstance(image.get('rect', {}), dict):
            raise ValueError('Each image must contain valid metadata.')
        if not all(type(image.get(field)) in (int, float) for field in ('natural_width', 'natural_height')):
            raise ValueError('Image dimensions must be numbers.')
    record('读取结果完整', not snapshot.get('text_truncated') and not snapshot.get('nodes_truncated'),
           '文本与节点未截断；此项不证明折叠内容或未加载内容已获取。')
    text = ''.join(snapshot['text'].split())
    for index, expected in enumerate(rules.get('required_text', []), 1):
        record('必需文本 ' + str(index), ''.join(expected.split()) in text, expected)
    expected_count = rules.get('expected_image_elements')
    if expected_count is not None:
        record('图片元素数量', len(snapshot['images']) == expected_count,
               f"预期 {expected_count}，实际 {len(snapshot['images'])}；装饰图也计入。")
    for index, image in enumerate(snapshot['images'], 1):
        loaded = image.get('complete') and image.get('natural_width', 0) > 0 and image.get('natural_height', 0) > 0
        record('图片加载 ' + str(index), loaded,
               f"原始尺寸 {image.get('natural_width', 0)}×{image.get('natural_height', 0)}；不验证图片语义或清晰度。")
    if not snapshot['images']:
        warnings.append('读取范围内没有图片元素；不代表整篇稿件无图。')
    if snapshot['backgrounds']:
        warnings.append(f"发现 {len(snapshot['backgrounds'])} 个 CSS 背景样式；只记录样式，未断言背景资源加载成功。")
    if snapshot['selector'] == 'body':
        warnings.append('读取范围为 body，请确认其中没有模板库、菜单或阅读数等页面内容。')
    if not rules:
        warnings.append('未提供业务规则；本报告只检查读取完整性及图片元素加载状态。')
    warnings.append('封面、落款、视觉排版、原文事实和最终发布状态需要另行核对。')
    return {'automated_checks_passed': all(c['passed'] for c in checks),
            'checks': checks, 'warnings': warnings,
            'image_elements': len(snapshot['images']), 'background_styles': len(snapshot['backgrounds'])}


def escaped(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def report(snapshot, result, rule_name):
    lines = ['# 页面内容检查报告', '',
             '本报告只覆盖所选页面区域与已配置规则。检查通过不等于稿件可发布。', '',
             f"读取范围：`{snapshot['selector']}`。",
             f"iframe 范围：`{snapshot.get('frame_selector') or '顶层页面'}`。",
             f"规则：{rule_name or '未配置'}。", '',
             '| 检查 | 结果 | 依据与范围 |', '| --- | --- | --- |']
    for item in result['checks']:
        lines.append(f"| {escaped(item['name'])} | {'通过' if item['passed'] else '失败'} | {escaped(item['detail'])} |")
    lines.extend(['', '## 待核对项', ''])
    lines.extend('- ' + warning for warning in result['warnings'])
    lines.extend(['', '## 图片尺寸', '', '| 元素 | 原始尺寸 | 显示尺寸 |', '| --- | --- | --- |'])
    for index, image in enumerate(snapshot['images'], 1):
        rect = image.get('rect', {})
        lines.append(f"| {index} | {image.get('natural_width', 0)}×{image.get('natural_height', 0)} | {rect.get('width', 0):.1f}×{rect.get('height', 0):.1f} CSS px |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--rules', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'content-check.md')
    args = parser.parse_args()
    # Reports may contain manuscript text from configured rules. Keep them out of Git.
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'artifacts').resolve()):
        raise ValueError('Write reports under the project artifacts/ directory.')
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    rules = json.loads(args.rules.read_text(encoding='utf-8')) if args.rules else {}
    if not isinstance(rules, dict) or not isinstance(rules.get('required_text', []), list) or not all(isinstance(t, str) for t in rules.get('required_text', [])):
        raise ValueError('Rules must be an object with a list of required_text strings.')
    count = rules.get('expected_image_elements')
    if count is not None and (type(count) is not int or count < 0):
        raise ValueError('expected_image_elements must be a nonnegative integer.')
    result = check(snapshot, rules)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report(snapshot, result, rules.get('name')), encoding='utf-8')
    print(json.dumps({'ok': True, 'automated_checks_passed': result['automated_checks_passed'],
                      'passed_checks': sum(c['passed'] for c in result['checks']),
                      'failed_checks': sum(not c['passed'] for c in result['checks']),
                      'manual_notes': len(result['warnings']), 'report': str(output)}, ensure_ascii=False))
    return 0 if result['automated_checks_passed'] else 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False))
        sys.exit(1)
