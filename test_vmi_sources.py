import json
import unittest
from unittest.mock import patch
import vmi_sources as sources
import vmi_sp500 as api

class RecoveryTests(unittest.TestCase):
    def html(self,symbol='AAPL',period='+1y'):
        payload={'quoteSummary':{'result':[{'price':{'symbol':symbol,'currency':'USD','marketCap':{'raw':123}},'financialData':{'financialCurrency':'USD','returnOnEquity':{'raw':.25},'grossMargins':{'raw':.5}},'earningsTrend':{'trend':[{'period':period,'growth':{'raw':.1}}]}}]}}
        return '<script type="application/json">'+json.dumps({'body':json.dumps(payload)})+'</script>'
    def test_public_embedded_financials_and_horizon(self):
        r=sources.decode_yahoo(self.html(),'AAPL','fixture')
        self.assertEqual(r['fields']['ROE'],'25.0')
        self.assertEqual(r['fields']['Forecast Horizon'],'+1y')
        self.assertNotIn('EPS next 5Y',r['fields'])
        self.assertNotIn('ROIC',r['fields'])
    def test_actual_long_term_estimate_preserved(self):
        r=sources.decode_yahoo(self.html(period='+5y'),'AAPL','fixture')
        self.assertEqual(r['fields']['EPS next 5Y'],'10.0')
    def test_identity_mismatch_fails(self):
        with self.assertRaises(ValueError): sources.decode_yahoo(self.html(symbol='MSFT'),'AAPL','fixture')
    def test_missing_analysis_module_recovers_from_statistics(self):
        with patch.object(sources.v,'fetch',side_effect=['<html>No financial module</html>',self.html()]) as fetch:
            row=sources.yahoo_market('AAPL')
        self.assertEqual(row['fields']['ROE'],'25.0')
        self.assertTrue(row['source'].endswith('/key-statistics/'))
        self.assertEqual(len(row['attempts']),1)
        self.assertEqual(fetch.call_count,2)

    def test_page_recovery_never_accepts_wrong_listing(self):
        with patch.object(sources.v,'fetch',return_value=self.html(symbol='MSFT')) as fetch:
            with self.assertRaises(ValueError): sources.yahoo_market('AAPL')
        self.assertEqual(fetch.call_count,3)

    def test_batch_recovers_from_independent_source(self):
        api._CACHE.clear()
        member=dict(symbol='AAPL',industry='Hardware',sector='IT')
        with patch.object(api,'members',return_value={'rows':[member]}),patch.object(api,'bulk',side_effect=ValueError('throttled')),patch.object(api,'snapshot',side_effect=ValueError('cooldown')),patch.object(api,'yahoo_market',return_value=dict(symbol='AAPL',fields={'ROE':'25'},source='Yahoo')) as fallback:
            r=api.batch('financial','AAPL');self.assertEqual(r['missing'],[]);fallback.assert_called_once_with('AAPL')

class StatementScraperTests(unittest.TestCase):
    def html(self,rows,ticker='TEST',header='2025',date='12/31/2025'):
        from html import escape
        text='<script type="application/ld+json">'+json.dumps({'tickerSymbol':ticker})+'</script>'
        text+='<p>All values are USD millions unless otherwise specified</p><table class="financials-table"><tr><th>Metric</th><th>'+header+'</th></tr><tr><td>Period end date</td><td>'+date+'</td></tr>'
        for label,shown,raw in rows:
            text+='<tr><td>'+escape(label)+'</td><td'+(' data-value="'+str(raw)+'"' if raw is not None else '')+'>'+escape(shown)+'</td></tr>'
        return text+'</table>'

    def decode(self,text,ticker='TEST'):
        from vmi_web import decode
        return decode(text,ticker,'https://fixture.test/financials/')

    def test_identity_currency_share_class_and_future_date(self):
        rows=[('Total Revenue','100',100000000)]
        self.assertEqual(self.decode(self.html(rows,'BRK-B'),'BRK.B')['symbol'],'BRK.B')
        for text in [self.html(rows,'OTHER'),self.html(rows).replace('USD','EUR'),self.html(rows,date='12/31/2099')]:
            with self.assertRaises(ValueError): self.decode(text)

    def test_missing_zero_and_units(self):
        data=self.decode(self.html([('Total Revenue','0',0),('Total Debt','2',2000000000),('Interest Expense','N/A',0),('Cash & Equivalents','2',None),('Weighted Average Diluted Shares Outstanding','2M',2000000)]))
        self.assertEqual(data['series']['revenue'][0]['val'],0)
        for key in ['debt','interest','cash']: self.assertNotIn(key,data['series'])
        self.assertEqual(data['series']['dilutedShares'][0]['unit'],'shares')

    def test_annual_quarterly_and_sources_remain_distinct(self):
        data=self.decode(self.html([('Total Revenue','100',100000000)])+self.html([('Total Revenue','25',25000000)],header='Q1 2025',date='03/31/2025'))
        self.assertEqual(data['series']['revenue'][0]['val'],100000000)
        q=data['quarterly']['revenue'][0];self.assertEqual(q['val'],25000000)
        self.assertEqual(q['end'],'2025-03-31');self.assertIn('not TTM or YTD',q['basis'])
        self.assertEqual(q['source'],'https://fixture.test/financials/');self.assertGreater(q['retrievedAt'],0)

    def test_same_label_conflicts_rejected(self):
        a=self.html([('Total Revenue','100',100000000)])
        self.assertEqual(len(self.decode(a+a)['series']['revenue']),1)
        with self.assertRaisesRegex(ValueError,'Conflicting'): self.decode(a+self.html([('Total Revenue','101',101000000)]))

    def test_conflict_quarantines_only_affected_metric_and_period(self):
        label='Earnings before Interest, Taxes, Depreciation and Amortization (EBITDA)'
        text=self.html([('Total Revenue','100',100000000),(label,'10',10000000)])
        text+=self.html([(label,'11',11000000)])+self.html([(label,'10',10000000)])
        data=self.decode(text)
        self.assertEqual(data['series']['revenue'][0]['val'],100000000)
        self.assertEqual(data['series']['ebitda'],[])
        self.assertEqual(len(data['attempts'][0]['observations']),3)
        self.assertIn('excluded',data['attempts'][0]['error'])
        with patch('vmi_web.v.fetch',return_value=text):
            from vmi_web import statements
            self.assertEqual(len(statements('TEST')['attempts']),1)

    def test_capex_total_preferred_with_alternative_retained(self):
        data=self.decode(self.html([('Purchase of Property, Plant & Equipment','-10',-10000000),('Capital Expenditures (CapEx)','12',12000000),('Total Interest Expense','2',2000000)]))
        self.assertEqual(data['series']['capex'][0]['val'],12000000)
        self.assertEqual(data['alternatives'][0]['alternative']['val'],10000000)
        self.assertEqual(data['series']['interest'][0]['val'],2000000)

    def test_income_statement_preferred_over_reconciliation_without_losing_alternative(self):
        income=self.html([('Consolidated Net Income / (Loss)','1,062',1062000000)]).replace('<p>','<h3>Annual Income Statements</h3><p>')
        cash=self.html([('Consolidated Net Income / (Loss)','1,062',1062100000)]).replace('<p>','<h3>Annual Cash Flow Statements</h3><p>')
        for text in (income+cash,cash+income):
            data=self.decode(text)
            self.assertEqual(data['series']['income'][0]['val'],1062000000)
            self.assertEqual(data['series']['income'][0]['statement'],'Income Statements')
            self.assertEqual(data['alternatives'][0]['alternative']['val'],1062100000)
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            self.decode(income+income.replace('1062000000','1062100000'))

    def test_venue_recovery_keeps_identity_validation(self):
        from vmi_web import statements
        rows=[('Total Revenue','100',100000000)]
        with patch('vmi_web.v.fetch',side_effect=[self.html(rows,'WRONG'),self.html(rows)]) as fetch:
            data=statements('TEST',['NYSE'])
        self.assertEqual(fetch.call_count,2);self.assertIn('/NASDAQ/',data['source']);self.assertEqual(len(data['attempts']),1)

if __name__=='__main__':unittest.main()
