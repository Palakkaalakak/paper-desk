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

if __name__=='__main__':unittest.main()
