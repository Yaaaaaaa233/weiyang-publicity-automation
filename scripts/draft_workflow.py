"""Bounded Xiumi draft operations and evidence-derived preservation checks.

Only independent copies are edited. Unsupported structures yield agent work requests.
Unknown save-as results are never replayed. No hidden platform APIs.
"""
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

try:
    from .check_runtime import Stop, Browser, ROOT, read, write, private_path, one, has_class
    from .daily_run import save_state
except ImportError:
    from check_runtime import Stop, Browser, ROOT, read, write, private_path, one, has_class
    from daily_run import save_state

TITLE = 'input[placeholder="请输入图文的标题"]'
BAD_SEPARATOR = re.compile(r'\s*[|｜]\s*')
TITLE_SEPARATOR = re.compile(r'\s*[|｜丨]\s*')
CREDIT = re.compile(r'^(策划|文案|文字|供稿|编辑|排版|摄影|审核|责编|审校)\s*[|｜丨]')

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def paper_id(url):
    p=urlsplit(url)
    match=re.fullmatch(r'/paper/for/(\d+)(?:/cube/\d+)?',p.fragment)
    if p.scheme!='https' or p.hostname!='xiumi.us' or p.username or p.password or not match:
        raise Stop('draft_location_invalid','Expected an observed Xiumi editor URL')
    return match[1]

def editor_url(identifier):
    if not isinstance(identifier,str) or not identifier.isdigit():
        raise ValueError('Numeric platform ID required')
    return 'https://xiumi.us/studio/v5#/paper/for/'+identifier+'/cube/0'

def content(snapshot):
    if snapshot.get('nodes_truncated') is not False or snapshot.get('text_truncated') is not False:
        raise Stop('body_truncated','Collect complete body evidence')
    identifier=paper_id(snapshot['url'])
    nodes=snapshot['nodes']
    vessel=one(nodes,lambda n:has_class(n,'tn-page-vessel'),'body_vessel_ambiguous')
    if vessel.get('rendered_text_truncated'):
        raise Stop('body_truncated','Body text truncated')
    title=one(nodes,lambda n:n.get('tag')=='input' and n.get('placeholder')=='请输入图文的标题')
    if not title.get('value'):
        raise Stop('draft_title_missing','Wait for editor metadata')
    children=[n for n in nodes if n.get('selector','').startswith(vessel['selector']+' > ')]
    r=vessel['rect']
    images=[i for i in snapshot.get('images',[]) if r['x']<=i['rect']['x']<r['x']+r['width'] and r['y']<=i['rect']['y']<r['y']+r['height']]
    if any(not i.get('complete') or i.get('natural_width',0)<=0 or i.get('natural_height',0)<=0 for i in images):
        raise Stop('body_images_loading','Wait for body images')
    paragraphs=[{'text':n.get('paragraph_text'), 'style':n.get('inline_style','')} for n in children if n.get('tag')=='p']
    if any(n.get('paragraph_text_truncated') for n in children):
        raise Stop('body_truncated','Body paragraph truncated')
    # Image sources are private evidence; runtime stdout never includes them.
    image_values=[{'src':i['src'],'width':i['natural_width'],'height':i['natural_height']} for i in images]
    backgrounds=[{'style':n.get('inline_style','')} for n in children if 'background-image:' in n.get('inline_style','')]
    sections=[n for n in children if has_class(n,'tn-comp-top-level')]
    cover_nodes=[n for n in nodes if has_class(n,'image') and has_class(n,'tn-lighting-box') and 'background-image:' in n.get('inline_style','')]
    ancestors=[n for n in nodes if title['selector'].startswith(n['selector']+' > ') and
               not vessel['selector'].startswith(n['selector']+' > ') and
               any(c['selector'].startswith(n['selector']+' > ') for c in cover_nodes)]
    metadata=max(ancestors,key=lambda n:len(n['selector'])) if ancestors else None
    covers=[c.get('inline_style','') for c in cover_nodes if metadata and c['selector'].startswith(metadata['selector']+' > ')]
    return {'cover_styles':covers,'id':identifier,'title':title['value'],'text':vessel.get('rendered_text',''),
            'paragraphs':paragraphs,'images':image_values,'backgrounds':backgrounds,
            'head_style':sections[0].get('inline_style','') if sections else None,
            'head_spacing':sections[0].get('component_spacing') if sections else None,
            'vessel_bottom':r['y']+r['height'],
            'tail_bottom':images[-1]['rect']['y']+images[-1]['rect']['height'] if images else None}

def body_signature(value):
    return fingerprint({k:value[k] for k in ('text','paragraphs','images','backgrounds')})

def snapshot_body(browser, identifier):
    def ready(s):
        try:
            return content(s)['id']==identifier
        except Stop as exc:
            if exc.code not in ('body_images_loading','draft_title_missing','body_vessel_ambiguous','draft_location_invalid'):
                raise
            return False
    first=browser.wait(ready)
    previous=content(first)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        current_snapshot=browser.snapshot(); current=content(current_snapshot)
        if current['id']==identifier and body_signature(current)==body_signature(previous) and current['title']==previous['title']:
            return current_snapshot,current
        previous=current;time.sleep(.4)
    raise Stop('body_unstable','Body did not settle')

def observe(browser, candidate):
    if paper_id(candidate['url']) != candidate['id']:
        raise Stop('draft_identity_changed','Candidate location differs from observed ID')
    opened=browser.command('new-window',candidate['url'])
    snapshot,value=snapshot_body(browser,candidate['id'])
    if value['title']!=candidate['title']:
        raise Stop('draft_identity_changed','Candidate title changed since library scan')
    path=write(browser.folder/('body-'+candidate['id']+'-'+uuid.uuid4().hex+'.json'),value)
    browser.evidence.append(str(path.relative_to(ROOT)))
    browser.command('screenshot','body-'+candidate['id']+'-'+uuid.uuid4().hex+'.png')
    browser.command('close-window',opened['handle'],'--expect-url',snapshot['url'],'--return-to',opened['previous_handle'])
    return value,str(path.relative_to(ROOT))

def format_issues(value, rules):
    """Require evidenced head/tail identities. Dimensions alone never confirm the artwork."""
    issues=[]; images=value['images']
    if TITLE_SEPARATOR.sub('丨',value['title'])!=value['title']:issues.append('title_separator')
    if normalized_credits(value['text'])!=value['text']:issues.append('credit_separator')
    if not images or images[0]['src'] not in rules.get('head_sources',[]):issues.append('head_identity_or_position')
    if not images or images[-1]['src'] not in rules.get('tail_sources',[]):issues.append('tail_identity_or_position')
    spacing=value.get('head_spacing')
    style=value.get('head_style') or ''
    explicit=all(re.search(r'margin-'+side+r':\s*0(?:px)?(?:;|$)',style) for side in ('top','bottom'))
    if not explicit and spacing!={'margin_top':'0px','margin_bottom':'0px'}:issues.append('head_spacing_unverified')
    if value['tail_bottom'] is None or abs(value['vessel_bottom']-value['tail_bottom'])>1:issues.append('trailing_component_unverified')
    if len(value.get('cover_styles',[]))!=1:issues.append('cover_unverified')
    if not rules.get('evidence_paths'):issues.append('format_rule_evidence_missing')
    return issues

def normalized_credits(text):
    # Only the trailing contiguous credit block is eligible; body pipes remain literal.
    lines=text.split('\n')
    for index in range(len(lines)-1,-1,-1):
        line=lines[index]
        if not line.strip():continue
        if not CREDIT.match(line.strip()):break
        lines[index]=TITLE_SEPARATOR.sub('丨',line)
    return '\n'.join(lines)


def preservation(before, after, rules=None):
    """Only known credit separators and additive, evidenced head/tail images may change."""
    if rules is None:
        return all(before[k]==after[k] for k in ('text','paragraphs','images','backgrounds'))
    if normalized_credits(before['text']).strip()!=normalized_credits(after['text']).strip():return False
    def ps(value):
        result=[{'text':normalized_credits(p['text'] or ''),'style':p['style']} for p in value['paragraphs']]
        while result and not result[0]['text'].strip():result.pop(0)
        while result and not result[-1]['text'].strip():result.pop()
        return result
    if ps(before)!=ps(after) or before['backgrounds']!=after['backgrounds']:return False
    images=list(after['images']);original=before['images']
    if images and images[0]['src'] in rules.get('head_sources',[]) and (not original or original[0]!=images[0]):images.pop(0)
    if images and images[-1]['src'] in rules.get('tail_sources',[]) and (not original or original[-1]!=images[-1]):images.pop()
    return original==images


class Drafts:
    def __init__(self, browser, folder, account, day, marker, rules, known_ids=None):
        self.browser=browser;self.folder=private_path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        if not marker or len(marker)>60 or not marker.startswith(('【自动整理','【全流程验证','【定时验证','【排版验证','【同步验证')):
            raise ValueError('Use a short managed-copy marker so generated copies stay excluded from candidates')
        self.marker=marker;self.rules=rules;self.known_ids=set(known_ids or [])
        for p in rules.get('evidence_paths',[]):
            if not private_path(p).is_file():raise ValueError('Missing format rule evidence')
        self.index_path=ROOT/'data/draft-workflow'/ (fingerprint({'account':account,'date':day})+'.json')
        self.index=read(self.index_path) if self.index_path.exists() else {'date':day,'account':account,'copies':{}}

    def persist(self):save_state_mkdir(self.index_path,self.index)

    def prepare(self, article, candidate):
        source=candidate['id'];record=self.index['copies'].get(source)
        if record and record['row']!=article['row']:
            raise Stop('copy_mapping_conflict','Source was already assigned to another reservation')
        if not record:
            before,path=observe(self.browser,candidate)
            record={'row':article['row'],'source_id':source,'source_title':candidate['title'],
                    'expected_title':self.marker+TITLE_SEPARATOR.sub('丨',candidate['title']),
                    'baseline':path,'known_ids_before_copy':sorted(self.known_ids),'phase':'baseline_saved','copy_id':None,'copy_reserved':False}
            self.index['copies'][source]=record;self.persist()
        baseline=read(private_path(record['baseline']))
        if record['copy_reserved'] and not record['copy_id']:
            raise Stop('copy_outcome_unknown','Reconcile actual library; never repeat save-as')
        windows=self.browser.command('windows')['windows']
        handle=self.index.get('editor_handle')
        if handle and any(w['handle']==handle for w in windows):
            self.browser.command('switch-window',handle)
        else:
            opened=self.browser.command('new-window',editor_url(record['copy_id'] or source))
            self.index['editor_handle']=opened['handle'];self.persist()
        if record['copy_id']:
            self.browser.command('open',editor_url(record['copy_id']))
            self.browser.command('reload')
            snapshot,value=snapshot_body(self.browser,record['copy_id'])
        else:
            if record['copy_reserved']:
                raise Stop('copy_outcome_unknown','Reconcile actual library and attach the existing copy; never repeat save-as')
            self.browser.command('open',editor_url(source))
            snapshot,value=snapshot_body(self.browser,source)
            if body_signature(value)!=body_signature(baseline) or value['title']!=candidate['title']:
                raise Stop('source_changed','Source changed since baseline')
            more=one(snapshot['nodes'],lambda n:n.get('own_text')=='更多' and n.get('tag')=='span')
            self.browser.click(more)
            snapshot=self.browser.snapshot()
            saveas=one(snapshot['nodes'],lambda n:n.get('tag')=='a' and n.get('own_text')=='另存一个图文','save_as_unavailable')
            record.update(copy_reserved=True,phase='copy_intent');self.persist()
            self.browser.click(saveas)
            def created(s):
                try:return paper_id(s['url'])!=source
                except Stop:return False
            snapshot=self.browser.wait(created)
            identifier=paper_id(snapshot['url'])
            # Record ID immediately, before any rename/save operation.
            record.update(copy_id=identifier,phase='copy_created');self.persist()
            snapshot,value=snapshot_body(self.browser,identifier)
        if not preservation(baseline,value,self.rules):
            raise Stop('copy_body_changed','Copy body or images differ from baseline')
        changed=False
        if value['title']!=record['expected_title']:
            if record['phase']=='save_intent':
                raise Stop('save_outcome_unknown','Saved title is not confirmed; do not repeat a rename')
            if value['title']!=baseline['title']:
                raise Stop('copy_title_changed','Unexpected title; do not overwrite')
            changed=True
            self.browser.command('focus',TITLE);self.browser.command('key','SelectAll')
            self.browser.command('type',record['expected_title']);self.browser.command('key','Tab')
        if changed or record['phase']=='repairing':
            if record['phase']=='save_intent':
                raise Stop('save_outcome_unknown','Observe persisted copy before replaying a save')
            snapshot=self.browser.snapshot()
            save=one(snapshot['nodes'],lambda n:n.get('own_text')=='保存' and n.get('tag')=='span')
            record['phase']='save_intent';self.persist()
            self.browser.click(save)
        self.browser.command('reload')
        snapshot,value=snapshot_body(self.browser,record['copy_id'])
        if value['title']!=record['expected_title'] or not preservation(baseline,value,self.rules):
            raise Stop('copy_save_unverified','Saved copy does not match baseline')
        # Reopen in a separate editor, not just an in-memory view of the saved draft.
        opened=self.browser.command('new-window',editor_url(record['copy_id']))
        self.browser.command('reload')
        reopened_snapshot,reopened=snapshot_body(self.browser,record['copy_id'])
        if reopened['title']!=record['expected_title'] or not preservation(value,reopened,self.rules):
            raise Stop('copy_reopen_unverified','Cold reopen differs from saved copy')
        self.browser.command('close-window',opened['handle'],'--expect-url',reopened_snapshot['url'],'--return-to',opened['previous_handle'])
        issues=format_issues(reopened,self.rules)
        evidence=write(self.folder/('verification-'+source+'-'+uuid.uuid4().hex+'.json'),
                       {'source_id':source,'copy_id':record['copy_id'],'saved_reopened':True,
                        'body_images_verified':True,'format_issues':issues,'observed':reopened})
        self.browser.evidence.append(str(evidence.relative_to(ROOT)))
        if not issues and record.get('repair_intent'):
            record['resolved_repair_intent']=record.pop('repair_intent')
        record.update(phase='needs_format' if issues else 'verified',format_issues=issues,
                      verification=str(evidence.relative_to(ROOT)));self.persist()
        return {'row':article['row'],'source_id':source,'draft_id':record['copy_id'],
                'source_title':record['source_title'],'sync_title':record['expected_title'],
                'is_headline':article['is_headline'],'saved_reopened':True,'body_images_verified':True,
                'format_verified':not issues,'format_issues':issues,
                'evidence_paths':[record['baseline'],record['verification'],*self.rules['evidence_paths']]}

    def action(self, source, action):
        """One fresh, scoped GUI repair. The agent observes again before the next action."""
        record=self.index['copies'][source]
        if record.get('repair_intent'):
            raise Stop('repair_outcome_unknown','Inspect pending repair before attempting another action')
        if not record['copy_id'] or record['phase'] not in ('needs_format','repairing'):
            raise Stop('repair_not_pending','Only known copies pending format repair can be edited')
        handle=self.index.get('editor_handle')
        if handle:self.browser.command('switch-window',handle)
        snapshot,value=snapshot_body(self.browser,record['copy_id'])
        if fingerprint(value)!=action.get('before_sha256'):
            raise Stop('repair_stale','Repair refers to another observation; read again')
        node=one(snapshot['nodes'],lambda n:n.get('selector')==action.get('selector'),'repair_target_ambiguous')
        kind=action.get('kind')
        def reserve():
            record['repair_intent']={'kind':kind,'before_sha256':action['before_sha256']};self.persist()
        if kind=='click':
            # Menus/material selectors only. No destructive, auth or sync controls.
            allowed={'我的图库','头尾图','头图.gif','尾图.gif','组件定位','前插空行','后插空行','图片'}
            if node.get('rendered_text','').strip() not in allowed:
                raise Stop('repair_action_unsupported','Use a verified component operation instead of arbitrary GUI clicks')
            reserve();self.browser.click(node)
        elif kind=='select_component':
            vessel=one(snapshot['nodes'],lambda n:has_class(n,'tn-page-vessel'))
            if not node.get('selector','').startswith(vessel['selector']+' > ') or not has_class(node,'tn-comp-top-level'):
                raise Stop('repair_action_unsupported','Select a top-level component in the known copy')
            reserve();self.browser.click(node)
        elif kind=='delete_empty_component':
            vessel=one(snapshot['nodes'],lambda n:has_class(n,'tn-page-vessel'))
            if not node.get('selector','').startswith(vessel['selector']+' > ') or not has_class(node,'tn-comp-top-level'):
                raise Stop('repair_action_unsupported','Deletion must target a top-level component in this copy')
            self.browser.command('check-empty',node['selector'])
            # Only edge whitespace around the tail may be removed; preserve inner body whitespace.
            image_nodes=[n for n in snapshot['nodes'] if n.get('tag')=='img' and n.get('selector','').startswith(vessel['selector']+' > ')]
            tail=image_nodes[-1] if image_nodes else None
            tops=[n for n in snapshot['nodes'] if has_class(n,'tn-comp-top-level') and n.get('selector','').startswith(vessel['selector']+' > ')]
            tail_tops=[n for n in tops if tail and tail['selector'].startswith(n['selector']+' > ')]
            if len(tail_tops)!=1 or abs(tops.index(node)-tops.index(tail_tops[0]))!=1:
                raise Stop('repair_action_unsupported','Only immediate top-level neighbors of the tail are eligible')
            reserve();self.browser.click(node)
            toolbar=self.browser.snapshot()
            delete=one(toolbar['nodes'],lambda n:n.get('own_text')=='删除' and n.get('tag') in ('a','span','button'),'delete_control_ambiguous')
            self.browser.click(delete)
        elif kind=='replace_credit':
            text=node.get('rendered_text','')
            children=[n for n in snapshot['nodes'] if n.get('selector','').startswith(node['selector']+' > ')]
            if not node.get('editable') or not CREDIT.match(text.strip()) or any(n.get('tag') not in ('p','br') for n in children):
                raise Stop('complex_credit','Mixed styles or complex credits require a reviewed operation')
            replacement=normalized_credits(text)
            if replacement==text:raise Stop('repair_unnecessary','Credit already conforms')
            reserve();self.browser.command('focus',node.get('stable_selector') or node['selector'])
            self.browser.command('key','SelectAll');self.browser.command('type',replacement);self.browser.command('key','Tab')
        elif kind=='zero_spacing':
            parents=[n for n in snapshot['nodes'] if node['selector'].startswith(n['selector']+' > ')]
            if node.get('tag')!='input' or not any(n.get('rendered_text','').strip() in ('组前距','组后距') for n in parents):
                raise Stop('repair_action_unsupported','Only verified group spacing controls may be set to zero')
            reserve();self.browser.command('focus',node.get('stable_selector') or node['selector'])
            self.browser.command('key','SelectAll');self.browser.command('type','0');self.browser.command('key','Tab')
        else:raise Stop('repair_action_unsupported','Unsupported repair action')
        snapshot,value=snapshot_body(self.browser,record['copy_id'])
        baseline=read(private_path(record['baseline']))
        if not preservation(baseline,value,self.rules):
            raise Stop('repair_preservation_failed','Repair changed protected body content; retain intent and baseline')
        mutated=kind in ('replace_credit','zero_spacing','delete_empty_component') or (kind=='click' and node.get('rendered_text','').strip() in ('头图.gif','尾图.gif','前插空行','后插空行'))
        if mutated:
            save=one(snapshot['nodes'],lambda n:n.get('own_text')=='保存' and n.get('tag')=='span')
            self.browser.click(save);self.browser.command('reload')
            _,persisted=snapshot_body(self.browser,record['copy_id'])
            if not preservation(value,persisted) or value['title']!=persisted['title']:
                raise Stop('repair_save_unverified','Saved repair differs from observed body')
            value=persisted
        record['phase']='needs_format';record.pop('repair_intent',None);self.persist()
        path=write(self.folder/('repair-observation-'+uuid.uuid4().hex+'.json'),value)
        record['repair_observation']=str(path.relative_to(ROOT));self.persist()
        return {'observation':record['repair_observation'],'before_sha256':fingerprint(value)}

    def reconcile_repair(self, source):
        """Reobserve persisted copy after an unknown repair; do not replay that action."""
        record=self.index['copies'][source]
        if not record.get('repair_intent'):raise Stop('repair_not_pending','No unknown repair to inspect')
        self.browser.command('open',editor_url(record['copy_id']));self.browser.command('reload')
        _,value=snapshot_body(self.browser,record['copy_id'])
        baseline=read(private_path(record['baseline']))
        if not preservation(baseline,value,self.rules):raise Stop('repair_preservation_failed','Unknown repair changed protected content')
        path=write(self.folder/('repair-reconciliation-'+uuid.uuid4().hex+'.json'),
                   {'copy_id':record['copy_id'],'observed':value,'previous_intent':record['repair_intent'],
                    'outcome':'persisted_state_reobserved; previous action not replayed'})
        record.update(phase='repairing',repair_reconciliation=str(path.relative_to(ROOT)))
        record.pop('repair_intent');self.persist()
        return {'observation':record['repair_reconciliation'],'before_sha256':fingerprint(value)}

    def attach(self, source, identifier):
        """Recover only a known pending save-as; verify title and entire body first."""
        record=self.index['copies'][source]
        if not record['copy_reserved'] or record['copy_id'] or identifier==source:
            raise Stop('copy_recovery_invalid','No unknown copy intent to recover')
        if identifier in record.get('known_ids_before_copy',[]):
            raise Stop('copy_recovery_invalid','Cannot attach a manuscript that existed before save-as')
        if any(r['copy_id']==identifier for r in self.index['copies'].values()):
            raise Stop('copy_recovery_invalid','Copy already belongs to another source')
        self.browser.command('open',editor_url(identifier))
        _,value=snapshot_body(self.browser,identifier);baseline=read(private_path(record['baseline']))
        if value['title'] not in (baseline['title'],record['expected_title']) or not preservation(baseline,value,self.rules):
            raise Stop('copy_recovery_invalid','Recovered copy does not match baseline')
        record.update(copy_id=identifier,phase='copy_recovered');self.persist()

def save_state_mkdir(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);save_state(path,value)
