"""Opt-in full real-universe verification; stdout is compact JSONL, no orders.
python -u check_vmi_live.py --live --mode moderate
Exit 2 means unresolved acquisition, never a completed scan.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
import vmi_sp500 as api


def emit(**row):
    print(json.dumps(dict(at=int(time.time()*1000),**row)),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true',required=True)
    p.add_argument('--mode',choices=api.MODES,default='moderate')
    args=p.parse_args();u=api.members();members=u['rows'];rejected=set()
    emit(stage='universe',count=len(members),source=u['source'])
    for view in ('financial','growth'):
        candidates=[r for r in members if r['symbol'] not in rejected]
        for offset in range(0,len(candidates),20):
            group=candidates[offset:offset+20]
            try:
                data=api.batch(view,','.join(r['symbol'] for r in group),args.mode)
                for row in data['rows']:
                    if row['screen']['rejected']:rejected.add(row['symbol'])
                    emit(stage=view,symbol=row['symbol'],screen=row['screen'],source=row['source'])
                emit(stage=view,done=min(offset+20,len(candidates)),total=len(candidates),rejected=len(rejected),missing=data['missing'])
            except Exception as exc:emit(stage=view,error=str(exc),symbols=[r['symbol'] for r in group])
    outcomes=Counter(REJECTED=len(rejected));pending=Counter()
    candidates=[r for r in members if r['symbol'] not in rejected]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(api.company,r['symbol'],args.mode):r['symbol'] for r in candidates}
        for index,future in enumerate(as_completed(futures)):
            ticker=futures[future]
            try:
                data=future.result();r=data['result'];outcomes[r['status']]+=1
                if r['status']=='ACQUIRING':pending.update(r['pending'])
                emit(stage='deep',symbol=ticker,done=index+1,total=len(candidates),status=r['status'],failures=r['failures'],pending=r['pending'],attempts=data['evidence']['attempts'])
            except Exception as exc:outcomes['ERROR']+=1;emit(stage='deep',symbol=ticker,error=str(exc))
    complete=not outcomes['ACQUIRING'] and not outcomes['ERROR']
    emit(stage='summary',universe=len(members),outcomes=dict(outcomes),pending=dict(pending),complete=complete)
    return 0 if complete else 2

if __name__=='__main__':raise SystemExit(main())
