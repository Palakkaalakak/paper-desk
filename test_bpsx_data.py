"""Read-only BPSX IB acquisition regressions. All broker responses are fixtures."""
import datetime as dt
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from market import MarketEngine
import bpsx_data as b
import vmi_options as v

class BpsxTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        from ib_async import Stock, Option, ContractDetails
        e=MarketEngine();under=Stock('META','SMART','USD',conId=100)
        details=[ContractDetails(contract=Option('META','20990116',strike,'P','SMART',conId=i,multiplier='100',currency='USD',tradingClass='META',localSymbol=f'META  990116P{strike*1000:08d}'),underConId=100,underSecType='STK') for i,strike in [(1,80),(2,85)]]
        e._stock=AsyncMock(return_value=under)
        calls=[ContractDetails(contract=Option('META','20990116',strike,'C','SMART',conId=i+100,multiplier='100',currency='USD',tradingClass='META',localSymbol=f'META  990116C{strike*1000:08d}'),underConId=100,underSecType='STK') for i,strike in [(1,80),(2,85)]]
        e._ib=SimpleNamespace(reqContractDetailsAsync=AsyncMock(side_effect=lambda c:details if c.right=='P' else calls),reqSecDefOptParamsAsync=AsyncMock(return_value=[SimpleNamespace(exchange='SMART',tradingClass='META',multiplier='100',expirations={'20990116'})]))
        async def snapshot(c,require_greeks=False):
            return dict(brokerConid=c.conId,bid=.5,ask=.6,status='LIVE',at=int(time.time()*1000),greeks={'delta':-.1})
        e._snapshot_quote=AsyncMock(side_effect=snapshot)
        return e,under,details

    async def test_verified_puts_and_fresh_owned_quotes_not_cached_prices(self):
        from unittest.mock import patch
        e,u,details=self.fixture()
        def start(engine,definition,spot,mode):
            out=dict(definition)
            out['puts']=[dict(r,quote=dict(brokerConid=r['conid'],bid=.5,ask=.6,status='LIVE',at=int(time.time()*1000),greeks={'delta':-.1})) for r in definition['puts']]
            return SimpleNamespace(snapshot=lambda:out,priority=[],closed=False)
        with patch('bpsx_stream.start',side_effect=start) as starter:
            out=await e._bpsx_chain('META','20990116',100)
            self.assertEqual([r['right'] for r in out['puts']],['P','P'])
            self.assertEqual([r['right'] for r in out['calls']],['C','C'])
            self.assertNotIn('quote',e._bpsx_definitions[('META','20990116',0)][1]['puts'][0])
            e._ib.reqContractDetailsAsync.reset_mock()
            await e._bpsx_chain('META','20990116',100,(1,2))
            e._ib.reqContractDetailsAsync.assert_not_awaited()
            self.assertEqual(starter.call_count,2)
        e._snapshot_quote.assert_not_awaited()

    async def test_invalid_puts_and_foreign_ids_fail_closed(self):
        e,u,details=self.fixture()
        with self.assertRaises(ValueError):await e._bpsx_chain('META','20990116',100,(999,))
        self.assertEqual(e._snapshot_quote.await_count,0)
        details[0].contract.multiplier='10'
        with self.assertRaises(ValueError):v.contract(details[0],u,'P')
        details[0].contract.multiplier='100';details[0].contract.right='C'
        with self.assertRaises(ValueError):v.contract(details[0],u,'P')

    def test_window_contains_only_existing_contiguous_strikes_and_preserves_adjacency(self):
        strikes=[50+i+(0.5 if i>80 else 0) for i in range(199)]
        definitions=dict(puts=[dict(conid=i+1,strike=k) for i,k in enumerate(strikes)],
            calls=[dict(conid=i+1000,strike=k) for i,k in enumerate(strikes)])
        out=b.strike_window(definitions,160,25,90)
        self.assertEqual(len(out['puts']),25)
        self.assertEqual(len(out['calls']),25)
        self.assertEqual(out['listedPutCount'],199)
        self.assertEqual(out['listedStrikes'],strikes)  # ATM uses only actual qualified strikes.
        positions=[strikes.index(r['strike']) for r in out['puts']]
        self.assertEqual(positions,list(range(positions[0],positions[0]+25)))
        self.assertEqual({r['strike'] for r in out['calls']},{r['strike'] for r in out['puts']})
        self.assertEqual(len(definitions['puts']),199)
        for count,center in [(199,90),(25,float('nan')),(13,49)]:
            with self.assertRaises(ValueError):b.strike_window(definitions,160,count,center)
        shifted=b.strike_window(definitions,160,25,90,(1,2))
        self.assertEqual([r['conid'] for r in shifted['puts'][:2]],[1,2])
        with self.assertRaises(ValueError):b.strike_window(definitions,160,25,90,(1,100))

    async def test_market_cap_uses_millions_and_average_volume_not_intraday_volume(self):
        from ib_async import ScannerSubscription
        e,u,d=self.fixture()
        e._ib.reqScannerDataAsync=AsyncMock(return_value=[SimpleNamespace(contractDetails=SimpleNamespace(contract=u,longName='Meta',industry='Software',category='Technology'))])
        out=await b.scan(e);sub=e._ib.reqScannerDataAsync.call_args.args[0]
        self.assertEqual(sub.marketCapAbove,30000)
        self.assertEqual(sub.abovePrice,50)
        self.assertEqual(sub.scanCode,'HIGH_OPT_IMP_VOLAT')
        self.assertEqual(sub.aboveVolume,ScannerSubscription().aboveVolume)
        self.assertTrue(out['rows'][0]['marketCapScreen'])

    async def test_incomplete_current_bar_removed_and_missing_iv_explicit(self):
        e,u,d=self.fixture();today=dt.datetime.now(dt.timezone.utc).date()
        def bar(date):return SimpleNamespace(date=date,open=100.,high=101.,low=99.,close=100.,volume=2000000.)
        async def hist(*args,**kw):
            if kw['whatToShow']=='OPTION_IMPLIED_VOLATILITY':raise ValueError('No IV entitlement')
            return [bar(today-dt.timedelta(days=1)),bar(today)]
        e._ib.reqHistoricalDataAsync=AsyncMock(side_effect=hist)
        out=await e._bpsx_stock('META')
        self.assertEqual(len(out['bars']),1)
        self.assertEqual(out['ivHistory'],[])
        self.assertIn('No IV entitlement',out['ivError'])
        self.assertIsNone(out['earningsDate'])

if __name__=='__main__':unittest.main()
