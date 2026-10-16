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
        self.skip=set();self.no_bid=set();self.permission=set();self.zero=set();self.no_greeks=set();self.bid_only=set()

    def respond(self,rid,c,generic,snapshot,regulatory,options):
        self.assertFalse(snapshot);self.assertFalse(regulatory)
        if c.conId in self.skip:return
        w=self.ib.wrapper
        if c.conId in self.permission:
            asyncio.get_running_loop().call_soon(self.ib.errorEvent.emit,rid,354,'OPRA subscription required',c)
            return
        w.tcpDataArrived()
        w.priceSizeTick(rid,1,-1 if c.conId in self.no_bid else 0 if c.conId in self.zero else .5,10)
        if c.conId not in self.bid_only:w.priceSizeTick(rid,2,.6,10)
        if c.secType=='OPT' and c.conId not in self.no_greeks:w.tickOptionComputation(rid,13,0,.3,-.1 if c.right=='P' else .9,.55,0,.01,.02,-.03,100)
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

    async def test_extreme_wide_live_market_preserves_raw_prices_and_original_receipt(self):
        stream=s.start(self.e,self.definition(1),100)
        await asyncio.sleep(.15)
        h=stream.handles[1000];w=self.ib.wrapper
        w.tcpDataArrived()
        w.priceSizeTick(h['id'],1,.1,10)
        w.priceSizeTick(h['id'],2,20,10)
        w.tcpDataProcessed()
        stream.capture(1000,h)
        q=stream.snapshot()['puts'][0]['quote']
        self.assertEqual((q['bid'],q['ask']),(.1,20))
        self.assertEqual(q['status'],'LIVE')
        self.assertEqual(q['brokerConid'],1000)
        self.assertEqual(q['source'],'IB Gateway')
        original=q['at']
        await asyncio.sleep(.03)
        again=stream.snapshot()['puts'][0]['quote']
        self.assertEqual(again['at'],original)
        self.assertEqual(stream.snapshot()['puts'][0]['displayQuote']['ask'],20)
        self.assertEqual(again['ask']-again['bid'],19.9)

    async def test_permission_error_has_actual_ib_code_and_no_fake_market(self):
        self.permission.add(1000)
        stream=s.start(self.e,self.definition(1),100)
        await asyncio.sleep(.15)
        row=stream.snapshot()['puts'][0]
        self.assertEqual(row['quoteState'],'PERMISSION')
        self.assertIn('354',row['quoteError'])
        self.assertNotIn('quote',row)

    async def test_put_gate_requires_both_sides_but_not_greeks_and_calls_can_follow(self):
        definition=self.definition(2)
        definition['calls']=[dict(r,conid=r['conid']+100,right='C') for r in definition['puts']]
        self.skip.update([1100,1101]);self.no_greeks.add(1000);self.bid_only.add(1001)
        stream=s.start(self.e,definition,100)
        await asyncio.sleep(.25)
        out=stream.snapshot()
        self.assertFalse(out['putsComplete'])
        self.assertEqual(out['putResolvedCount'],1)
        self.assertTrue(out['puts'][0]['bidAskResolved'])
        self.assertFalse(out['puts'][1]['bidAskResolved'])
        # Missing ask arrives separately; table gate must not wait for either Greek or call.
        h=stream.handles[1001];w=self.ib.wrapper
        w.tcpDataArrived();w.priceSizeTick(h['id'],2,.6,10);w.tcpDataProcessed()
        out=stream.snapshot()
        self.assertTrue(out['putsComplete'])
        self.assertEqual(out['callsResolvedCount'],0)

    async def test_timeout_and_permission_are_not_successful_put_acquisition(self):
        self.skip.add(1000);self.permission.add(1001)
        with patch.object(s,'BATCH_SECONDS',.05):
            stream=s.start(self.e,self.definition(2),100)
            await asyncio.sleep(.25)
            out=stream.snapshot()
            self.assertFalse(out['putsComplete'])
            self.assertEqual(out['putResolvedCount'],0)
            self.assertEqual(out['puts'][0]['quoteState'],'NO_QUOTE')

    async def test_old_25_second_pause_no_longer_expires_and_window_sessions_are_distinct(self):
        stream=s.start(self.e,self.definition(2),100)
        await asyncio.sleep(.12)
        stream.last_read=time.monotonic()-30
        self.assertIn(stream.id,s.sessions(self.e))
        self.assertEqual(s.read(self.e,stream.id)['streamId'],stream.id)
        other=s.start(self.e,self.definition(3),100)
        self.assertNotEqual(stream.id,other.id)
        stream.last_read=time.monotonic()-s.IDLE_SECONDS-1
        with self.assertRaisesRegex(ValueError,'expired'):s.read(self.e,stream.id)

    async def test_calls_use_call_contracts_and_positive_delta(self):
        definition=self.definition(1)
        definition['calls']=[dict(definition['puts'][0],conid=1100,right='C')]
        stream=s.start(self.e,definition,100)
        await asyncio.sleep(.2)
        call=stream.snapshot()['calls'][0]
        self.assertEqual(call['quoteState'],'READY')
        self.assertEqual(call['quote']['greeks']['delta'],.9)
        requested=[c.args[1] for c in self.ib.client.reqMktData.call_args_list if c.args[1].secType=='OPT']
        self.assertEqual([c.right for c in requested],['P','C'])

    async def test_reopened_partial_stream_retains_display_market_but_not_executable_quote(self):
        stream=s.start(self.e,self.definition(1),100)
        await asyncio.sleep(.12)
        first=stream.snapshot()['puts'][0]
        self.assertTrue(first['bidAskResolved'])
        old_at=first['displayQuote']['at']
        self.bid_only.add(1000)
        stream.cancel(stream.handles.pop(1000))
        c=Option('TEST','20990116',50,'P','SMART',conId=1000)
        stream.handles[1000]=stream.open(c)
        out=stream.snapshot();row=out['puts'][0]
        self.assertFalse(out['putsComplete'])
        self.assertIsNone(row['quote']['ask'])
        self.assertEqual(row['displayQuote']['ask'],.6)
        self.assertEqual(row['displayQuote']['at'],old_at)
        self.assertFalse(s.quote_ready(row['quote']))
        self.assertEqual(out['underlyingQuote']['brokerConid'],999)
        stream.handles[1000]['errors']=[dict(code=354,message='No OPRA')]
        row=stream.snapshot()['puts'][0]
        self.assertNotIn('quote',row)
        self.assertEqual(row['displayQuote']['at'],old_at)
        self.assertEqual(row['quoteState'],'PERMISSION')

    async def test_atm_down_extends_by_observed_delta_without_requesting_itm_puts(self):
        from bpsx_data import strike_window
        definition=strike_window(self.definition(80),100,25)
        def respond(rid,c,*args):
            self.respond(rid,c,*args)
            if c.secType=='OPT':
                w=self.ib.wrapper;w.tcpDataArrived()
                w.tickOptionComputation(rid,13,0,.3,-max(.005,(c.strike-65)/100),.55,0,.01,.02,-.03,100)
                w.tcpDataProcessed()
        self.ib.client.reqMktData.side_effect=respond
        with patch.object(s,'BATCH_SECONDS',.1):
            stream=s.start(self.e,definition,100)
            deadline=time.monotonic()+6
            while not stream.snapshot()['coverageDone'] and time.monotonic()<deadline:await asyncio.sleep(.05)
            out=stream.snapshot()
            self.assertTrue(out['coverageDone'])
            self.assertEqual(out['atmStrike'],100)
            self.assertGreater(len(out['puts']),25)
            self.assertTrue(all(r['strike']<=100 for r in out['puts']))
            self.assertLess(out['puts'][0]['quote']['greeks']['delta']*-1,.09)
            self.assertNotIn('remainingPuts',out)
            requested=[x.args[1].strike for x in self.ib.client.reqMktData.call_args_list if x.args[1].secType=='OPT']
            self.assertLessEqual(max(requested),100)
            self.assertEqual(requested[0],100)

    async def test_atm_reference_uses_owned_share_data_and_cleans_up(self):
        from bpsx_data import reference_quote
        price,q=await reference_quote(self.e,'TEST','live')
        self.assertEqual(price,.55)
        self.assertEqual(q['brokerConid'],999)
        self.assertFalse(self.ib.wrapper.reqId2Ticker)
        self.ib.client.cancelMktData.assert_called_once()

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
