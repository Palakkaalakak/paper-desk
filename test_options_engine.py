"""Offline tests for the Trade-tab option/search engine. No live IBKR connection is used."""
import asyncio
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import market


def chain(cls, exps, strikes=(100.0,), mult='100', exch='SMART'):
    return NS(exchange=exch, underlyingConId=1, tradingClass=cls, multiplier=mult,
              expirations=list(exps), strikes=list(strikes))


def detail(sym, cls, expiry, strike, right, conid, mult='100'):
    return NS(contract=NS(conId=conid, secType='OPT', right=right, strike=strike, symbol=sym,
                          lastTradeDateOrContractMonth=expiry, tradingClass=cls, multiplier=mult))


class GreeksTests(unittest.TestCase):
    def test_model_first_and_sentinels_removed(self):
        g = NS(impliedVol=.3, delta=-.12, gamma=.01, theta=-2, vega=-2, undPrice=101, optPrice=1.2)
        out = market.option_greeks(NS(modelGreeks=g))
        self.assertEqual(out['delta'], -.12)
        self.assertIsNone(out['theta']); self.assertIsNone(out['vega'])
        self.assertEqual(out['source'], 'model'); self.assertEqual(out['undPrice'], 101)

    def test_falls_back_to_quote_computations(self):
        bad = NS(impliedVol=-1, delta=None, gamma=None, theta=None, vega=None, undPrice=-1, optPrice=-1)
        bid = NS(impliedVol=.25, delta=.4, gamma=.02, theta=-.05, vega=.1, undPrice=100, optPrice=2)
        out = market.option_greeks(NS(modelGreeks=bad, bidGreeks=bid))
        self.assertEqual((out['delta'], out['source']), (.4, 'bid'))
        self.assertIsNone(market.option_greeks(NS(modelGreeks=None)))


class ChainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.e = market.MarketEngine()
        self.e._stock = AsyncMock(return_value=NS(conId=1, symbol='SPX'))
        self.saved = {}
        p1 = patch('tws._disk', return_value={}); p2 = patch('tws._disk_save', side_effect=lambda k, r: self.saved.update({k: r}))
        p1.start(); p2.start(); self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    async def test_weekly_class_expiries_merge_and_adjusted_classes_drop(self):
        params = [chain('SPX', ['20991217']), chain('SPXW', ['20991210', '20991217']),
                  chain('2SPX', ['20991203']), chain('SPX', ['20991203'], exch='CBOE'), chain('XSP', ['20991224'], mult='10')]
        self.e._ib = NS(reqSecDefOptParamsAsync=AsyncMock(return_value=params))
        out = await self.e._chain('SPX')
        self.assertEqual(out['expirations'], ['20991210', '20991217'])

    async def test_expiry_requests_its_own_class_once_and_filters(self):
        params = [chain('SPX', ['20991217']), chain('SPXW', ['20991210', '20991217'])]
        details = [detail('SPX', 'SPXW', '20991210', 100.0, 'C', 11), detail('SPX', 'SPXW', '20991210', 100.0, 'P', 12),
                   detail('SPX', 'SPXW', '20991210', 105.0, 'P', 13, mult='10')]
        ib = NS(reqSecDefOptParamsAsync=AsyncMock(return_value=params), reqContractDetailsAsync=AsyncMock(return_value=details))
        self.e._ib = ib
        out = await self.e._chain('SPX', '20991210')
        template = ib.reqContractDetailsAsync.await_args.args[0]
        self.assertEqual((template.tradingClass, template.multiplier, template.right), ('SPXW', '100', ''))
        self.assertEqual(ib.reqContractDetailsAsync.await_count, 1)
        self.assertEqual([r['conid'] for r in out['calls']], [11]); self.assertEqual([r['conid'] for r in out['puts']], [12])
        # Monthly class wins for an expiry both classes list.
        ib.reqContractDetailsAsync.return_value = [detail('SPX', 'SPX', '20991217', 100.0, 'C', 21)]
        await self.e._chain('SPX', '20991217')
        self.assertEqual(ib.reqContractDetailsAsync.await_args.args[0].tradingClass, 'SPX')

    async def test_empty_answers_are_not_cached(self):
        ib = NS(reqSecDefOptParamsAsync=AsyncMock(return_value=[]))
        self.e._ib = ib
        with self.assertRaises(ValueError): await self.e._chain('SPX')
        ib.reqSecDefOptParamsAsync.return_value = [chain('SPX', ['20991217'])]
        ib.reqContractDetailsAsync = AsyncMock(return_value=[])
        with self.assertRaises(ValueError): await self.e._chain('SPX', '20991217')
        self.assertEqual(self.saved, {})


class ParamTests(unittest.TestCase):
    def test_non_numeric_query_value_gives_clear_error(self):
        import serve
        self.assertEqual(serve.num('12.5', 'spot'), 12.5)
        with self.assertRaisesRegex(ValueError, 'Invalid spot value'): serve.num('undefined', 'spot')


class ErrorTests(unittest.TestCase):
    def setUp(self):
        self.e = market.MarketEngine()
        self.ticker = Mock()
        self.e._ib = NS(wrapper=NS(reqId2Ticker={7: self.ticker, 8: self.ticker}, ticker2ReqId={'mktData': {self.ticker: 7}}), cancelMktData=Mock())
        self.e._active = {55: dict(contract=NS(conId=55), ticker=self.ticker)}
        self.e._ticker_ids = {id(self.ticker): {55}}

    def test_not_subscribed_frees_line_and_reports_on_contract(self):
        self.e._error(7, 354, 'Requested market data is not subscribed.')
        self.assertNotIn(55, self.e._active)
        self.e._ib.cancelMktData.assert_called_once()
        self.assertEqual(self.e.quotes[55]['errorCode'], 354)
        self.assertGreater(self.e._failed[55], time.monotonic())

    def test_other_request_for_same_contract_is_not_the_stream(self):
        self.e._error(8, 101, 'Max number of tickers has been reached.')
        self.assertIn(55, self.e._active); self.assertNotIn(55, self.e.quotes)

    def test_partial_warning_keeps_stream_and_annotates(self):
        self.e._error(7, 10167, 'Displaying delayed market data.')
        self.assertIn(55, self.e._active)
        self.assertIn('10167', self.e._notes[55])

    def test_farm_state_is_tracked(self):
        self.e._error(-1, 2157, 'Sec-def data farm connection is broken')
        self.assertTrue(self.e.farm_problem('secdef'))
        self.e._error(-1, 2158, 'Sec-def data farm connection is OK')
        self.assertFalse(self.e.farm_problem('secdef'))


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_symbol_calls_are_spaced_and_exact_symbol_fallback(self):
        e = market.MarketEngine()
        stamps = []
        async def matching(pattern):
            stamps.append(time.monotonic()); return None  # ib_async's 4s timeout answer
        e._ib = NS(reqMatchingSymbolsAsync=matching)
        e._stock = AsyncMock(return_value=NS(symbol='AMZN', conId=3691937, primaryExchange='NASDAQ', secType='STK'))
        out = await e._search('AMZN')
        self.assertEqual(out[0]['conid'], 3691937)
        self.assertGreaterEqual(stamps[1] - stamps[0], 1.0)


if __name__ == '__main__':
    unittest.main()
