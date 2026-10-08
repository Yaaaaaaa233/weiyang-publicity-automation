"""Read only compact run summaries and produce private cost/volume reports."""
import argparse
import gzip
import json
from pathlib import Path
from decimal import Decimal

try:
    from .run_log import ROOT, load, label
    from .check_runtime import private_path, write
except ImportError:
    from run_log import ROOT, load, label
    from check_runtime import private_path, write

FIELDS=('input_tokens','cache_read_tokens','output_tokens','cache_write_tokens')

def estimate(records, prices):
    """Prices must identify their source/date and separately price every token category."""
    total=Decimal(0);currency=None
    for record in records:
        price=prices.get(record['model'])
        if not price or not price.get('source') or not price.get('as_of') or not price.get('currency'):
            return {'status':'unknown','reason':'missing_price_provenance','amount':None}
        if currency is not None and price['currency']!=currency:
            raise ValueError('Mixed currencies cannot be summed')
        currency=price['currency']
        for field in FIELDS:
            rate=price.get(field+'_per_million')
            if record.get(field) is None or rate is None:
                return {'status':'unknown','reason':'missing_usage_or_rate','amount':None}
            value=Decimal(str(rate))
            if not value.is_finite() or value<0:raise ValueError('Invalid price')
            total+=Decimal(record[field])*value/Decimal(1000000)
    return {'status':'estimated' if records else 'unknown','amount':str(total) if records else None,
            'currency':currency,'basis':'per_request_usage_and_supplied_prices; not a billing receipt'}

def report(run_ids, prices=None):
    results=[]
    for run in run_ids:
        label(run)
        folder=ROOT/'local/run-logs'/run;value=load(folder/'summary.json')
        cost={'status':'unknown','amount':None,'reason':'no_per_request_usage_or_prices'}
        path=folder/'usage.jsonl'
        archive=path.with_suffix(path.suffix+'.gz')
        if prices is not None and (path.exists() or archive.exists()):
            text=path.read_text(encoding='utf-8') if path.exists() else gzip.decompress(archive.read_bytes()).decode('utf-8')
            records=[json.loads(line) for line in text.splitlines()]
            cost=estimate(records,prices)
        results.append({k:value.get(k) for k in ('run_id','date','node','status','started_at','finished_at',
                        'reservation_count','formatted_count','sync_status','notification_status',
                        'human_interventions','usage','log_bytes','evidence_bytes')}|{'cost':cost})
    return {'schema_version':1,'runs':results,'limits':'Separate deployment/debug runs from operational runs; unknown usage is not zero.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='append',required=True)
    p.add_argument('--prices',type=private_path);p.add_argument('--output',type=private_path,required=True)
    args=p.parse_args();value=report(args.run,load(args.prices) if args.prices else None);write(args.output,value)
    print(json.dumps({'runs':len(value['runs']),'output':str(args.output.relative_to(ROOT))}))

if __name__=='__main__':main()
