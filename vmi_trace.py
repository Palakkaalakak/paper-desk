"""Transient, bounded request-scoped scanner diagnostics (no credentials/URLs)."""
from collections import Counter, deque
from contextlib import contextmanager
import re
import threading
import time
import uuid

_LOCK=threading.RLock()
_LOCAL=threading.local()
_RECORDS={}


def valid_id(key):
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',key): raise ValueError('Invalid diagnostic ID')
    return key


def event(kind,detail='',provider='',**values):
    key=getattr(_LOCAL,'key',None)
    with _LOCK:
        row=_RECORDS.get(key)
        if not row: return
        row.update(updated=time.monotonic(),phase=kind,detail=str(detail)[:240])
        if provider: row['provider']=str(provider)[:80]
        row['counts'][kind]+=1
        item=dict(kind=kind,detail=row['detail'],provider=provider,at=int(time.time()*1000))
        item.update({k:v for k,v in values.items() if k in ('waitSeconds','attempt','status','bytes') and type(v) in (int,float)})
        row['events'].append(item)


@contextmanager
def task(key,label):
    key=valid_id(key) if key else uuid.uuid4().hex
    now=time.monotonic()
    with _LOCK:
        for old,r in list(_RECORDS.items()):
            if r['state']!='running' and now-r['updated']>600: del _RECORDS[old]
        if key in _RECORDS and _RECORDS[key]['state']=='running': raise ValueError('Duplicate active diagnostic ID')
        while len(_RECORDS)>=256:
            old=next((k for k,r in _RECORDS.items() if r['state']!='running'),None)
            if old is None: raise ValueError('Too many active scanner requests')
            del _RECORDS[old]
        _RECORDS[key]=dict(id=key,label=str(label)[:180],state='running',started=now,updated=now,phase='queued',detail='Accepted',provider='',counts=Counter(),events=deque(maxlen=30))
    previous=getattr(_LOCAL,'key',None);_LOCAL.key=key
    try:
        event('accepted',label);yield key
    except Exception as exc:
        event('failed',type(exc).__name__)
        with _LOCK: _RECORDS[key]['state']='failed'
        raise
    else:
        event('completed',label)
        with _LOCK: _RECORDS[key]['state']='completed'
    finally: _LOCAL.key=previous


def status(ids):
    keys=ids.split(',') if ids else []
    if len(keys)>12: raise ValueError('At most 12 diagnostic IDs')
    for key in keys: valid_id(key)
    now=time.monotonic();out=[]
    with _LOCK:
        for key in keys:
            r=_RECORDS.get(key)
            if r: out.append(dict(id=key,label=r['label'],state=r['state'],phase=r['phase'],detail=r['detail'],provider=r['provider'],elapsedSeconds=round((now if r['state']=='running' else r['updated'])-r['started'],1),idleSeconds=round(now-r['updated'],1),counts=dict(r['counts']),events=list(r['events'])))
    return dict(version=1,at=int(time.time()*1000),requests=out)
