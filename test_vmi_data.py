import json
import unittest
from unittest.mock import patch
import vmi_data as v

class VMIDataTests(unittest.TestCase):
    def test_omitted_closing_row(self):
        p=v.Tables('screener_table');p.feed('<table class="screener_table"><tr><th>Ticker</th><th>Price</th><tr><td>META</td><td>100</td></tr></table>')
        self.assertEqual(p.rows,[['Ticker','Price'],['META','100']])

    def test_scan_valuation_neutral(self):
        html='<table class="screener_table"><tr><th>Ticker</th><th>Price</th><tr><td>META</td><td>100</td></tr></table>'
        with patch.object(v,'fetch',return_value=html): result=v.scan()
        self.assertEqual(result['rows'][0]['symbol'],'META')
        self.assertFalse(result['valuationFiltered'])
        for mode,page in [('other',0),('moderate',50),('moderate',-1)]:
            with self.assertRaises(ValueError): v.scan(mode,page)

    def test_numeric_and_symbol_validation(self):
        self.assertEqual(v.number('1.2B'),1.2e9)
        self.assertEqual(v.number('12%'),12)
        for s in ['NaN','Infinity','-','12xx']: self.assertIsNone(v.number(s))
        for s in ['../X','A&B','A/B','']:
            with self.assertRaises(ValueError): v.symbol(s)

    def test_annual_only_cutoff_and_conflict(self):
        row={'start':'2023-01-01','end':'2023-12-31','filed':'2024-02-01','val':4,'form':'10-K'}
        facts={'X':{'units':{'USD':[row,dict(row,start='2023-10-01',val=1)]}}}
        self.assertEqual(len(v.observations(facts,['X'],annual=True,cutoff='2024-12-31')),1)
        self.assertEqual(v.observations(facts,['X'],annual=True,cutoff='2024-01-01'),[])
        facts['X']['units']['USD'].append(dict(row,val=5))
        self.assertEqual(v.observations(facts,['X'],annual=True),[])

    def test_newer_alternate_tag_wins_over_old_history(self):
        row={'end':'2023-12-31','filed':'2024-02-01','val':4,'form':'10-K'}
        facts={'Old':{'units':{'USD':[row]}},'New':{'units':{'USD':[dict(row,end='2024-12-31',filed='2025-02-01',val=6)]}}}
        self.assertEqual(v.observations(facts,['Old','New'],cutoff='2025-10-01')[-1]['tag'],'New')

    def test_secondary_currency_and_identity(self):
        data={'timeseries':{'result':[{'meta':{'symbol':['META']},'annualTotalDebt':[
          {'asOfDate':'2024-12-31','currencyCode':'EUR','reportedValue':{'raw':4}},
          {'asOfDate':'2023-12-31','currencyCode':'USD','reportedValue':{'raw':3}}]}]}}
        with patch.object(v,'fetch',return_value=json.dumps(data)):
            result,_=v.secondary('META')
            self.assertEqual([x['val'] for x in result['annualTotalDebt']],[3])
            with self.assertRaises(ValueError): v.secondary('MSFT')

class VMIStockTests(unittest.IsolatedAsyncioTestCase):
    async def test_stock_identity_comes_from_owner_loop_qualification(self):
        from market import MarketEngine
        from unittest.mock import AsyncMock
        from types import SimpleNamespace
        engine=MarketEngine()
        engine._stock=AsyncMock(return_value=SimpleNamespace(symbol='META',conId=123,secType='STK',currency='USD'))
        out=await engine._vmi_stock('meta')
        engine._stock.assert_awaited_once_with('META')
        self.assertEqual(out['conid'],123)
        self.assertEqual(out['served_by'],'tws')
        engine._stock.return_value.currency='EUR'
        with self.assertRaises(ValueError): await engine._vmi_stock('META')
        with self.assertRaises(ValueError): await engine._vmi_stock('../META')

if __name__=='__main__': unittest.main()
