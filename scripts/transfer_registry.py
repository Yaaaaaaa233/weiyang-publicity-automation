"""Private account/source registry. Reservations survive notification/tag failures."""
import hashlib
try:
    from . import check_runtime as runtime
    from .check_runtime import read, Stop
    from .daily_run import save_state
except ImportError:
    import check_runtime as runtime
    from check_runtime import read, Stop
    from daily_run import save_state


def path(account):
    return runtime.ROOT/'data/transfers'/('account-'+hashlib.sha256(account.encode()).hexdigest()+'.json')


def entries(account):
    p=path(account)
    return read(p) if p.exists() else {}


def reserve(account, run_id, articles):
    """Caller holds full-run lock; commit before composition submission."""
    p=path(account);data=entries(account)
    ids=[i for a in articles for i in (a['source_id'],a['draft_id'])]
    for i in ids:
        if i in data and data[i]['run_id']!=run_id:
            raise Stop('already_transferred_or_reserved','Inspect original transfer; never resubmit in a new run')
    for a in articles:
        for i in (a['source_id'],a['draft_id']):
            data.setdefault(i,{'run_id':run_id,'source_id':a['source_id'],'draft_id':a['draft_id'],
                               'status':'reserved','notification_status':'pending','tag_status':'pending'})
    p.parent.mkdir(parents=True,exist_ok=True);save_state(p,data)


def update(account, run_id, **fields):
    p=path(account);data=entries(account)
    for item in data.values():
        if item['run_id']==run_id:item.update(fields)
    p.parent.mkdir(parents=True,exist_ok=True);save_state(p,data)


def annotate(cards, account):
    data=entries(account)
    return [dict(c,transfer_status=data[c['id']]['status'],transfer_run_id=data[c['id']]['run_id'])
            if c['id'] in data else c for c in cards]
