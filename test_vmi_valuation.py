import datetime as dt
import json
import time
import unittest
from unittest.mock import patch
import vmi_valuation as v
import vmi_sp500 as api


class ValuationTests(unittest.TestCase):
    def setUp(self):
        api._CACHE.clear()
        self.member=dict(symbol='TEST',industry='Software',sector='Information Technology')
        year=dt.date.today().year-1
        self.dates=[f'{year-4+i}-12-31' for i in range(5)]
        end=(dt.date.today()-dt.timedelta(days=60)).isoformat()
        series={k:[dict(val=base+i*step,end=date) for i,date in enumerate(self.dates)] for k,base,step in [('ocf',100,10),('income',50,5),('capex',20,1)]}
        self.data=dict(series=series,current={k:dict(val=x,end=end,source='fixture') for k,x in dict(ocf=140,income=70,capex=24,cashInvestments=50,debt=20,dilutedShares=10,shares=9,equity=200,revenue=500).items()},market={'EPS next 5Y':'12'},shareClassCount=1)
        self.rate=dict(rate=9,beta=1,riskFree=4,erp=5,source='fixture',formula='risk-free + beta × ERP')
        patcher=patch.object(v,'company_discount',return_value=self.rate);patcher.start();self.addCleanup(patcher.stop)

    def test_macro_five_years_units_conflicts_freshness(self):
        rows=[['Year','T.Bond Rate','Implied ERP (FCFE)']]+[[y,.04,.05] for y in range(2021,2026)]
        result=v.macro_rows(rows,dt.date(2026,10,11))
        self.assertEqual((result['riskFree'],result['erp']),(4,5))
        for bad in [rows[:-1],rows+[[2025,.03,.05]],rows[:-1]+[[2025,4,5]]]:
            with self.assertRaises(ValueError): v.macro_rows(bad,dt.date(2026,10,11))
        with self.assertRaises(ValueError): v.macro_rows(rows,dt.date(2027,1,1))

    def test_company_discount_beta_not_global_constant(self):
        now=int(time.time()*1000);a=dict(riskFree=4,erp=5)
        self.assertEqual(v.discount(.5,a,'source',now)['rate'],6.5)
        self.assertEqual(v.discount(1.5,a,'source',now)['rate'],11.5)
        self.assertEqual(v.discount(0,a,'source',now)['rate'],4)
        for beta,stamp in [(None,now),(float('nan'),now),(-2,now),(1,now-86400001)]:
            with self.assertRaises(ValueError): v.discount(beta,a,'source',stamp)

    def test_sector_methods_not_blanket_dcf(self):
        for industry,sector,method in [('Diversified Banks','Financials','PB'),('Investment Banking & Brokerage','Financials','NI'),('Insurance','Financials','NI'),('Retail REITs','Real Estate','PB'),('Utilities','Utilities','PS'),('Airlines','Industrials','PS'),('Transaction & Payment Processing Services','Financials','FCF'),('Financial Exchanges & Data','Financials','FCF')]:
            self.assertEqual(v.select(dict(self.member,industry=industry,sector=sector),self.data)['method'],method)

    def test_financial_shape_selects_fcf_normalized_ocf(self):
        self.assertEqual(v.select(self.member,self.data)['method'],'FCF')
        self.data['series']['capex'][-1]['val']=120
        self.assertEqual(v.select(self.member,self.data)['method'],'NORMALIZED')
        self.data['series']['capex'][-1]['val']=24;self.data['current']['income']['val']=130
        self.assertEqual(v.select(self.member,self.data)['method'],'OCF')
        self.data['series']['ocf']=self.data['series']['ocf'][:4]
        self.assertIsNone(v.select(self.member,self.data)['method'])

    def test_dcf_diluted_shares_complete_debt(self):
        x,d=v.proposal(self.member,self.data)
        self.assertEqual(d['status'],'READY');self.assertEqual((x['shares'],x['base'],x['discount']),(10,116,9))
        self.data['current']['debt']['completeDebt']=False
        x,d=v.proposal(self.member,self.data)
        self.assertIsNone(x);self.assertIn('Complete short-',d['missing'][0]);self.assertEqual(d['discountEvidence']['rate'],9)

    def test_missing_investments_not_zero_and_stale_debt(self):
        del self.data['current']['cashInvestments'];self.data['current']['cash']=dict(self.data['current']['debt'],val=50)
        x,d=v.proposal(self.member,self.data)
        self.assertIsNone(x);self.assertIn('investments',d['missing'][0])
        self.data['current']['investments']=dict(self.data['current']['cash'],val=0)
        self.assertIsNotNone(v.proposal(self.member,self.data)[0])
        self.data['current']['debt']['end']='2000-01-01'
        self.assertIsNone(v.proposal(self.member,self.data)[0])

    def test_no_valuation_acquisition_for_eliminated_business(self):
        with patch.object(v,'company_discount') as rate,patch.object(v,'historical_multiple') as multiple:
            self.assertIsNone(v.proposal(self.member,self.data,acquire=False)[0]);rate.assert_not_called();multiple.assert_not_called()

    def test_share_class_and_forecast_horizon(self):
        self.data['shareClassCount']=2
        self.assertIsNone(v.proposal(self.member,self.data)[0])
        self.data['shareClassCount']=1;self.data['market']={'Forecast Growth':'12','Forecast Horizon':'+1y'}
        x,d=v.proposal(self.member,self.data)
        self.assertIsNone(x);self.assertIn('no one-year substitution',d['missing'][0])

    def test_bank_five_year_pb_never_four(self):
        bank=dict(self.member,industry='Diversified Banks',sector='Financials')
        history=[dict(end=date,val=1+i*.1,source='fixture') for i,date in enumerate(self.dates)]
        quote=dict(source='fixture',retrievedAt=int(time.time()*1000))
        with patch.object(v,'quote_field',return_value=(20,quote)),patch.object(v,'historical_multiple',return_value=history) as fetch:
            x,d=v.proposal(bank,self.data)
            self.assertEqual((x['method'],x['base'],x['shares']),('PB',20,1));self.assertAlmostEqual(x['multiple'],1.2);self.assertNotIn('discount',x)
            api._CACHE.clear();fetch.return_value=history[1:]
            x,d=v.proposal(bank,self.data)
            self.assertIsNone(x);self.assertIn('available 4',d['missing'][0])

    def test_ni_consistency_required(self):
        member=dict(self.member,sector='Financials',industry='Asset Management')
        self.assertEqual(v.proposal(member,self.data)[0]['method'],'NI')
        self.data['series']['income'][-1]['val']=-10
        self.assertIsNone(v.proposal(member,self.data)[0])

    def test_reit_yield_hurdle_and_cyclical_ps(self):
        history=[dict(end=date,val=2,source='fixture') for date in self.dates];quote=dict(source='fixture',retrievedAt=int(time.time()*1000))
        with patch.object(v,'quote_field',side_effect=lambda ticker,key:(20 if key=='Book/sh' else .5,quote)),patch.object(v,'historical_multiple',return_value=history):
            x,d=v.proposal(dict(self.member,industry='Retail REITs',sector='Real Estate'),self.data)
            self.assertEqual((x['multiple'],x['yieldPriceCap']),(1.2,10))
            x,d=v.proposal(dict(self.member,industry='Utilities',sector='Utilities'),self.data)
            self.assertEqual((x['method'],x['base']),('PS',500))

    def test_psg_revenue_growth_not_eps(self):
        self.data['current']['income']['val']=-1;self.data['market']['Revenue Growth Forecast']='80'
        self.assertEqual(v.select(self.member,self.data)['method'],'PSG')
        row=dict(source='fixture',retrievedAt=int(time.time()*1000),fields={'Revenue Forecast Horizon':'+1y'})
        with patch.object(v,'quote_field',side_effect=lambda ticker,key:(80 if key=='Revenue Growth Forecast' else 5,row)):
            x,d=v.proposal(self.member,self.data)
            self.assertEqual((x['method'],x['revenueGrowth']),('PSG',80));self.assertNotIn('discount',x)

    def test_multiple_identity(self):
        raw=dict(timeseries=dict(result=[dict(meta=dict(symbol=['OTHER']),annualPbRatio=[])]))
        with patch.object(v.v,'fetch',return_value=json.dumps(raw)):
            with self.assertRaises(ValueError): v.historical_multiple('TEST','PB')

if __name__=='__main__': unittest.main()
