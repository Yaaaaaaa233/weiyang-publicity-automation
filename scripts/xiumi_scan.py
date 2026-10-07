"""Read the whole all-tag manuscript library; collect candidates without editing drafts."""
import argparse
import json
from datetime import datetime, timedelta, date
from difflib import SequenceMatcher
import re
import sys
import time

try:
    from .check_runtime import Browser, Stop, ROOT, read, write, one, has_class, private_path, find_window
    from .read_wps_reservation import SHANGHAI
    from .draft_identity import conflicts
except ImportError:
    from check_runtime import Browser, Stop, ROOT, read, write, one, has_class, private_path, find_window
    from read_wps_reservation import SHANGHAI
    from draft_identity import conflicts


def parse_page(snapshot, expected_account, require_all=False):
    nodes=snapshot['nodes']
    if any(n['own_text'] in ('登录','登录秀米') for n in nodes) and not any(has_class(n,'nickname') for n in nodes):
        raise Stop('xiumi_login_required','Xiumi needs a user login')
    nickname=one(nodes,lambda n:has_class(n,'nickname'),'xiumi_account_unavailable')
    if nickname['own_text'] != expected_account:
        raise Stop('wrong_xiumi_account','Expected receipt account is not active')
    if require_all:
        all_tag=one(nodes,lambda n:n['tag']=='a' and n['own_text']=='全部','tag_scope_changed')
        search=one(nodes,lambda n:n['tag']=='input' and n['placeholder']=='输入标题或描述','tag_scope_changed')
        if 'active' not in all_tag.get('parent_class','').split() or search.get('value')!='':
            raise Stop('tag_scope_changed','All tags and empty search must remain selected')
    total=one(nodes,lambda n:bool(re.fullmatch(r'已保存图文:\s*\d+',n['own_text'])),'library_total_unknown')
    total=int(total['own_text'].split(':')[1])
    cards=[]
    unavailable=[]
    for node in nodes:
        if not has_class(node,'show-item'):
            continue
        children=[n for n in nodes if n['selector'].startswith(node['selector']+' > ')]
        links=[n for n in children if n['tag']=='a' and re.fullmatch(r'https://xiumi\.us/studio/v5#/paper/for/\d+',n.get('href') or '')]
        if not links:
            if any(has_class(n,'restore') for n in children):
                title=one(children,lambda n:has_class(n,'title') and n['tag']=='div' and not has_class(n,'textarea-preview'))
                unavailable.append({'title':title['rendered_text'].strip(),'locator':node['selector'],'available':False})
            elif any(has_class(n,'paper') for n in children):
                raise Stop('unreadable_library_card','A manuscript card has no readable location or restore marker')
            continue  # New/sync action cards are not manuscripts.
        link=one(links,lambda n:True)
        title=one(children,lambda n:has_class(n,'title') and n['tag']=='div' and not has_class(n,'textarea-preview'))
        if title.get('rendered_text_truncated'):
            raise Stop('truncated_title','A manuscript title was truncated')
        date_nodes=[n['own_text'] for n in children if re.fullmatch(r'\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}:\d{2}',n.get('own_text',''))]
        if len(date_nodes)!=1:
            raise Stop('draft_time_unreadable','Cannot establish manuscript date window')
        try:
            displayed_date=datetime.strptime(date_nodes[0],'%Y年%m月%d日 %H:%M').date().isoformat()
        except ValueError:
            raise Stop('draft_time_unreadable','Invalid manuscript display date')
        cards.append({'display_time_raw':date_nodes[0], 'display_date':displayed_date, 'id':link['href'].rsplit('/',1)[1],'title':title['rendered_text'].strip(),'url':link['href']})
    pagers=[n for n in nodes if n['tag']=='ul' and has_class(n,'pagination')]
    if len(pagers) != 1:
        if total==0 and not cards:
            return {'total':0,'page':1,'cards':[],'next':None}
        raise Stop('pagination_unknown','Cannot prove complete library traversal')
    pager=pagers[0]
    links=[n for n in nodes if n['tag']=='a' and n['selector'].startswith(pager['selector']+' > ')]
    active_link=one(links,lambda n:'active' in n.get('parent_class','').split(),'pagination_unknown')
    if not active_link['own_text'].isdigit():
        raise Stop('pagination_unknown','Active page number is not visible')
    following=[n for n in links if n['own_text']=='›' and 'disabled' not in n.get('parent_class','').split()]
    if len(following)>1:
        raise Stop('pagination_unknown','Multiple next controls')
    page=int(active_link['own_text'])
    for i,card in enumerate(unavailable):
        card['id']='unavailable-slot-'+str(page)+'-'+str(i)
        card['url']=None
    return {'total':total,'page':page,'cards':cards+unavailable,'next':following[0] if following else None}


def catalog(browser, url, account, max_pages=100):
    find_window(browser,url)
    browser.command('reload')  # The library is read-only; refresh cached counts and card metadata.
    snapshot=browser.wait(lambda s:any(n['own_text'] in ('登录','登录秀米') for n in s['nodes']) or
                          (any(has_class(n,'nickname') for n in s['nodes']) and
                           any(re.fullmatch(r'已保存图文:\s*\d+',n['own_text']) for n in s['nodes'])))
    parse_page(snapshot,account)
    control=one(snapshot['nodes'],lambda n:n['tag']=='input' and n['placeholder']=='输入标题或描述')
    browser.command('focus',control['selector']); browser.command('key','SelectAll')
    browser.command('key','Backspace'); browser.command('key','Enter')
    # Observe the cleared field and active page before accepting stable page data.
    def cleared(s):
        controls=[n for n in s['nodes'] if n['tag']=='input' and n['placeholder']=='输入标题或描述']
        return len(controls)==1 and controls[0]['value']=='' and parse_page(s,account)['page']==1
    snapshot=browser.wait(cleared)
    # An empty-search submission can reset the list filter to "untagged".
    # Select all AFTER clearing search, then verify its rendered selected state.
    all_tag=one(snapshot['nodes'],lambda n:n['tag']=='a' and n['own_text']=='全部')
    browser.click(all_tag)
    def all_selected(s):
        tags=[n for n in s['nodes'] if n['tag']=='a' and n['own_text']=='全部']
        return len(tags)==1 and 'active' in tags[0].get('parent_class','').split() and parse_page(s,account)['page']==1
    snapshot=browser.wait(all_selected)
    collected={}
    total=None
    for expected_page in range(1,max_pages+1):
        tags=[n for n in snapshot['nodes'] if n['tag']=='a' and n['own_text']=='全部']
        if len(tags)!=1 or 'active' not in tags[0].get('parent_class','').split():
            raise Stop('tag_scope_changed','All-tag selection was lost')
        first=parse_page(snapshot,account,True)
        deadline=time.monotonic()+20
        while True:
            time.sleep(.7)
            second_snapshot=browser.snapshot()
            second=parse_page(second_snapshot,account,True)
            if (first['page'],first['cards'],first['total']) == (second['page'],second['cards'],second['total']):
                break
            if time.monotonic()>=deadline:
                raise Stop('library_changing','Library did not settle; cannot infer completeness')
            first=second
        if second['page'] != expected_page or (total is not None and second['total']!=total):
            raise Stop('library_changing','Library count or page changed during scan')
        total=second['total']
        for card in second['cards']:
            if card['id'] in collected:
                raise Stop('library_changing','Repeated draft across pages')
            collected[card['id']]=card
        if not second['next']:
            if len(collected)!=total:
                write(browser.folder/('partial-catalog-'+str(time.time_ns())+'.json'),list(collected.values()))
                raise Stop('incomplete_library',f'Collected count {len(collected)} differs from displayed total {total}')
            return list(collected.values())
        browser.click(second['next'])
        snapshot=browser.wait(lambda s:parse_page(s,account)['page']==expected_page+1)
    raise Stop('page_limit','Library scan exceeded bounded page limit')


def recent_cards(cards, as_of):
    # Calendar dates avoid assuming the platform's unverified hour/timezone semantics.
    lower=as_of-timedelta(days=13)
    selected=[]
    for card in cards:
        if not card.get('available',True):
            continue
        try:
            shown=date.fromisoformat(card['display_date'])
        except (KeyError,ValueError,TypeError):
            raise Stop('draft_time_unreadable','Cannot establish manuscript date window')
        if shown>as_of:
            raise Stop('draft_time_unreadable','Future manuscript display date')
        if shown>=lower:
            selected.append(card)
    return selected


def match_candidates(articles, cards):
    matches=[]
    def normalize(s):
        return re.sub(r'[\W_]+','',s,flags=re.UNICODE)
    for article in articles:
        excluded=[dict(c,exclusion_reasons=conflicts(article,c)) for c in cards if conflicts(article,c)]
        eligible=[c for c in cards if not conflicts(article,c)]
        exact=[c for c in eligible if c['title']==article['title']]
        if exact:
            candidates=[dict(c,identity_confirmed=len(exact)==1 and c.get('available',True),
                             identity_reason='常用账号全标签全页读取，完整标题唯一一致' if len(exact)==1 and c.get('available',True) else '') for c in exact]
        else:
            # Similarity only locates review candidates; never confirms identity.
            target=normalize(article['title'])
            candidates=[dict(c,identity_confirmed=False) for c in eligible
                        if not c['title'].startswith(('【定时验证','【同步验证','【排版验证'))
                        and target and normalize(c['title']) and (target in normalize(c['title']) or normalize(c['title']) in target
                        or SequenceMatcher(None,target,normalize(c['title'])).ratio()>=.45)]
        matches.append({'row':article['row'],'candidates':candidates,'excluded_candidates':excluded})
    return matches


def record_inventory(cards, account, observed_at):
    import hashlib
    key=hashlib.sha256(account.encode('utf-8')).hexdigest()[:24]
    path=private_path('local/xiumi-inventory/'+key+'.json')
    prior=read(path) if path.exists() else {'account':account,'cards':{}}
    initial=not path.exists()
    for card in cards:
        if not card.get('available',True):
            continue
        entry=prior['cards'].setdefault(card['id'], {
            'first_seen_at':observed_at,'initial_inventory':initial,
            'first_display_time_raw':card.get('display_time_raw'),
            'first_display_date':card.get('display_date')})
        entry.update(last_seen_at=observed_at,last_display_date=card.get('display_date'),
                     last_display_time_raw=card.get('display_time_raw'))
        card['first_seen_at']=entry['first_seen_at']
        card['initial_inventory']=entry['initial_inventory']
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(prior,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def run(browser, config, articles):
    cards=catalog(browser,config['xiumi_library_url'],config['xiumi_account'],config.get('max_library_pages',100))
    as_of=datetime.now(SHANGHAI).date()
    eligible=recent_cards(cards,as_of)
    record_inventory(cards,config['xiumi_account'],datetime.now(SHANGHAI).isoformat())
    catalog_path=write(browser.folder/('catalog-'+str(time.time_ns())+'.json'),cards)
    return {'status':'ok','account_confirmed':True,'search_complete':True,
            'candidate_window':{'days':14,'start_date':(as_of-timedelta(days=13)).isoformat(),'end_date':as_of.isoformat(),'basis':'platform_display_date','eligible_count':len(eligible),'outside_window':'manual_confirmation_only'},
            'observed_at':datetime.now(SHANGHAI).isoformat(),'catalog_path':str(catalog_path.relative_to(ROOT)),
            'evidence_paths':browser.evidence+[str(catalog_path.relative_to(ROOT))],
            'matches':match_candidates(articles,eligible),
            'matching_policy':'unique exact title auto-matched; semantic candidates require evidenced agent decisions'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True,type=private_path)
    p.add_argument('--observation',required=True,type=private_path)
    p.add_argument('--run',required=True)
    args=p.parse_args()
    if not args.run.replace('-','').replace('_','').isalnum():
        raise ValueError('Invalid run label')
    observation=read(args.observation)
    browser=Browser('local/xiumi-scan/'+args.run)
    with browser.reserved():
        browser.command('start')
        data=run(browser,read(args.config),observation['reservation']['articles'])
    write(browser.folder/'result.json',data)
    print(json.dumps({'status':data['status'],'reservations':len(data['matches']),'output':str(browser.folder.relative_to(ROOT))},ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (Stop,ValueError,OSError,KeyError) as exc:
        print(json.dumps({'status':'failed','code':getattr(exc,'code',type(exc).__name__),'detail':'Inspect private evidence'}))
        sys.exit(2)
