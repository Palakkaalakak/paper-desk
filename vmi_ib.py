"""Optional read-only IBKR scanner enrichment on the existing owner loop."""
import asyncio
import datetime as dt
import math
import re
import time
import xml.etree.ElementTree as ET
from vmi_data import symbol

FIELDS={'TTMROEPCT':'ROE','QCURRATIO':'Curr R','TTMGROSMGN':'Gross M','TTMNPMGN':'Profit M','TTMOPMGN':'Oper M'}
# Deliberately no TTMROIPCT: return on investment is not return on invested capital.
def identity(value): return str(value).upper().replace('.',' ').replace('-',' ').split()


def parse_report(xml,ticker):
    if not isinstance(xml,str) or len(xml)>2_000_000 or re.search(r'<!DOCTYPE|<!ENTITY',xml,re.I): raise ValueError('Unsafe IB report')
    root=ET.fromstring(xml);ids={n.get('Type','').upper():(n.text or '').strip() for n in root.iter() if n.tag.split('}')[-1]=='CoID'}
    if identity(ids.get('TICKER',ids.get('SYMBOL','')))!=identity(ticker): raise ValueError('IB report identity mismatch')
    fields={};observations=[]
    parents={c:p for p in root.iter() for c in p}
    for n in root.iter():
        if n.tag.split('}')[-1]!='Ratio' or n.get('FieldName') not in FIELDS: continue
        try: value=float(n.text or '')
        except ValueError: continue
        if not math.isfinite(value) or abs(value)>1e50 or value==-99999.99: continue
        date=n.get('AsOfDate') or parents.get(n,root).get('AsOfDate')
        if date:
            try:
                if not 0<=(dt.date.today()-dt.date.fromisoformat(date[:10])).days<=550: continue
            except ValueError: continue
        label=FIELDS[n.get('FieldName')];fields[label]=str(value)
        observations.append(dict(field=n.get('FieldName'),value=value,asOf=date,basis='IB provider snapshot definition'))
    return dict(fields=fields,observations=observations,identifiers=ids,source='IBKR ReportSnapshot')


def valid_quote(q,conid):
    at=q.get('tradeAt') or q.get('at');last=q.get('last')
    return q.get('brokerConid')==conid and q.get('status')=='LIVE' and not q.get('error') and not q.get('halted') and type(at) in (int,float) and 0<=time.time()*1000-at<=60000 and type(last) in (int,float) and math.isfinite(last) and last>0


async def report(engine,contract,timeout=6):
    ib=engine._ib;rid=ib.client.getReqId();future=ib.wrapper.startReq(rid,contract)
    try:
        ib.client.reqFundamentalData(rid,contract,'ReportSnapshot',[])
        return await asyncio.wait_for(asyncio.shield(future),timeout)
    finally:
        if engine._ib is ib:
            try: ib.client.cancelFundamentalData(rid)
            except Exception: pass
        ib.wrapper._endReq(rid)
        if not future.done(): future.cancel()
        elif not future.cancelled(): future.exception()


OBSERVATION_BUDGET=8


def _blocked(engine):
    if not engine.info.get('connected',False):
        return 'Gateway disconnected; public sources continue'
    if '10197' in str(engine.info.get('problem') or ''):
        return 'IB 10197 competing live session; scanner broker requests skipped, public screening continues'
    state=getattr(engine,'_vmi_scan_cooldown',None)
    if state and state[0]==engine.info.get('generation',0) and time.monotonic()<state[1]:
        return 'Scanner IB circuit open after unavailable data: '+state[2]
    return None


def _cooldown(engine,reason):
    with engine.changed:
        engine._vmi_scan_cooldown=(engine.info.get('generation',0),time.monotonic()+120,str(reason)[:200])


async def observe(engine,ticker):
    ticker=symbol(ticker)
    reason=_blocked(engine)
    if reason: return dict(available=False,source='IBKR',reason=reason)
    if not hasattr(engine,'_vmi_scanner_lock'): engine._vmi_scanner_lock=asyncio.Lock()
    # Optional scanner work must not queue behind other symbols or touch streams.
    if engine._vmi_scanner_lock.locked():
        return dict(available=False,source='IBKR',reason='Scanner IB slot busy; public evidence retained')
    async with engine._vmi_scanner_lock:
        try:
            result=await asyncio.wait_for(_observe(engine,ticker),OBSERVATION_BUDGET)
            if not result.get('fields') and not valid_quote(result.get('quote',{}),result.get('conid')):
                _cooldown(engine,'No usable live quote or financial report')
            return result
        except TimeoutError as exc:
            _cooldown(engine,type(exc).__name__)
            return dict(available=False,source='IBKR',reason='Scanner IB deadline/service unavailable; public evidence retained')


async def _observe(engine,ticker):
    ib,generation=engine._ib,engine.info.get('generation',0)
    def check():
        if engine._ib is not ib or engine.info.get('generation',0)!=generation: raise ConnectionError('Gateway changed during VMI acquisition')
    c=await asyncio.wait_for(engine._stock(ticker.replace('.',' ').replace('-',' ')),3);check()
    if c.secType!='STK' or c.currency!='USD' or not c.conId or identity(c.symbol)!=identity(ticker): raise ValueError('IB stock identity/currency mismatch')
    out=dict(symbol=ticker,conid=c.conId,fields={},source='IBKR',served_by='tws',retrievedAt=int(time.time()*1000),attempts=[])
    try:
        existing=next((r['ticker'] for r in engine._active.values() if r['contract'].conId==c.conId),None)
        q=engine._quote(existing) if existing is not None else {}
        if not valid_quote(q,c.conId): q=await asyncio.wait_for(engine._snapshot_quote(c),3)
        check()
        if q.get('brokerConid')!=c.conId: raise ValueError('IB quote conid mismatch')
        out['quote']=q
    except asyncio.CancelledError: raise
    except Exception as exc: out['attempts'].append(dict(source='IBKR snapshot',error=str(exc)))
    if time.monotonic()>=getattr(engine,'_vmi_report_cooldown',0):
        try:
            raw=await report(engine,c);check();parsed=parse_report(raw,ticker)
            out['fundamentals']=parsed;out['fields']=parsed['fields']
        except asyncio.CancelledError: raise
        except Exception as exc:
            engine._vmi_report_cooldown=time.monotonic()+900
            out['attempts'].append(dict(source='IBKR ReportSnapshot',error=str(exc),fallback='Public financial evidence retained'))
    else: out['attempts'].append(dict(source='IBKR ReportSnapshot',error='Entitlement/service cooldown'))
    check();return out

def available(engine,ticker):
    with engine.changed:
        reason=_blocked(engine)
        if reason: return dict(available=False,source='IBKR',reason=reason)
        if getattr(engine,'_vmi_scan_http_busy',False):
            return dict(available=False,source='IBKR',reason='Scanner IB slot busy; no queued broker request')
        engine._vmi_scan_http_busy=True
    try:
        result=engine.call('vmi_observation',symbol(ticker),timeout=9)
        return dict(result,available=result.get('available',True))
    except Exception as exc:
        _cooldown(engine,type(exc).__name__)
        return dict(available=False,source='IBKR',reason=str(exc))
    finally:
        with engine.changed: engine._vmi_scan_http_busy=False
