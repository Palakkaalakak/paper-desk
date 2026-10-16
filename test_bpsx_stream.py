"""Exercise actual ib_async tick routing with mocked socket writes, not fabricated broker fills."""
import asyncio
import datetime as dt
import itertools
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from ib_async import IB, Stock, Option
from market import MarketEngine
import bpsx_stream as s


class ChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ib=IB();self.e=MarketEngine();self.e._ib=self.ib
        self.under=Stock('TEST','SMART','USD',conId=999)
        self.e._stock=AsyncMock(return_value=self.under)
        counter=itertools.count(1)
        self.ib.client.getReqId=Mock(side_effect=lambda:next(counter))
        self.ib.client.reqMktData=Mock(side_effect=self.respond)
        self.ib.client.cancelMktData=Mock();self.ib.client.reqMarketDataType=Mock()
        self.skip=set();self.no_bid=set();self.permission=set();self.zero=set()

    def respond(self,rid,c,generic,snapshot,regulatory,options):
        self.assertFalse(snapshot);self.assertFalse(regulatory)
        if c.conId in self.skip:return
        w=self.ib.wrapper
        if c.conId in self.permission:
            asyncio.get_running_loop().call_soon(self.ib.errorEvent.emit,rid,354,'OPRA subscription required',c)
            return
        w.tcpDataArrived()
        w.priceSizeTick(rid,1,-1 if c.conId in self.no_bid else 0 if c.conId in self.zero else .5,10)
        w.priceSizeTick(rid,2,.6,10)
        if c.secType=='OPT':w.tickOptionComputation(rid,13,0,.3,-.1,.55,0,.01,.02,-.03,100)
        w.tcpDataProcessed()

    def definition(self,n=3):
        return dict(symbol='TEST',underConid=999,expiry='20990116',expirations=['20990116'],served_by='tws',puts=[dict(conid=i+1000,symbol='TEST put',root='TEST',secType='OPT',right='P',expiry='20990116',strike=50+i,underConid=999,currency='USD',mult=100,tradingClass='TEST') for i in range(n)])

    async def asyncTearDown(self):
        tasks=[x.task for x in getattr(self.e,'_bpsx_streams',{}).values() if x.task]
        s.close_all(self.e)
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    async def test_full_chain_above_old_32_strike_cutoff_returns_immediately_and_updates(self):
        with patch.dict('os.environ',{'PAPER_BPSX_LINES':'48'}),patch.object(s,'BATCH_SECONDS',.1):
            started=time.monotonic();stream=s.start(self.e,self.definition(41),100)
            out=stream.snapshot()
            self.assertLess(time.monotonic()-started,.1)
            self.assertEqual(len(out['puts']),41)
            deadline=time.monotonic()+3
            while stream.snapshot()['readyCount']<41 and time.monotonic()<deadline:await asyncio.sleep(.05)
            self.assertEqual(stream.snapshot()['readyCount'],41)
            requested={c.args[1].conId for c in self.ib.client.reqMktData.call_args_list}
            self.assertTrue({r['conid'] for r in out['puts']}.issubset(requested))

    async def test_chain_owned_ticker_cannot_overwrite_or_cancel_browser_owner(self):
        c=Option('TEST','20990116',80,'P','SMART',conId=1001)
        rid=self.ib.client.getReqId();browser_ticker=self.ib.wrapper.startTicker(rid,c,'mktData')
        handle=self.e._open_chain_stream(c)
        self.assertIsNot(handle['ticker'],browser_ticker)
        self.assertEqual(self.ib.wrapper.ticker2ReqId['mktData'][browser_ticker],rid)
        self.e._close_chain_stream(handle)
        self.ib.client.cancelMktData.assert_called_once_with(handle['id'])
        self.assertIn(rid,self.ib.wrapper.reqId2Ticker)
        self.assertNotIn(handle['id'],self.ib.wrapper.reqId2Ticker)

    async def test_zero_bid_retained_and_missing_bid_not_mislabeled_zero_or_ready(self):
        self.zero.add(1000);self.no_bid.add(1001)
        with patch.object(s,'BATCH_SECONDS',.08):
            stream=s.start(self.e,self.definition(2),100)
            await asyncio.sleep(.25)
            rows={r['conid']:r for r in stream.snapshot()['puts']}
            self.assertEqual(rows[1000]['quote']['bid'],0)
            self.assertEqual(rows[1000]['quoteState'],'READY')
            self.assertIsNone(rows[1001]['quote']['bid'])
            self.assertEqual(rows[1001]['quoteState'],'NO_MARKET')

    async def test_permission_error_has_actual_ib_code_and_no_fake_market(self):
        self.permission.add(1000)
        stream=s.start(self.e,self.definition(1),100)
        await asyncio.sleep(.15)
        row=stream.snapshot()['puts'][0]
        self.assertEqual(row['quoteState'],'PERMISSION')
        self.assertIn('354',row['quoteError'])
        self.assertNotIn('quote',row)

    async def test_close_releases_owned_ids_and_underlying(self):
        stream=s.start(self.e,self.definition(3),100)
        await asyncio.sleep(.15)
        owned=set(self.ib.wrapper.reqId2Ticker)
        self.assertTrue(owned)
        stream.close();await asyncio.gather(stream.task,return_exceptions=True)
        self.assertFalse(owned.intersection(self.ib.wrapper.reqId2Ticker))
        self.assertTrue(stream.closed)

    async def test_frozen_is_explicit_and_restores_default_for_following_requests(self):
        stream=s.start(self.e,self.definition(1),100,'frozen')
        await asyncio.sleep(.12)
        self.assertEqual(stream.snapshot()['dataMode'],'frozen')
        types=[c.args[0] for c in self.ib.client.reqMarketDataType.call_args_list]
        self.assertEqual(types,[2,1,2,1])

if __name__=='__main__':unittest.main()
