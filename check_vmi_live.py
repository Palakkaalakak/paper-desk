"""Opt-in real-universe verification using the browser's stage order and bounds.
python -u check_vmi_live.py --live --mode moderate
Exit 2 means unresolved acquisition, never a completed scan. No orders/subscriptions.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import threading
import time
import vmi_sp500 as api
import vmi_trace as trace


def emit(**row):
    print(json.dumps(dict(at=int(time.time()*1000),**row)),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true',required=True)
    p.add_argument('--mode',choices=api.MODES,default='moderate')
    args=p.parse_args();started=time.monotonic();totals=Counter();lock=threading.Lock();sequence=0
    def measured(label,fn):
        nonlocal sequence
        with lock:
            sequence+=1;key='verify-'+str(sequence)
        at=time.monotonic()
        try:
            with trace.task(key,label): return fn()
        finally:
            record=trace.status(key)['requests'][0]
            with lock: totals.update(record['counts'])
            emit(stage='request',label=label,seconds=round(time.monotonic()-at,3),counts=record['counts'])
    u=measured('universe',api.members);members=u['rows'];rejected={r['symbol'] for r in members if api.sector(r)['excluded']};recovery=set()
    emit(stage='universe',count=len(members),source=u['source'],industryExcluded=len(rejected),ib='Optional; offline Gateway is not a user entitlement test')
    for view in ('financial','growth'):
        candidates=[r for r in members if r['symbol'] not in rejected];at=time.monotonic();done=0
        groups=[candidates[i:i+20] for i in range(0,len(candidates),20)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures={pool.submit(measured,'bulk '+view+' '+g[0]['symbol'],lambda g=g:api.batch(view,','.join(r['symbol'] for r in g),args.mode,defer=True)):g for g in groups}
            for future in as_completed(futures):
                group=futures[future];done+=len(group)
                try:
                    data=future.result();recovery.update(data['missing'])
                    for row in data['rows']:
                        if row['screen']['rejected']: rejected.add(row['symbol'])
                    emit(stage=view,done=done,total=len(candidates),rejected=len(rejected),missing=data['missing'])
                except Exception as exc:
                    recovery.update(r['symbol'] for r in group);emit(stage=view,error=str(exc))
        emit(stage=view+' summary',seconds=round(time.monotonic()-at,3),rejected=len(rejected),recovery=len(recovery-rejected))
    # Missing broad fields go straight to independent dated statements; do not
    # hold all companies behind a separate Yahoo quote-page recovery sweep.
    outcomes=Counter(REJECTED=len(rejected));pending=Counter();at=time.monotonic()
    candidates=[r for r in members if r['symbol'] not in rejected]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(measured,'detailed '+r['symbol'],lambda r=r:api.company(r['symbol'],args.mode)):r['symbol'] for r in candidates}
        for index,future in enumerate(as_completed(futures)):
            ticker=futures[future]
            try:
                data=future.result();r=data['result'];outcomes[r['status']]+=1
                if r['status']=='ACQUIRING': pending.update(r['pending'])
                emit(stage='deep',symbol=ticker,done=index+1,total=len(candidates),status=r['status'],failures=r['failures'],pending=r['pending'],attempts=data['evidence']['attempts'])
            except Exception as exc: outcomes['ERROR']+=1;emit(stage='deep',symbol=ticker,error=str(exc))
    complete=not outcomes['ACQUIRING'] and not outcomes['ERROR']
    emit(stage='summary',seconds=round(time.monotonic()-started,3),deepSeconds=round(time.monotonic()-at,3),universe=len(members),outcomes=dict(outcomes),pending=dict(pending),counts=dict(totals),complete=complete)
    return 0 if complete else 2


if __name__=='__main__': raise SystemExit(main())
