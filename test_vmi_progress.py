import threading
import time
import unittest
import urllib.error
from unittest.mock import patch
import vmi_data as data
import vmi_sources as sources
import vmi_sp500 as api
import vmi_trace as trace


class ProgressTests(unittest.TestCase):
    def setUp(self):
        api._CACHE.clear();trace._RECORDS.clear()
        sources._FINVIZ_UNTIL=0;sources._YAHOO_UNTIL=0
        self.member=dict(symbol='TEST',industry='Software',sector='IT')
    def tearDown(self):
        sources._FINVIZ_UNTIL=0;sources._YAHOO_UNTIL=0

    def test_deferred_bulk_never_waits_for_individual_fallbacks(self):
        with patch.object(api,'members',return_value={'rows':[self.member]}),patch.object(api,'bulk',side_effect=ValueError('throttled')) as bulk,patch.object(api,'snapshot') as snapshot,patch.object(api,'yahoo_market') as yahoo:
            with trace.task('bulk1','Bulk TEST'): out=api.batch('financial','TEST',defer=True)
            snapshot.assert_not_called();yahoo.assert_not_called()
            self.assertEqual(out['missing'],['TEST']);self.assertEqual(out['deferred'],['TEST']);self.assertEqual(out['rows'],[])
            bulk.assert_called_once_with('financial',['TEST'],fast=True)
        self.assertEqual(trace.status('bulk1')['requests'][0]['state'],'completed')

    def test_single_recovery_reused_across_views_without_qualification(self):
        row=dict(symbol='TEST',fields={'ROE':'20','EPS next 5Y':'10'},source='fixture',retrievedAt=int(time.time()*1000))
        with patch.object(api,'members',return_value={'rows':[self.member]}),patch.object(api,'snapshot',return_value=row) as fetch:
            out=api.recover_one('TEST');api.recover_one('TEST');fetch.assert_called_once()
            self.assertFalse(out['rows'][0]['screen']['rejected']);self.assertNotIn('status',out['rows'][0])
            for view in ('financial','growth'):
                with patch.object(api,'bulk') as bulk:
                    self.assertEqual(api.batch(view,'TEST',defer=True)['rows'][0]['fields'],row['fields']);bulk.assert_not_called()

    def test_failed_pages_shared_across_views_then_retry_after_expiry(self):
        with patch.object(api,'bulk',side_effect=ValueError('blocked')) as bulk,patch.object(api,'snapshot',side_effect=ValueError('blocked')) as snap,patch.object(api,'yahoo_market',side_effect=ValueError('three pages absent')) as yahoo:
            first=api.market_fields('TEST');second=api.market_fields('TEST')
            self.assertEqual(first['fields'],{});self.assertEqual(second['fields'],{})
            self.assertEqual(snap.call_count,1);self.assertEqual(yahoo.call_count,1);self.assertEqual(bulk.call_count,2)
            self.assertEqual(len(first['attempts']),2)
            for key,(at,value) in list(api._CACHE.items()): api._CACHE[key]=(at-121,value)
            api.market_fields('TEST');self.assertEqual(yahoo.call_count,2)

    def test_recovered_rows_survive_original_81_minute_run(self):
        row=dict(symbol='TEST',fields={'ROE':'20'},source='dated fixture',retrievedAt=123)
        with patch.object(api,'members',return_value={'rows':[self.member]}),patch.object(api,'snapshot',return_value=row) as snap,patch.object(api,'bulk') as bulk:
            api.recover_one('TEST')
            for key,(at,value) in list(api._CACHE.items()): api._CACHE[key]=(at-4861,value)
            result=api.market_fields('TEST')
            self.assertEqual(result['fields']['ROE'],'20');snap.assert_called_once();bulk.assert_not_called()
            self.assertEqual(result['fieldSources']['ROE'],dict(source='dated fixture',retrievedAt=123))

    def test_parallel_failed_views_share_one_fallback_chain(self):
        from concurrent.futures import ThreadPoolExecutor
        def missing(ticker): time.sleep(.02);raise ValueError('absent modules')
        with patch.object(api,'bulk',side_effect=ValueError('blocked')),patch.object(api,'snapshot',side_effect=ValueError('blocked')),patch.object(api,'yahoo_market',side_effect=missing) as yahoo,ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:api.market_fields('TEST'),range(8)))
        self.assertTrue(all(r['fields']=={} for r in results));yahoo.assert_called_once_with('TEST')

    def test_failed_recovery_remains_missing(self):
        with patch.object(api,'members',return_value={'rows':[self.member]}),patch.object(api,'snapshot',side_effect=ValueError('blocked')),patch.object(api,'yahoo_market',side_effect=ValueError('blocked')):
            out=api.recover_one('TEST')
        self.assertEqual(out['rows'],[]);self.assertEqual(out['missing'],['TEST']);self.assertEqual(len(out['attempts']),2)

    def test_finviz_fast_no_retry_and_immediate_cooldown(self):
        error=urllib.error.HTTPError('https://finviz.com/',429,'blocked',{},None)
        with patch.object(data,'fetch',side_effect=error) as fetch:
            with self.assertRaises(urllib.error.HTTPError): sources.finviz_fetch('https://finviz.com/',fast=True)
            with self.assertRaises(ValueError): sources.finviz_fetch('https://finviz.com/',fast=True)
            fetch.assert_called_once_with('https://finviz.com/',retry_429=False,timeout=8,retries=0)
        self.assertGreater(sources.cooldowns()['finvizSeconds'],0)

    def test_yahoo_block_does_not_retry_all_pages_and_tickers(self):
        error=urllib.error.HTTPError('https://finance.yahoo.com/',429,'blocked',{},None)
        with patch.object(data,'fetch',side_effect=error) as fetch:
            for ticker in ('TEST','OTHER'):
                with self.assertRaises(ValueError): sources.yahoo_market(ticker)
            self.assertEqual(fetch.call_count,1)

    def test_diagnostics_live_scoped_and_bounded(self):
        entered=threading.Event();release=threading.Event()
        def work():
            with trace.task('slow1','Detailed TEST'):
                trace.event('download','Waiting for response',provider='data.sec.gov');entered.set();release.wait(2)
        thread=threading.Thread(target=work);thread.start();self.assertTrue(entered.wait(1))
        try:
            row=trace.status('slow1')['requests'][0]
            self.assertEqual(row['phase'],'download');self.assertEqual(row['state'],'running');self.assertEqual(trace.status('another')['requests'],[])
        finally: release.set();thread.join(2)
        self.assertEqual(trace.status('slow1')['requests'][0]['state'],'completed')
        with trace.task('bounded','fixture'):
            for _ in range(100): trace.event('cache_hit','snapshot')
        self.assertEqual(len(trace.status('bounded')['requests'][0]['events']),30)
        with self.assertRaises(ValueError): trace.status('../invalid')
        with self.assertRaises(ValueError): trace.status(','.join(['a']*13))

if __name__=='__main__': unittest.main()
