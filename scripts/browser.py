"""Local browser tool, run INSIDE the browser container. No third-party Python packages.

JSON output lets another agent use the same interface. One command holds the profile
lock; this prevents simultaneous command execution, not competing task ownership.
"""
import argparse
import base64
import fcntl
import json
from pathlib import Path
import sys
from urllib import error, parse, request

ROOT = Path('/home/seluser/profile')
STATE = ROOT / 'session.json'
ENDPOINT = 'http://127.0.0.1:4444'
ELEMENT_KEY = 'element-6066-11e4-a52e-4f735466cecf'
FIXTURE = 'file:///opt/project/fixtures/browser-check.html'
TEST_TEXT = '未央宣传组：容器重启验证'

# Read rendered DOM only. No cookies, page application state, or network requests.
INSPECT_SCRIPT = r"""
const selector = arguments[0];
const matches = document.querySelectorAll(selector);
if (matches.length !== 1) throw new Error('Inspection selector must match exactly one element.');
const root = matches[0];
const visible = e => {
  const s = getComputedStyle(e), r = e.getBoundingClientRect();
  return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
};
const rect = e => {
  const r = e.getBoundingClientRect();
  return {x:r.x, y:r.y, width:r.width, height:r.height};
};
const elements = [root, ...root.querySelectorAll('*')].filter(visible);
const nodes = elements.slice(0, 3000).map(e => ({
  tag:e.tagName.toLowerCase(), id:e.id, class:e.getAttribute('class') || '',
  role:e.getAttribute('role'), label:e.getAttribute('aria-label'),
  placeholder:e.getAttribute('placeholder'), type:e.getAttribute('type'),
  own_text:[...e.childNodes].filter(n => n.nodeType === Node.TEXT_NODE)
    .map(n => n.textContent).join(' ').trim().slice(0, 500),
  value:['INPUT','TEXTAREA'].includes(e.tagName) && e.type !== 'password' ? e.value : null,
  editable:e.isContentEditable, rect:rect(e),
  frame_src:e.tagName === 'IFRAME' ? e.getAttribute('src') : null
}));
const images = elements.filter(e => e.tagName === 'IMG').map(e => ({
  src:e.currentSrc || e.getAttribute('src'), alt:e.getAttribute('alt'),
  complete:e.complete, natural_width:e.naturalWidth, natural_height:e.naturalHeight,
  rect:rect(e)
}));
const backgrounds = elements.map(e => ({tag:e.tagName.toLowerCase(), id:e.id,
  class:e.getAttribute('class') || '', image:getComputedStyle(e).backgroundImage,
  rect:rect(e)})).filter(e => e.image !== 'none');
return {selector, url:location.href, title:document.title,
  text:(root.innerText || '').slice(0, 100000), text_truncated:(root.innerText || '').length > 100000,
  nodes, nodes_truncated:elements.length > 3000, images, backgrounds};
"""


class BrowserError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(ENDPOINT + path, data=data, method=method,
                          headers={'Content-Type': 'application/json'})
    try:
        with request.urlopen(req, timeout=90) as response:
            payload = json.load(response)
    except error.HTTPError as exc:
        try:
            value = json.load(exc).get('value', {})
        except (ValueError, AttributeError):
            value = {}
        raise BrowserError(value.get('error', 'http error'), value.get('message', str(exc))) from exc
    except (error.URLError, TimeoutError) as exc:
        raise BrowserError('connection error', str(exc)) from exc
    value = payload.get('value')
    if isinstance(value, dict) and 'error' in value:
        raise BrowserError(value['error'], value.get('message', 'WebDriver failed'))
    return value


def session_path(sid, suffix=''):
    return '/session/' + parse.quote(sid, safe='') + suffix


def existing():
    if not STATE.exists():
        raise BrowserError('no saved session', 'Run start first.')
    sid = json.loads(STATE.read_text())['session_id']
    call('GET', session_path(sid, '/url'))
    return sid


def start():
    if STATE.exists():
        try:
            return existing(), True
        except BrowserError as exc:
            if exc.code != 'invalid session id':
                raise
    value = call('POST', '/session', {'capabilities': {'alwaysMatch': {
        'browserName': 'chrome',
        'pageLoadStrategy': 'normal',
        'goog:chromeOptions': {'args': [
            '--user-data-dir=/home/seluser/profile/chromium',
            '--window-size=1360,900', '--no-first-run', '--no-default-browser-check'
        ]}
    }}})
    sid = value['sessionId']
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps({'session_id': sid}))
    temporary.replace(STATE)
    return sid, False


def find(sid, selector):
    value = call('POST', session_path(sid, '/element'),
                 {'using': 'css selector', 'value': selector})
    return value[ELEMENT_KEY]


def element_path(sid, selector, suffix):
    return session_path(sid, '/element/' + find(sid, selector) + suffix)


def type_active(sid, text):
    element = call('GET', session_path(sid, '/element/active'))[ELEMENT_KEY]
    call('POST', session_path(sid, '/element/' + element + '/value'), {'text': text})


def observe(sid):
    return {'title': call('GET', session_path(sid, '/title')),
            'url': call('GET', session_path(sid, '/url')),
            'window': call('GET', session_path(sid, '/window/rect'))}


def screenshot(sid, name):
    if Path(name).name != name or not name.endswith('.png'):
        raise BrowserError('invalid filename', 'Use a plain filename ending in .png.')
    target = Path('/opt/artifacts') / name
    target.write_bytes(base64.b64decode(call('GET', session_path(sid, '/screenshot')), validate=True))
    return 'artifacts/' + name


def navigate(sid, url):
    if parse.urlsplit(url).scheme not in ('https', 'http') and url != FIXTURE:
        raise BrowserError('invalid URL', 'Only HTTP(S) URLs or the built-in test fixture are supported.')
    call('POST', session_path(sid, '/url'), {'url': url})


def inspect(sid, selector, name, frame=None):
    if Path(name).name != name or not name.endswith('.json'):
        raise BrowserError('invalid filename', 'Use a plain filename ending in .json.')
    # Each call begins and ends in the top-level document, including on failure.
    call('POST', session_path(sid, '/frame'), {'id': None})
    try:
        if frame:
            frames = call('POST', session_path(sid, '/elements'),
                          {'using': 'css selector', 'value': frame})
            if len(frames) != 1:
                raise BrowserError('ambiguous frame', 'Frame selector must match exactly one element.')
            call('POST', session_path(sid, '/frame'), {'id': frames[0]})
        snapshot = call('POST', session_path(sid, '/execute/sync'),
                        {'script': INSPECT_SCRIPT, 'args': [selector]})
        snapshot['frame_selector'] = frame
        target = Path('/opt/artifacts') / name
        target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'artifact': 'artifacts/' + name, 'node_count': len(snapshot['nodes']),
                'image_count': len(snapshot['images']), 'nodes_truncated': snapshot['nodes_truncated'],
                'text_truncated': snapshot['text_truncated']}
    finally:
        call('POST', session_path(sid, '/frame'), {'id': None})


def click(sid, x, y):
    rect = call('GET', session_path(sid, '/window/rect'))
    if not (0 <= x < rect['width'] and 0 <= y < rect['height']):
        raise BrowserError('invalid coordinates', 'Coordinates must be inside the screenshot.')
    call('POST', session_path(sid, '/actions'), {'actions': [{
        'type': 'pointer', 'id': 'mouse', 'parameters': {'pointerType': 'mouse'},
        'actions': [{'type': 'pointerMove', 'duration': 0, 'origin': 'viewport', 'x': x, 'y': y},
                    {'type': 'pointerDown', 'button': 0}, {'type': 'pointerUp', 'button': 0}]
    }]})


def self_test(sid, resume):
    navigate(sid, FIXTURE)
    if resume:
        marker = call('GET', element_path(sid, '#marker', '/text'))
        if marker != TEST_TEXT:
            raise BrowserError('persistence failed', 'The previous test marker was not restored.')
    else:
        call('POST', element_path(sid, '#draft', '/clear'), {})
        field = call('GET', element_path(sid, '#draft', '/rect'))
        click(sid, round(field['x'] + field['width']/2), round(field['y'] + field['height']/2))
        type_active(sid, TEST_TEXT)
        # The same pointer API an image-based agent would use; no scripted DOM click.
        rect = call('GET', element_path(sid, '#check', '/rect'))
        click(sid, round(rect['x'] + rect['width']/2), round(rect['y'] + rect['height']/2))
        result = call('GET', element_path(sid, '#result', '/text'))
        if result != '已验证：' + TEST_TEXT:
            raise BrowserError('click failed', 'Test page did not show the expected result.')
    shot = screenshot(sid, 'browser-restored.png' if resume else 'browser-check.png')
    return {'passed': True, 'restored': resume, 'screenshot': shot, **observe(sid)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'start', 'observe', 'reload', 'close'):
        sub.add_parser(name)
    p = sub.add_parser('open'); p.add_argument('url')
    p = sub.add_parser('screenshot'); p.add_argument('filename', nargs='?', default='browser.png')
    p = sub.add_parser('click'); p.add_argument('x', type=int); p.add_argument('y', type=int)
    p = sub.add_parser('type'); p.add_argument('text')
    p = sub.add_parser('key'); p.add_argument('name', choices=['Enter', 'Tab', 'Escape', 'Backspace'])
    p = sub.add_parser('scroll'); p.add_argument('delta', type=int)
    p = sub.add_parser('text'); p.add_argument('selector', nargs='?', default='body')
    p = sub.add_parser('inspect'); p.add_argument('selector'); p.add_argument('filename')
    p.add_argument('--frame', help='CSS selector of one iframe in the top-level document')
    p = sub.add_parser('self-test'); p.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'command.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BrowserError('busy', 'Another browser command is running.') from exc
        if args.command == 'status':
            return call('GET', '/status')
        if args.command == 'start':
            sid, reused = start()
            return {'reused': reused, **observe(sid)}
        sid = existing()
        if args.command == 'close':
            call('DELETE', session_path(sid)); STATE.unlink()
            return {'closed': True}
        if args.command == 'open':
            navigate(sid, args.url)
        elif args.command == 'reload':
            call('POST', session_path(sid, '/refresh'), {})
        elif args.command == 'screenshot':
            return {'screenshot': screenshot(sid, args.filename)}
        elif args.command == 'click':
            click(sid, args.x, args.y)
        elif args.command == 'type':
            type_active(sid, args.text)
        elif args.command == 'key':
            key = {'Enter': '\ue007', 'Tab': '\ue004', 'Escape': '\ue00c', 'Backspace': '\ue003'}[args.name]
            type_active(sid, key)
        elif args.command == 'scroll':
            call('POST', session_path(sid, '/actions'), {'actions': [{
                'type': 'wheel', 'id': 'wheel', 'actions': [{'type': 'scroll', 'x': 200, 'y': 200,
                'deltaX': 0, 'deltaY': args.delta, 'duration': 200, 'origin': 'viewport'}]
            }]})
        elif args.command == 'text':
            return {'text': call('GET', element_path(sid, args.selector, '/text'))}
        elif args.command == 'inspect':
            return inspect(sid, args.selector, args.filename, args.frame)
        elif args.command == 'self-test':
            return self_test(sid, args.resume)
        return observe(sid)


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'result': main()}, ensure_ascii=False))
    except (BrowserError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'ok': False, 'error': getattr(exc, 'code', type(exc).__name__),
                          'message': str(exc)}, ensure_ascii=False))
        sys.exit(1)
