"""Local browser tool, run INSIDE the browser container. No third-party Python packages.

JSON output lets another agent use the same interface. One command holds the profile
lock; this prevents simultaneous command execution, not competing task ownership.
"""
import argparse
import base64
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows can import pure helpers; commands still require the Linux container.
import json
import os
import subprocess
import time
from pathlib import Path
import sys
from urllib import error, parse, request

ROOT = Path('/home/seluser/profile')
STATE = ROOT / 'session.json'
LEASE = ROOT / 'workflow-lease.json'
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
const path = e => {
  const parts = [];
  while (e && e.nodeType === Node.ELEMENT_NODE) {
    const tag = e.tagName.toLowerCase();
    const peers = e.parentElement ? [...e.parentElement.children].filter(x => x.tagName === e.tagName) : [e];
    parts.unshift(tag + ':nth-of-type(' + (peers.indexOf(e) + 1) + ')');
    e = e.parentElement;
  }
  return parts.join(' > ');
};
const elements = [root, ...root.querySelectorAll('*')].filter(visible);
const nodes = elements.slice(0, 3000).map(e => ({
  tag:e.tagName.toLowerCase(), id:e.id, class:e.getAttribute('class') || '',
  parent_class:e.parentElement ? e.parentElement.getAttribute('class') || '' : '',
  parent_tag:e.parentElement ? e.parentElement.tagName.toLowerCase() : null,
  role:e.getAttribute('role'), label:e.getAttribute('aria-label'),
  placeholder:e.getAttribute('placeholder'), type:e.getAttribute('type'),
  own_text:[...e.childNodes].filter(n => n.nodeType === Node.TEXT_NODE)
    .map(n => n.textContent).join(' ').trim().slice(0, 500),
  rendered_text:(e.innerText || '').slice(0, 12000),
  rendered_text_truncated:(e.innerText || '').length > 12000,
  value:['INPUT','TEXTAREA'].includes(e.tagName) && e.type !== 'password' ? e.value : null,
  checked:e.tagName === 'INPUT' && ['checkbox','radio'].includes(e.type) ? e.checked : null,
  disabled:'disabled' in e ? Boolean(e.disabled) : null,
  editable:e.isContentEditable, rect:rect(e),
  editable_attribute:e.getAttribute('contenteditable'),
  dom_attributes:Object.fromEntries([...e.attributes]
    .filter(a => a.name.startsWith('data-') || a.name.startsWith('aria-') ||
      ['title','draggable'].includes(a.name))
    .map(a => [a.name, a.value])),
  selector:path(e),
  inline_style:e.getAttribute('style') || '',
  paragraph_text:e.tagName === 'P' ? (e.innerText || '').slice(0, 10000) : null,
  paragraph_text_truncated:e.tagName === 'P' && (e.innerText || '').length > 10000,
  href:e.tagName === 'A' ? e.href : null,
  target:e.tagName === 'A' ? e.getAttribute('target') : null,
  frame_src:e.tagName === 'IFRAME' ? e.getAttribute('src') : null
}));
const images = elements.filter(e => e.tagName === 'IMG').map(e => ({
  src:e.currentSrc || e.getAttribute('src'), alt:e.getAttribute('alt'),
  complete:e.complete, natural_width:e.naturalWidth, natural_height:e.naturalHeight,
  rect:rect(e)
}));
const backgrounds = elements.map(e => ({tag:e.tagName.toLowerCase(), id:e.id,
  class:e.getAttribute('class') || '', image:getComputedStyle(e).backgroundImage,
  selector:path(e), rect:rect(e)})).filter(e => e.image !== 'none');
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
            'handle': call('GET', session_path(sid, '/window')),
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


def enter_frame(sid, frame):
    call('POST', session_path(sid, '/frame'), {'id': None})
    if frame:
        matches = call('POST', session_path(sid, '/elements'), {'using':'css selector', 'value':frame})
        if len(matches) != 1:
            raise BrowserError('ambiguous frame', 'Expected one frame.')
        call('POST', session_path(sid, '/frame'), {'id':matches[0]})


def workflow_lease(command, token):
    """Called under command.lock. Stale leases require explicit owner recovery."""
    if command == 'lease-acquire':
        if not token or len(token) < 16:
            raise BrowserError('lease token required', 'Use a unique workflow token.')
        if LEASE.exists():
            raise BrowserError('workflow busy', 'An existing workflow lease requires inspection; do not take over.')
        LEASE.write_text(json.dumps({'token':token, 'created_at':time.time()}))
        return {'acquired':True}
    if command == 'lease-release':
        if not LEASE.exists() or json.loads(LEASE.read_text())['token'] != token:
            raise BrowserError('wrong lease owner', 'Only the current owner can release.')
        LEASE.unlink()
        return {'released':True}
    if LEASE.exists() and json.loads(LEASE.read_text())['token'] != token:
        raise BrowserError('workflow busy', 'Browser reserved by a workflow; do not operate concurrently.')


def key_action(sid, name):
    if name in ('SelectAll', 'Copy'):
        letter = 'a' if name == 'SelectAll' else 'c'
        call('POST', session_path(sid, '/actions'), {'actions':[{
            'type':'key','id':'keyboard','actions':[
                {'type':'keyDown','value':'\ue009'},{'type':'keyDown','value':letter},
                {'type':'keyUp','value':letter},{'type':'keyUp','value':'\ue009'}]}]})
    else:
        type_active(sid, {'Enter':'\ue007','Tab':'\ue004','Escape':'\ue00c','Backspace':'\ue003'}[name])


def copy_editor(sid, selector, name, frame):
    if not frame or selector != 'textarea.inputarea' or Path(name).name != name or not name.endswith('.json'):
        raise BrowserError('invalid editor copy', 'Only the observed Monaco editor is supported.')
    enter_frame(sid, frame)
    try:
        elements = call('POST', session_path(sid, '/elements'), {'using':'css selector','value':selector})
        if len(elements) != 1:
            raise BrowserError('ambiguous editor', 'Expected exactly one source editor.')
        target = elements[0][ELEMENT_KEY]
        # Preserve the editor and use normal keys; xclip reads only this container's clipboard.
        # Monaco's 1px textarea is covered; click its rendered code surface instead.
        surfaces = call('POST', session_path(sid, '/elements'), {'using':'css selector','value':'.view-lines'})
        if len(surfaces) != 1:
            raise BrowserError('ambiguous editor surface','Expected one rendered source surface.')
        call('POST', session_path(sid, '/element/' + surfaces[0][ELEMENT_KEY] + '/click'), {})
        if call('GET',session_path(sid,'/element/active'))[ELEMENT_KEY] != target:
            raise BrowserError('focus mismatch','Source editor is not focused.')
        marker = 'weiyang-copy-' + str(time.time_ns())
        subprocess.run(['xclip','-selection','clipboard'],input=marker.encode(),check=True,timeout=5,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        key_action(sid, 'SelectAll'); key_action(sid, 'Copy')
        deadline = time.monotonic()+5
        while True:
            value = subprocess.run(['xclip','-selection','clipboard','-o'],capture_output=True,check=True,timeout=5).stdout.decode('utf-8')
            if value != marker:
                break
            if time.monotonic() >= deadline:
                raise BrowserError('copy pending', 'Editor copy did not produce fresh text.')
            time.sleep(.1)
        if len(value) > 100000:
            raise BrowserError('oversized source', 'Source exceeded supported bounds.')
        (Path('/opt/artifacts')/name).write_text(json.dumps({'source':value},ensure_ascii=False),encoding='utf-8')
        return {'artifact':'artifacts/'+name,'source_length':len(value)}
    finally:
        enter_frame(sid,None)


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
    parser.add_argument('--lease-token', default=None)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'start', 'observe', 'reload', 'close', 'windows', 'lease-acquire', 'lease-release'):
        sub.add_parser(name)
    p = sub.add_parser('switch-window'); p.add_argument('handle')
    p = sub.add_parser('focus'); p.add_argument('selector')
    p.add_argument('--frame')
    p = sub.add_parser('click-element'); p.add_argument('selector')
    p.add_argument('--expect-text', required=True)
    p.add_argument('--frame')
    p = sub.add_parser('copy-editor'); p.add_argument('selector'); p.add_argument('filename'); p.add_argument('--frame',required=True)
    p = sub.add_parser('new-window'); p.add_argument('url')
    p = sub.add_parser('open'); p.add_argument('url')
    p = sub.add_parser('screenshot'); p.add_argument('filename', nargs='?', default='browser.png')
    p = sub.add_parser('click'); p.add_argument('x', type=int); p.add_argument('y', type=int)
    p = sub.add_parser('type'); p.add_argument('text')
    p = sub.add_parser('key'); p.add_argument('name', choices=['Enter', 'Tab', 'Escape', 'Backspace', 'SelectAll'])
    p.add_argument('--frame')
    p = sub.add_parser('scroll'); p.add_argument('delta', type=int)
    p.add_argument('--x', type=int, default=200)
    p.add_argument('--y', type=int, default=200)
    p = sub.add_parser('text'); p.add_argument('selector', nargs='?', default='body')
    p = sub.add_parser('inspect'); p.add_argument('selector'); p.add_argument('filename')
    p.add_argument('--frame', help='CSS selector of one iframe in the top-level document')
    p = sub.add_parser('self-test'); p.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if fcntl is None:
        raise BrowserError('unsupported platform','Run browser commands inside the Linux container via browserctl.py.')
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'command.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BrowserError('busy', 'Another browser command is running.') from exc
        if args.command == 'status':
            return call('GET', '/status')
        lease_result = workflow_lease(args.command, args.lease_token)
        if args.command in ('lease-acquire','lease-release'):
            return lease_result
        if args.command == 'start':
            sid, reused = start()
            return {'reused': reused, **observe(sid)}
        sid = existing()
        enter_frame(sid,None)
        if args.command == 'copy-editor':
            return copy_editor(sid,args.selector,args.filename,args.frame)
        if args.command in ('focus','click-element','key') and args.frame:
            enter_frame(sid,args.frame)
        if args.command == 'new-window':
            # Open a separate composition without discarding the current window.
            if parse.urlsplit(args.url).scheme not in ('https', 'http'):
                raise BrowserError('invalid URL', 'Use an observed HTTP(S) URL.')
            previous = call('GET', session_path(sid, '/window'))
            created = call('POST', session_path(sid, '/window/new'), {'type': 'tab'})
            call('POST', session_path(sid, '/window'), {'handle': created['handle']})
            navigate(sid, args.url)
            return {'previous_handle': previous, **observe(sid)}
        if args.command == 'close':
            call('DELETE', session_path(sid)); STATE.unlink()
            return {'closed': True}
        if args.command == 'windows':
            current = call('GET', session_path(sid, '/window'))
            windows = []
            try:
                for handle in call('GET', session_path(sid, '/window/handles')):
                    call('POST', session_path(sid, '/window'), {'handle': handle})
                    windows.append({'handle': handle, **observe(sid)})
            finally:
                call('POST', session_path(sid, '/window'), {'handle': current})
            return {'current_handle': current, 'windows': windows}
        if args.command == 'switch-window':
            handles = call('GET', session_path(sid, '/window/handles'))
            if args.handle not in handles:
                raise BrowserError('unknown window', 'Observe available windows before switching.')
            call('POST', session_path(sid, '/window'), {'handle': args.handle})
            return observe(sid)
        if args.command == 'focus':
            matches = call('POST', session_path(sid, '/elements'),
                           {'using': 'css selector', 'value': args.selector})
            if len(matches) != 1:
                raise BrowserError('ambiguous focus', 'Focus selector must match exactly one element.')
            target = matches[0][ELEMENT_KEY]
            call('POST', session_path(sid, '/element/' + target + '/click'), {})
            active = call('GET', session_path(sid, '/element/active'))[ELEMENT_KEY]
            if active != target:
                raise BrowserError('focus mismatch', 'Clicked element is not the active element; do not type.')
            enter_frame(sid,None)
            return {'focused': True, 'selector': args.selector, **observe(sid)}
        if args.command == 'click-element':
            matches = call('POST', session_path(sid, '/elements'),
                           {'using': 'css selector', 'value': args.selector})
            if len(matches) != 1:
                raise BrowserError('ambiguous click', 'Click selector must match exactly one element.')
            target = session_path(sid, '/element/' + matches[0][ELEMENT_KEY])
            if call('GET', target + '/text') != args.expect_text:
                raise BrowserError('text mismatch', 'Element text changed; inspect again before clicking.')
            if not call('GET', target + '/displayed') or not call('GET', target + '/enabled'):
                raise BrowserError('unavailable element', 'Element must be displayed and enabled.')
            call('POST', target + '/click', {})
            enter_frame(sid,None)
            return {'clicked': True, **observe(sid)}
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
            key_action(sid,args.name)
            enter_frame(sid,None)
        elif args.command == 'scroll':
            rect = call('GET', session_path(sid, '/window/rect'))
            if not (0 <= args.x < rect['width'] and 0 <= args.y < rect['height']):
                raise BrowserError('invalid coordinates', 'Scroll origin must be inside the screenshot.')
            call('POST', session_path(sid, '/actions'), {'actions': [{
                'type': 'wheel', 'id': 'wheel', 'actions': [{'type': 'scroll', 'x': args.x, 'y': args.y,
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
