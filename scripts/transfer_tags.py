"""Apply the exact managed label through an observed, private UI adapter.

No API/session scraping. Unknown tag layouts stop without replaying sync.
"""
try:
    from .check_runtime import one, has_class, Stop, find_window
    from . import xiumi_scan
except ImportError:
    from check_runtime import one, has_class, Stop, find_window
    import xiumi_scan

LABEL='转存好'


def inside(node, parent):
    return node['selector'].startswith(parent['selector']+' > ')


def card(snapshot, identifier):
    link=one(snapshot['nodes'],lambda n:(n.get('href') or '').endswith('/paper/for/'+identifier),'tag_card_ambiguous')
    return one(snapshot['nodes'],lambda n:has_class(n,'show-item') and inside(link,n),'tag_card_ambiguous')


def tagged(snapshot, identifier):
    parent=card(snapshot,identifier)
    return any(inside(n,parent) and n.get('own_text')==LABEL and not has_class(n,'action-btn-small')
               for n in snapshot['nodes'])


def list_has_label(snapshot, panel, list_class):
    tag_list=one(snapshot['nodes'],lambda n:inside(n,panel) and n.get('tag')=='ul' and has_class(n,list_class),
                 'tag_list_unavailable')
    return any(inside(n,tag_list) and n.get('tag') in ('li','span') and
               (n.get('rendered_text') or '').strip()==LABEL for n in snapshot['nodes'])


def label_choice(snapshot, panel, adapter):
    all_tags=one(snapshot['nodes'],lambda n:inside(n,panel) and n.get('tag')=='ul' and
                 has_class(n,adapter['choice_list_class']),'tag_list_unavailable')
    choices=[n for n in snapshot['nodes'] if inside(n,all_tags) and n.get('tag')=='li' and
             has_class(n,adapter['choice_class']) and (n.get('rendered_text') or '').strip()==LABEL]
    if len(choices)>1:
        raise Stop('tag_choice_ambiguous','More than one exact transfer label is available')
    return choices[0] if choices else None


def lookup(browser, account, title, identifier):
    browser.command('reload')
    snapshot=browser.wait(lambda s:any(has_class(n,'nickname') or n.get('own_text') in ('登录','登录秀米') for n in s['nodes']))
    names=[n for n in snapshot['nodes'] if has_class(n,'nickname')]
    if not names:raise Stop('xiumi_login_required','Tag account requires login')
    nickname=one(names,lambda n:True)
    if nickname['own_text']!=account:raise Stop('wrong_xiumi_account','Wrong tag account')
    search=one(snapshot['nodes'],lambda n:n.get('placeholder')=='输入标题或描述')
    browser.command('focus',search['selector']);browser.command('key','SelectAll')
    browser.command('type',title);browser.command('key','Enter')
    snapshot=browser.snapshot()
    scope=one(snapshot['nodes'],lambda n:n.get('tag')=='a' and n.get('own_text')=='全部')
    browser.click(scope)
    def found(s):
        search=one(s['nodes'],lambda n:n.get('placeholder')=='输入标题或描述')
        scope=one(s['nodes'],lambda n:n.get('tag')=='a' and n.get('own_text')=='全部')
        return search.get('value')==title and 'active' in scope.get('parent_class','').split() and any((n.get('href') or '').endswith('/paper/for/'+identifier) for n in s['nodes'])
    return browser.wait(found)


def apply(browser, config, articles, checkpoint):
    adapter=config.get('transfer_tag_ui')
    if not isinstance(adapter,dict) or not adapter.get('evidence_paths'):
        raise Stop('tag_adapter_required','Observe tag editor and configure the private UI adapter; never resubmit')
    try:
        from .check_runtime import private_path
    except ImportError:
        from check_runtime import private_path
    if not all(private_path(p).is_file() for p in adapter['evidence_paths']):
        raise Stop('tag_adapter_required','Tag UI evidence is missing')
    find_window(browser,config['xiumi_library_url'])
    for article in articles:
        for identifier,title in ((article['source_id'],article['source_title']),
                                 (article['draft_id'],article['sync_title'])):
            # One independent library tab; full title search then ID, never list position.
            snapshot=lookup(browser,config['xiumi_account'],title,identifier)
            if tagged(snapshot,identifier):checkpoint(identifier,list(browser.evidence));continue
            parent=card(snapshot,identifier)
            browser.command('hover-element',parent.get('stable_selector') or parent['selector'])
            def tag_button_visible(current):
                target=card(current,identifier)
                return any(inside(n,target) and has_class(n,'action-btn-small') and has_class(n,'tag')
                           for n in current['nodes'])
            snapshot=browser.wait(tag_button_visible)
            parent=card(snapshot,identifier)
            button=one(snapshot['nodes'],lambda n:inside(n,parent) and has_class(n,'action-btn-small') and has_class(n,'tag'))
            browser.click(button)
            snapshot=browser.wait(lambda s:any(has_class(n,adapter['panel_class']) for n in s['nodes']))
            panel=one(snapshot['nodes'],lambda n:has_class(n,adapter['panel_class']))
            if not list_has_label(snapshot,panel,adapter['current_list_class']):
                choice=label_choice(snapshot,panel,adapter)
                if choice is None:
                    # The live dialog creates labels with Enter; never guess a button or toggle.
                    # A newly created label must appear on this piece or in the selectable list.
                    field=one(snapshot['nodes'],lambda n:inside(n,panel) and n.get('placeholder')==adapter.get('new_placeholder') and n.get('tag')=='input')
                    browser.command('focus',field.get('stable_selector') or field['selector'])
                    browser.command('type',LABEL)
                    browser.command('key','Enter')
                    def label_created(s):
                        try:
                            p=one(s['nodes'],lambda n:has_class(n,adapter['panel_class']))
                            return list_has_label(s,p,adapter['current_list_class']) or label_choice(s,p,adapter) is not None
                        except Stop as exc:
                            if exc.code in ('changed_layout','tag_list_unavailable'):return False
                            raise
                    snapshot=browser.wait(label_created)
                    panel=one(snapshot['nodes'],lambda n:has_class(n,adapter['panel_class']))
                    if list_has_label(snapshot,panel,adapter['current_list_class']):
                        choice=None
                    else:
                        choice=label_choice(snapshot,panel,adapter)
                if choice is not None:
                    browser.click(choice)
                    snapshot=browser.wait(lambda s:list_has_label(
                        s,one(s['nodes'],lambda n:has_class(n,adapter['panel_class'])),adapter['current_list_class']))
                    panel=one(snapshot['nodes'],lambda n:has_class(n,adapter['panel_class']))
                if not list_has_label(snapshot,panel,adapter['current_list_class']):
                    raise Stop('tag_selection_unverified','The exact label is not assigned to this manuscript')
            save=one(snapshot['nodes'],lambda n:inside(n,panel) and n.get('own_text')==adapter['save_text'])
            browser.click(save)
            browser.wait(lambda s:not any(has_class(n,adapter['panel_class']) for n in s['nodes']))
            snapshot=lookup(browser,config['xiumi_account'],title,identifier)
            if not tagged(snapshot,identifier):raise Stop('tag_save_unverified','Reobserve tag before retry; never resubmit')
            checkpoint(identifier,list(browser.evidence))
