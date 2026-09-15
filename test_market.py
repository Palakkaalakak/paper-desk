"""Offline stream regression tests. No live IBKR connection is used."""
import asyncio
import datetime as dt
import http.client
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock, AsyncMock
import market
import serve


def rows(n):
    return [dict(conid=10000+i,symbol='TEST'+str(i),secType='STK') for i in range(n)]


class FakeBroker:
    def __init__(self):
        self.connected=True
        self.calls=[]
        self.cancelled=[]
        self.owners=set()
    def isConnected(self): return self.connected
    def disconnect(self): self.connected=False
    def reqMktData(self,c,*args):
        self.calls.append(c.conId);self.owners.add(threading.get_ident())
        return SimpleNamespace(contract=c,bid=100,ask=100.02,last=100.01,bidSize=1000,askSize=1000,
            volume=10000,close=99,marketDataType=1,halted=0,time=dt.datetime.now(dt.timezone.utc))
    def cancelMktData(self,c): self.cancelled.append(c.conId)


class FakeEngine(market.MarketEngine):
    async def _connect(self):
        self._ib=FakeBroker();self._status(connected=True,problem=None,feedHealthy=True)
        return self._ib
    async def _contract(self,row): return SimpleNamespace(conId=row['conid'])
    async def _slow(self):
        await asyncio.sleep(.3)
        return 'finished'


class SnapshotOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import itertools
        from ib_async import IB,Stock
        self.ib=IB();self.e=market.MarketEngine();self.e._ib=self.ib
        self.c=Stock('TEST','SMART','USD',conId=123)
        self.e._stock=AsyncMock(return_value=self.c);self.e._contract=AsyncMock(return_value=self.c)
        ids=itertools.count(1)
        self.ib.client.getReqId=Mock(side_effect=lambda:next(ids))
        self.ib.client.reqMktData=Mock();self.ib.client.cancelMktData=Mock()
        self.ib.pendingTickersEvent+=self.e._ticks

    def ticks(self,req_id,end=False):
        w=self.ib.wrapper;w.tcpDataArrived()
        w.priceSizeTick(req_id,1,10,100);w.priceSizeTick(req_id,2,10.02,100)
        w.priceSizeTick(req_id,4,10.01,100);w.tcpDataProcessed()
        if end:w.tickSnapshotEnd(req_id)

    async def test_snapshot_cannot_cancel_new_browser_stream_for_same_conid(self):
        task=asyncio.create_task(self.e._quote_symbol('TEST'));await asyncio.sleep(0)
        snapshot_id=self.ib.client.reqMktData.call_args.args[0]
        await self.e._subscribe_one(123,{'conid':123})
        stream_id=self.ib.client.reqMktData.call_args.args[0]
        self.assertNotEqual(snapshot_id,stream_id)
        self.ticks(snapshot_id,True);q=await task
        self.assertAlmostEqual(q['ask']-q['bid'],.02)
        self.ib.client.cancelMktData.assert_called_once_with(snapshot_id)
        ticker=self.e._active[123]['ticker']
        self.assertEqual(self.ib.wrapper.ticker2ReqId['mktData'][ticker],stream_id)
        self.ticks(stream_id)
        self.assertEqual(self.e.quotes[123]['bid'],10)
        self.assertEqual(self.e.quotes[123]['ask'],10.02)

    async def test_cancelled_snapshot_cleans_only_its_request(self):
        await self.e._subscribe_one(123,{'conid':123})
        stream_id=self.ib.client.reqMktData.call_args.args[0]
        task=asyncio.create_task(self.e._snapshot_quote(self.c));await asyncio.sleep(0)
        snapshot_id=self.ib.client.reqMktData.call_args.args[0]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.ib.client.cancelMktData.assert_called_once_with(snapshot_id)
        self.assertIn(stream_id,self.ib.wrapper.reqId2Ticker)
        self.assertNotIn(snapshot_id,self.ib.wrapper.reqId2Ticker)
        self.assertNotIn(snapshot_id,self.ib.wrapper._futures)

    async def test_old_generation_cannot_cancel_new_connections_reused_id(self):
        task=asyncio.create_task(self.e._snapshot_quote(self.c));await asyncio.sleep(0)
        self.e.info['generation']=1;task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.ib.client.cancelMktData.assert_not_called()

    async def test_scanner_reads_exact_ib_contract_without_browser_subscription(self):
        task=asyncio.create_task(self.e._guns_ib_quote('123'));await asyncio.sleep(0)
        call=self.ib.client.reqMktData.call_args
        self.assertEqual(call.args[1].conId,123);self.assertEqual(call.args[1].secType,'STK')
        self.assertEqual(call.args[3:5],(True,False))
        self.ticks(call.args[0],True);q=await task
        self.assertEqual(q['brokerConid'],123);self.assertEqual(q['source'],'IB Gateway')
        self.assertEqual(q['status'],'LIVE');self.assertEqual(q['ask'],10.02)

    async def test_live_stream_quote_is_reused_without_new_request(self):
        await self.e._subscribe_one(123,{'conid':123})
        self.ticks(self.ib.client.reqMktData.call_args.args[0])
        q=await self.e._guns_ib_quote('123')
        self.assertEqual(q['bid'],10);self.assertEqual(self.ib.client.reqMktData.call_count,1)
        self.ib.client.cancelMktData.assert_not_called()

    def test_delayed_crossed_missing_stale_or_halted_quote_cannot_screen(self):
        now=int(time.time()*1000);q=dict(bid=10,ask=10.02,last=10.01,status='LIVE',at=now)
        for change in [dict(bid=None),dict(ask=None),dict(bid=0),dict(ask=9),dict(at=now-20000),dict(status='DELAYED'),dict(halted=True)]:
            self.assertFalse(self.e._screen_quote_complete({**q,**change},now))


class DepthStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from ib_async import IB, Stock
        import itertools
        self.e=market.MarketEngine();self.ib=IB();self.e._ib=self.ib
        self.ib.isConnected=Mock(return_value=True)
        ids=itertools.count(1)
        self.ib.client.getReqId=Mock(side_effect=lambda:next(ids))
        self.ib.client.reqMktDepth=Mock();self.ib.client.cancelMktDepth=Mock()
        self.e._stock=AsyncMock(side_effect=lambda symbol:Stock(symbol,'SMART','USD',conId=sum(map(ord,symbol))))

    async def acquire(self,symbol='TEST'):
        task=asyncio.create_task(self.e._depth(symbol));await asyncio.sleep(0)
        self.update(symbol)
        return await task

    def update(self,symbol='TEST'):
        entry=self.e._depth_streams[symbol];t=entry['ticker']
        from ib_async.objects import DOMLevel
        t.domBids[:]=[DOMLevel(10-i*.01,100,'') for i in range(3)]
        t.domAsks[:]=[DOMLevel(10.02+i*.01,100,'') for i in range(3)]
        t.domTicks[:]=[object()];t.updateEvent.emit(t);t.domTicks.clear()

    async def test_polling_reuses_subscription_without_refreshing_timestamp(self):
        first=await self.acquire()
        second=await self.e._depth('TEST')
        self.assertEqual(first,second)
        self.assertEqual(self.ib.client.reqMktDepth.call_count,1)
        t=self.e._depth_streams['TEST']['ticker'];t.updateEvent.emit(t)
        self.assertEqual((await self.e._depth('TEST'))['revision'],1)
        self.update()
        self.assertEqual((await self.e._depth('TEST'))['revision'],2)
        self.ib.client.cancelMktDepth.assert_not_called()

    async def test_capacity_eviction_detaches_handler_and_cancels_owned_stream(self):
        await self.acquire('ONE');old=self.e._depth_streams['ONE']
        await self.acquire('TWO');await self.acquire('THREE');await self.acquire('FOUR')
        self.assertEqual(len(self.e._depth_streams),3)
        self.assertNotIn('ONE',self.e._depth_streams)
        self.assertEqual(self.ib.client.cancelMktDepth.call_count,1)
        revision=old['revision'];old['ticker'].domTicks[:]=[object()]
        old['handler'](old['ticker'])
        self.assertEqual(old['revision'],revision)

    async def test_evicted_waiter_cannot_return_replaced_depth(self):
        task=asyncio.create_task(self.e._depth('ONE'));await asyncio.sleep(0)
        await self.acquire('TWO');await self.acquire('THREE');await self.acquire('FOUR')
        with self.assertRaisesRegex(ValueError,'replaced'):await task

    async def test_generation_change_during_qualification_never_subscribes(self):
        async def qualify(symbol):
            self.e.info['generation']=1
            return SimpleNamespace(conId=123)
        self.e._stock=qualify
        with self.assertRaises(ConnectionError):await self.e._depth('TEST')
        self.ib.client.reqMktDepth.assert_not_called()

    async def test_old_generation_handler_cannot_refresh_or_cancel_new_connection(self):
        await self.acquire();old=self.e._depth_streams['TEST'];revision=old['revision']
        self.e.info['generation']=1;self.update()
        self.assertEqual(old['revision'],revision)
        self.e._close_depth('TEST')
        self.ib.client.cancelMktDepth.assert_not_called()

    async def test_broker_depth_reset_invalidates_old_book(self):
        await self.acquire();entry=self.e._depth_streams['TEST']
        req_id=self.ib.client.reqMktDepth.call_args.args[0]
        self.e._error(req_id,317,'Reset',entry['contract'])
        self.assertEqual(entry['at'],0)
        self.assertEqual(entry['ticker'].domBids,[])
        self.assertEqual(entry['ticker'].domAsks,[])


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.e=FakeEngine(capacity=500);self.addCleanup(self.e.close)
    def until(self,predicate):
        end=time.monotonic()+3
        while time.monotonic()<end:
            if predicate(): return
            time.sleep(.01)
        self.fail('Timed out waiting for mock broker')
    def subscribe(self,n):
        self.e.subscribe('browser_test',rows(n))
        self.until(lambda:self.e.snapshot('browser_test')['loaded']==min(n,self.e.capacity))
    def test_500_contracts_one_owner_no_resubscription(self):
        t=time.perf_counter();self.e.subscribe('browser_test',rows(500))
        self.assertLess(time.perf_counter()-t,.2)
        self.until(lambda:self.e.snapshot('browser_test')['loaded']==500)
        self.assertEqual(len(self.e._ib.calls),500)
        self.assertEqual(len(self.e._ib.owners),1)
        self.assertNotIn(threading.get_ident(),self.e._ib.owners)
        self.e.subscribe('browser_test',rows(500));time.sleep(.2)
        self.assertEqual(len(self.e._ib.calls),500)
    def test_deltas_and_client_isolation(self):
        self.subscribe(2);first=self.e.snapshot('browser_test')
        self.e.subscribe('other_browser',[dict(conid=999,symbol='OTHER')])
        self.e.publish(10000,dict(bid=101,ask=102,at=1))
        self.assertEqual(list(self.e.snapshot('browser_test',first['seq'])['quotes']),['10000'])
        self.assertNotIn('10000',self.e.snapshot('other_browser')['quotes'])
    def test_slow_lookup_keeps_ticking(self):
        self.subscribe(1)
        f=asyncio.run_coroutine_threadsafe(self.e._request('slow'),self.e._loop)
        before=self.e.snapshot('browser_test')['seq'];ticker=self.e._active[10000]['ticker']
        ticker.bid=102;self.e._loop.call_soon_threadsafe(self.e._ticks,[ticker])
        self.until(lambda:self.e.snapshot('browser_test')['seq']>before)
        self.assertFalse(f.done());self.assertEqual(f.result(2),'finished')
    def test_disconnect_rebuilds_subscriptions(self):
        self.subscribe(3);old=self.e._ib;old.connected=False
        self.until(lambda:self.e._ib is not old)
        self.until(lambda:self.e.snapshot('browser_test')['loaded']==3)
        self.assertEqual(self.e.snapshot('browser_test')['generation'],1)
        self.assertEqual(len(self.e._ib.calls),3)
    def test_capacity_is_explicit(self):
        self.e.capacity=2;self.subscribe(5)
        self.until(lambda:self.e.snapshot('browser_test').get('overCapacity')==3)
        self.assertEqual(len(self.e.snapshot('browser_test')['quotes']),2)
        self.assertEqual(len(self.e._ib.calls),2)
    def test_unused_lines_are_released(self):
        self.subscribe(2);self.e.subscribe('browser_test',rows(1))
        self.until(lambda:10001 in self.e._ib.cancelled)
        self.assertNotIn('10001',self.e.snapshot('browser_test')['quotes'])
    def test_validation(self):
        for bad in [None,{},[{}],[dict(conid=True,symbol='X')],rows(2001)]:
            with self.assertRaises(ValueError): market.instruments(bad)
        for v in [-1,float('nan'),float('inf')]: self.assertIsNone(market.number(v,True))
        self.assertEqual(market.number(0,True),0)
    def test_delayed_and_frozen_are_labeled(self):
        self.subscribe(1);t=self.e._active[10000]['ticker']
        for mode,label in [(1,'LIVE'),(2,'FROZEN'),(3,'DELAYED'),(4,'DELAYED FROZEN')]:
            t.marketDataType=mode;self.assertEqual(self.e._quote(t)['status'],label)
        t.last=float('nan');self.assertIsNone(self.e._quote(t)['last'])
    def test_authenticated_http_subscription_and_sse(self):
        with patch.object(market,'ENGINE',self.e),patch.object(serve,'ACCESS','test-token'):
            server=serve.Server(('127.0.0.1',0),serve.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(method,path,body=None,auth=True):
                c=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=3)
                c.request(method,path,body=body,headers={'Cookie':'pd_token=test-token'} if auth else {})
                return c,c.getresponse()
            try:
                data=json.dumps(dict(client='browser_test',instruments=rows(2)))
                for method,path,body,auth,status in [
                    ('POST','/data/subscriptions',data,False,403),
                    ('GET','/data/stream?client=browser_test',None,False,403),
                    ('POST','/data/subscriptions','[]',True,400),
                    ('POST','/data/subscriptions',data,True,200)]:
                    c,r=request(method,path,body,auth);self.assertEqual(r.status,status);r.read();c.close()
                self.until(lambda:self.e.snapshot('browser_test')['loaded']==2)
                c,r=request('GET','/data/stream?client=browser_test')
                self.assertEqual(r.getheader('Content-Type'),'text/event-stream')
                self.assertEqual(r.readline(),b'event: quotes\n')
                first=json.loads(r.readline().decode()[6:]);r.readline()
                self.assertEqual(len(first['quotes']),2)
                self.e.publish(10000,dict(bid=102,ask=103,at=1))
                self.assertEqual(r.readline(),b'event: quotes\n')
                delta=json.loads(r.readline().decode()[6:]);self.assertEqual(delta['quotes']['10000']['bid'],102)
                r.close();c.close()
            finally:
                server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__': unittest.main()
