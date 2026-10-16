"""Owned, paced streaming requests for every listed put; no 11-second snapshots.

IB docs: md_request.html, market_data_type.html, option_computations.html.
A frozen quote is for inspection only. Missing exchange quotes are never invented.
"""
import asyncio
import copy
import math
import os
import secrets
import time
from vmi_options import unchanged

IDLE_SECONDS = 25
BATCH_SECONDS = 5
ERRORS = {100,101,200,354,420,10089,10090,10167,10168,10197}


def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and abs(x)<1e100


def quote_ready(q):
    return (q and finite(q.get('bid')) and finite(q.get('ask')) and q['bid']>=0 and
            q['ask']>0 and q['ask']>=q['bid'] and finite(q.get('greeks',{}).get('delta')) and
            -1<=q['greeks']['delta']<=0)


class ChainStream:
    def __init__(self,engine,definitions,spot,mode='live'):
        if mode not in ('live','frozen'):
            raise ValueError('Chain data mode must be live or frozen')
        self.engine=engine;self.ib=engine._ib;self.generation=engine.info.get('generation',0)
        self.id=secrets.token_urlsafe(18);self.mode=mode
        self.out=copy.deepcopy(definitions);self.rows={r['conid']:r for r in self.out['puts']}
        self.last_read=time.monotonic();self.handles={};self.priority=[];self.task=None;self.closed=False
        self.under_handle=None;self.error=None;self.cycle=0
        # Quote likely 10-delta strikes first, then cover EVERY listed ITM/OTM strike.
        self.order=sorted(self.rows,key=lambda cid:abs(self.rows[cid]['strike']-spot*.93))
        self.limit=max(2,min(48,int(os.environ.get('PAPER_BPSX_LINES','32'))))
        self.capacity=min(engine.capacity,max(1,int(os.environ.get('PAPER_TWS_LINES','100'))))
        for r in self.rows.values():r['quoteState']='QUEUED'

    def start(self):
        self.ib.errorEvent+=self.on_error
        self.task=asyncio.create_task(self.run())
        self.task.add_done_callback(lambda t:None if t.cancelled() else t.exception())
        return self

    def on_error(self,req_id,code,text,contract=None):
        if code not in ERRORS:return
        for cid,h in self.handles.items():
            if h['id']==req_id:
                row=self.rows.get(cid)
                if row is not None:
                    row['quoteError']=str(code)+': '+text
                    row['quoteState']='PERMISSION' if code in (354,10089,10090,10167,10168,10197) else 'IB_ERROR'
                    row.pop('quote',None)

    def open(self,contract):
        # Borrow an existing browser stream rather than consuming a second data line.
        existing=self.engine._active.get(contract.conId)
        if existing and self.mode=='live':return dict(id=None,ticker=existing['ticker'],borrowed=True)
        # IB's datatype selection applies to subsequent requests. No await between
        # selection/request/reset: other browser requests retain their configured type.
        default=3 if os.environ.get('PAPER_TWS_DATA')=='delayed' else 1
        try:
            if self.mode=='frozen':self.ib.client.reqMarketDataType(2)
            handle=self.engine._open_chain_stream(contract)
        finally:
            if self.mode=='frozen':self.ib.client.reqMarketDataType(default)
        handle['borrowed']=False
        return handle

    def cancel(self,h):
        if not h or h['borrowed']:return
        self.engine._close_chain_stream(h)

    def capture(self,cid,h):
        r=self.rows[cid]
        if r.get('quoteState') in ('PERMISSION','IB_ERROR'):return
        own_error=next((e for e in h.get('errors',[]) if e['code'] in ERRORS),None)
        if own_error:
            r['quoteError']=str(own_error['code'])+': '+own_error['message']
            r['quoteState']='PERMISSION' if own_error['code'] in (354,10089,10090,10167,10168,10197) else 'IB_ERROR'
            r.pop('quote',None)
            return
        t=h['ticker'];q=self.engine._quote(t)
        # _quote must retain tick receipt time; never stamp cached values as new.
        if not getattr(t,'time',None):return
        q['greeks']=q.get('greeks',{})
        # Model first; real bid/ask option computations are valid alternatives.
        if not finite(q['greeks'].get('delta')) or not -1<=q['greeks']['delta']<=0:
            for name in ('bidGreeks','askGreeks','lastGreeks'):
                g=getattr(t,name,None)
                if g and finite(getattr(g,'delta',None)) and -1<=g.delta<=0:
                    q['greeks']={k:getattr(g,n,None) for k,n in [('delta','delta'),('iv','impliedVol'),('gamma','gamma'),('theta','theta'),('vega','vega')]}
                    q['greeksSource']=name;break
        r['quote']=q
        raw_bid=getattr(t,'bid',None);raw_ask=getattr(t,'ask',None)
        r['bidAbsent']=finite(raw_bid) and raw_bid<0
        r['askAbsent']=finite(raw_ask) and raw_ask<0
        if quote_ready(q):r['quoteState']='READY'
        elif r['bidAbsent'] or r['askAbsent']:r['quoteState']='NO_MARKET'
        elif q.get('bid') is not None and q.get('ask') is not None:r['quoteState']='WAIT_GREEKS'
        else:r['quoteState']='LOADING'

    async def run(self):
        from ib_async import Option
        try:
            under=await self.engine._stock(self.out['symbol'])
            unchanged(self.engine,self.ib,self.generation)
            self.under_handle=self.open(under)  # Live Greeks require underlying data too.
            remaining=list(self.order)
            while not self.closed and time.monotonic()-self.last_read<IDLE_SECONDS:
                unchanged(self.engine,self.ib,self.generation)
                # Preserve headroom for stock/position streams and other chain sessions.
                other=sum(len(s.handles)+(1 if s.under_handle else 0) for s in getattr(self.engine,'_bpsx_streams',{}).values() if s is not self and not s.closed)
                budget=min(self.limit,max(0,self.capacity-len(self.engine._active)-other-4))
                if budget<1:
                    self.error='IB data-line capacity exhausted. Close unused watchlists/chains or increase your IBKR market-data line allowance.'
                    await asyncio.sleep(.5);continue
                self.error=None
                if not remaining:
                    self.cycle+=1;remaining=list(self.order)
                    await asyncio.sleep(.2)
                batch=[]
                for cid in list(dict.fromkeys(self.priority+remaining)):
                    if cid not in self.rows:continue
                    batch.append(cid)
                    if cid in remaining:remaining.remove(cid)
                    if cid in self.priority:self.priority.remove(cid)
                    if len(batch)>=budget:break
                for cid in batch:
                    row=self.rows[cid]
                    row.pop('quoteError',None)
                    if not row.get('quote'):row['quoteState']='LOADING'
                    self.handles[cid]=self.open(Option(row['root'],row['expiry'],row['strike'],'P','SMART',conId=cid,multiplier='100',currency='USD',tradingClass=row['tradingClass']))
                    await asyncio.sleep(.035)  # Request/cancel combined stays below default 50 msg/s.
                deadline=time.monotonic()+BATCH_SECONDS
                persistent=len(self.rows)<=budget
                while (persistent or time.monotonic()<deadline) and not self.closed:
                    unchanged(self.engine,self.ib,self.generation)
                    for cid,h in self.handles.items():self.capture(cid,h)
                    settled=all(self.rows[c].get('quoteState') in ('READY','PERMISSION','IB_ERROR') for c in batch)
                    if settled and not persistent:break
                    if persistent and time.monotonic()>deadline:
                        for cid in batch:
                            state=self.rows[cid].get('quoteState')
                            if state in ('LOADING','WAIT_GREEKS'):
                                self.rows[cid]['quoteState']='GREEKS_MISSING' if state=='WAIT_GREEKS' else 'NO_QUOTE'
                    if time.monotonic()-self.last_read>=IDLE_SECONDS:break
                    await asyncio.sleep(.08)
                for cid,h in list(self.handles.items()):
                    self.capture(cid,h)
                    if self.rows[cid].get('quoteState') in ('LOADING','WAIT_GREEKS'):
                        q=self.rows[cid].get('quote') or {}
                        self.rows[cid]['quoteState']='GREEKS_MISSING' if q.get('bid') is not None and q.get('ask') is not None else 'NO_QUOTE'
                    self.cancel(h);self.handles.pop(cid,None)
                    await asyncio.sleep(.025)
                if self.cycle==0 and not remaining:self.cycle=1
        except asyncio.CancelledError:raise
        except Exception as exc:self.error=str(exc)
        finally:
            for h in list(self.handles.values()):self.cancel(h)
            self.handles.clear();self.cancel(self.under_handle)
            self.ib.errorEvent-=self.on_error
            self.closed=True

    def snapshot(self):
        self.last_read=time.monotonic()
        unchanged(self.engine,self.ib,self.generation)
        for cid,h in self.handles.items():self.capture(cid,h)
        out=copy.deepcopy(self.out)
        underlying_errors=(self.under_handle or {}).get('errors',[])
        underlying_issue=next((e for e in underlying_errors if e['code'] in ERRORS),None)
        issue=self.error or ('Underlying IB '+str(underlying_issue['code'])+': '+underlying_issue['message'] if underlying_issue else None)
        states=[r.get('quoteState') for r in self.rows.values()]
        out.update(streamId=self.id,streaming=not self.closed,cycle=self.cycle,
            total=len(states),readyCount=states.count('READY'),
            settledCount=sum(s not in ('QUEUED','LOADING','WAIT_GREEKS') for s in states),
            streamError=issue,dataMode=self.mode,served_by='tws',retrievedAt=int(time.time()*1000),
            note='Every listed put is requested using paced streaming batches. LIVE/FROZEN status is explicit; frozen/delayed quotes cannot submit orders. No exchange bid/ask is not a numeric zero. No paid regulatory snapshots.')
        return out

    def close(self):
        self.closed=True
        if self.task:self.task.cancel()


def sessions(engine):
    if not hasattr(engine,'_bpsx_streams'):engine._bpsx_streams={}
    for sid,s in list(engine._bpsx_streams.items()):
        if s.closed or time.monotonic()-s.last_read>IDLE_SECONDS:
            s.close();engine._bpsx_streams.pop(sid,None)
    return engine._bpsx_streams


def start(engine,definitions,spot,mode='live'):
    all_sessions=sessions(engine)
    for s in all_sessions.values():
        if s.out['symbol']==definitions['symbol'] and s.out['expiry']==definitions['expiry'] and s.mode==mode:
            return s
    if len(all_sessions)>=3:raise ValueError('Close an existing BPSX chain before opening another')
    s=ChainStream(engine,definitions,spot,mode)
    all_sessions[s.id]=s.start()
    return s


def read(engine,sid):
    s=sessions(engine).get(sid)
    if not s:raise ValueError('Chain stream expired; reload the chain')
    return s.snapshot()


def close_all(engine):
    for s in getattr(engine,'_bpsx_streams',{}).values():s.close()
    engine._bpsx_streams={}
