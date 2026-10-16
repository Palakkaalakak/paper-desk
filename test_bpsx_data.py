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
        e._ib=SimpleNamespace(reqContractDetailsAsync=AsyncMock(return_value=details),reqSecDefOptParamsAsync=AsyncMock(return_value=[SimpleNamespace(exchange='SMART',tradingClass='META',multiplier='100',expirations={'20990116'})]))
        async def snapshot(c,require_greeks=False):
            return dict(brokerConid=c.conId,bid=.5,ask=.6,status='LIVE',at=int(time.time()*1000),greeks={'delta':-.1})
        e._snapshot_quote=AsyncMock(side_effect=snapshot)
        return e,under,details

    async def test_verified_puts_and_fresh_owned_quotes_not_cached_prices(self):
        e,u,details=self.fixture();out=await e._bpsx_chain('META','20990116',100)
        self.assertEqual([r['right'] for r in out['puts']],['P','P'])
        self.assertEqual(out['calls'],[])
        self.assertEqual(out['quotedCount'],2)
        self.assertTrue(e._snapshot_quote.call_args.kwargs['require_greeks'])
        self.assertNotIn('quote',e._bpsx_definitions[('META','20990116',0)][1]['puts'][0])
        e._ib.reqContractDetailsAsync.reset_mock()
        await e._bpsx_chain('META','20990116',100,(1,2))
        e._ib.reqContractDetailsAsync.assert_not_awaited()
        self.assertEqual(e._snapshot_quote.await_count,4)

    async def test_invalid_puts_and_foreign_ids_fail_closed(self):
        e,u,details=self.fixture()
        with self.assertRaises(ValueError):await e._bpsx_chain('META','20990116',100,(999,))
        self.assertEqual(e._snapshot_quote.await_count,0)
        details[0].contract.multiplier='10'
        with self.assertRaises(ValueError):v.contract(details[0],u,'P')
        details[0].contract.multiplier='100';details[0].contract.right='C'
        with self.assertRaises(ValueError):v.contract(details[0],u,'P')

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
