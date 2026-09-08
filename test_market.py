"""Offline stream regression tests. No live IBKR connection is used."""
import asyncio
import datetime as dt
import http.client
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
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
