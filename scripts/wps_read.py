"""Run the existing shared WPS reader via rendered UI and save fresh logs directly."""
import argparse
from datetime import datetime
import hashlib
import json
import time
import sys

try:
    from .check_runtime import Browser, Stop, ROOT, read, write, one, has_class, find_window, private_path
    from .read_wps_reservation import decode_frames, reservation_from_payload, SHANGHAI
except ImportError:
    from check_runtime import Browser, Stop, ROOT, read, write, one, has_class, find_window, private_path
    from read_wps_reservation import decode_frames, reservation_from_payload, SHANGHAI

FRAME = 'iframe#excelIde'
NAME = '未央预约表只读自动读取'
EDITOR_LABELS = ('AirScript脚本编辑器','AirScript Editor','AirScript Script Editor')


def login_required(snapshot):
    text = snapshot.get('text', '')
    return ('account.wps.cn' in snapshot.get('url', '') or
            any(label in text for label in ('Scan QR Code', '扫码登录',
                'Inviting you to log in to edit the document')) or
            any(n.get('own_text', '').strip() in ('Sign In Now', 'Sign in', '立即登录', '登录') or
                n.get('rendered_text', '').strip() in ('Sign In Now', 'Sign in', '立即登录', '登录')
                for n in snapshot.get('nodes', [])))


def require_login_if_visible(snapshot):
    if login_required(snapshot):
        raise Stop('wps_login_required', 'WPS needs a user login')


def click_navigation(browser, labels, button=False):
    # Only retry a rejected preflight, before any click was dispatched.
    for attempt in range(3):
        snapshot=browser.snapshot()
        require_login_if_visible(snapshot)
        tabs=[n for n in snapshot['nodes'] if has_class(n,'tab-btn') and n.get('rendered_text','').strip() in labels]
        node=one(tabs,lambda n:True) if tabs and not button else one(
            snapshot['nodes'],lambda n:n['own_text'] in labels and (not button or n['tag']=='button'))
        if node.get('id') and node['id'].isalnum():
            node=dict(node,selector='#'+node['id'])
        try:
            browser.click(node)
            return
        except Stop as exc:
            if exc.code == 'element click intercepted':
                require_login_if_visible(browser.snapshot())
            if exc.code not in ('ambiguous click','text mismatch','unavailable element') or attempt==2:
                raise


def execution_finished(snapshot, started, now=None):
    lines=[n['rendered_text'] for n in snapshot['nodes'] if has_class(n,'concent')]
    begins=[v for v in lines if v.startswith('WY_BEGIN ')]
    if len(begins)!=1:
        return False
    fields=begins[0].split()
    try:
        fresh=started-5 <= int(fields[1].removeprefix('wps-'))/1000 <= (time.time() if now is None else now)+5
    except (ValueError,IndexError):
        return False
    return fresh and any(v.startswith('WY_END '+fields[1]+' ') for v in lines)


def shared_script(snapshot):
    nodes = snapshot['nodes']
    heading = one(nodes,lambda n:n['own_text'] in ('文档共享脚本','Document Shared Scripts'))
    group = one([n for n in nodes if has_class(n,'listItem')],
                lambda n:heading['selector'].startswith(n['selector']+' > '))
    matches = [n for n in nodes if n['selector'].startswith(group['selector']+' > ')
               and n['own_text'] == NAME]
    return matches, heading


def shared_collapsed(snapshot):
    _,heading=shared_script(snapshot)
    group=one(snapshot['nodes'],lambda n:has_class(n,'listItem') and
              heading['selector'].startswith(n['selector']+' > '))
    return any(has_class(n,'kd-icon-arrow_triangle_right') and
               n['selector'].startswith(group['selector']+' > ') for n in snapshot['nodes'])


def run(browser, url, business_date, reviewed_hash=None):
    if business_date != datetime.now(SHANGHAI).date().isoformat():
        raise Stop('historical_parameter_ui_not_configured','Historical UI parameters are not deployed')
    find_window(browser,url)
    snapshot = browser.wait(lambda s: login_required(s) or
                            any(n['own_text'] in ('Tools','效率','Sign In Now','立即登录','登录') or
                                (n['tag']=='iframe' and n['id']=='excelIde') for n in s['nodes']))
    require_login_if_visible(snapshot)
    if not any(n['tag']=='iframe' and n['id']=='excelIde' for n in snapshot['nodes']):
        for attempt in range(2):
            click_navigation(browser,('Tools','效率'))
            try:
                snapshot = browser.wait(lambda s:any(n['own_text'] in ('高级开发','Advanced Development') for n in s['nodes']),seconds=10)
                break
            except Stop as exc:
                # Initial load may reset the ribbon to Home. Re-selecting a menu is safe.
                if exc.code!='page_timeout' or attempt==1:
                    raise
        browser.click(one(snapshot['nodes'],lambda n:n['tag']=='button' and n['rendered_text'].strip() in ('高级开发','Advanced Development')))
        snapshot = browser.wait(lambda s:any(n['own_text'] in EDITOR_LABELS for n in s['nodes']))
        browser.click(one(snapshot['nodes'],lambda n:n['own_text'] in EDITOR_LABELS))
        browser.wait(lambda s:any(n['tag']=='iframe' and n['id']=='excelIde' for n in s['nodes']))
    snapshot = browser.wait(lambda s:any(n['own_text'] in ('文档共享脚本','Document Shared Scripts') for n in s['nodes']),FRAME)
    matches, heading = shared_script(snapshot)
    if not matches:
        # Expanded groups may still be loading; only toggle an observed closed group.
        if shared_collapsed(snapshot):
            browser.click(heading,FRAME)
        snapshot = browser.wait(lambda s:bool(shared_script(s)[0]),FRAME)
        matches, _ = shared_script(snapshot)
    browser.click(one(matches,lambda n:True),FRAME)
    browser.wait(lambda s:any(n['tag']=='textarea' and has_class(n,'inputarea') for n in s['nodes']),FRAME)
    result = browser.command('copy-editor','textarea.inputarea','source-'+str(time.time_ns())+'.json','--frame',FRAME)
    actual = read(result['local_artifact'])['source'].replace('\r\n','\n').rstrip('\n')
    expected = (ROOT/'airsheet/reservation_day_reader.js').read_text(encoding='utf-8').replace('\r\n','\n').rstrip('\n')
    if actual != expected:
        raise Stop('shared_source_changed','Shared source differs; do not run or overwrite it')
    started = time.time()
    snapshot = browser.snapshot(FRAME)
    browser.click(one(snapshot['nodes'],lambda n:n['id']=='run-script'),FRAME)
    snapshot = browser.wait(lambda s:execution_finished(s,started),FRAME,45)
    # WPS can update the completion row just after the final frame.
    snapshot = browser.wait(lambda s:any(has_class(n,'concent') and n['rendered_text']=='执行完毕' for n in s['nodes']),FRAME,10)
    lines = [n['rendered_text'] for n in snapshot['nodes'] if has_class(n,'concent')]
    begins = [s for s in lines if s.startswith('WY_BEGIN ')]
    if len(begins) != 1:
        raise Stop('mixed_execution','Expected one complete execution')
    run_id = begins[0].split()[1]
    if not started-5 <= int(run_id.removeprefix('wps-'))/1000 <= time.time()+5:
        raise Stop('stale_execution','Log belongs to another execution')
    logs_path = write(browser.folder/(run_id+'-logs.json'),lines)
    payload = decode_frames(lines,run_id)
    block = payload.get('matches',[{}])[0] if len(payload.get('matches',[])) == 1 else {}
    digest = hashlib.sha256(block.get('instructions','').encode('utf-8')).hexdigest()
    reservation = reservation_from_payload(payload,business_date,browser.evidence+[str(logs_path.relative_to(ROOT))],
                                            instructions_reviewed=bool(reviewed_hash and digest == reviewed_hash))
    return {'date':business_date,'timezone':'Asia/Shanghai','observed_at':payload['generated_at'],
            'run_id':run_id,'reservation':reservation,'instructions_sha256':digest}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True,type=private_path)
    p.add_argument('--run',required=True)
    args=p.parse_args()
    if not args.run.replace('-','').replace('_','').isalnum():
        raise ValueError('Invalid run label')
    config=read(args.config)
    browser=Browser('local/wps-reading/'+args.run)
    with browser.reserved():
        browser.command('start')
        observation=run(browser,config['wps_url'],datetime.now(SHANGHAI).date().isoformat(),config.get('reviewed_instructions_sha256'))
    path=write(browser.folder/'observation.json',observation)
    print(json.dumps({'status':observation['reservation']['status'],'count':len(observation['reservation']['articles']),
           'output':str(path.relative_to(ROOT))},ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (Stop,ValueError,OSError,KeyError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code',type(exc).__name__),'detail':'See private evidence; no replay'}))
        sys.exit(2)
