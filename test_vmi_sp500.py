import datetime as dt
import unittest
from unittest.mock import patch
import vmi_sp500 as api
import vmi_metrics as m

class ScannerTests(unittest.TestCase):
    def setUp(self): api._CACHE.clear()
    def test_link_identity_not_duplicate_text(self):
        p=api.Screener();p.feed('<table class="screener_table"><tr><th>No.</th><th>Ticker</th><th>ROE</th><tr><td>1</td><td><a href="stock?t=AAPL&ty=c">AAAPL</a></td><td>20%</td><tr><td>2</td><td><a href="stock?t=A&ty=c">AA</a></td><td>15%</td></table>')
        self.assertEqual([r[1] for r in p.rows[1:]],['AAPL','A'])
    def test_labelled_peer_links_are_not_scan_universe(self):
        text='<a href="quote.ashx?t=DECK">DECK</a><table class="snapshot-table2"><tr><td>ROE</td><td>25%</td></tr></table><a href="screener?t=DECK,NKE,ONON">Peers</a>'
        with patch.object(api,'finviz_fetch',return_value=text):
            self.assertEqual(api.snapshot('DECK')['peers'],['NKE','ONON'])
        with patch.object(api,'finviz_fetch',return_value=text.replace('t=DECK,NKE','t=OTHER,NKE')):
            self.assertEqual(api.snapshot('DECK')['peers'],[])
        member=dict(symbol='DECK',cik=1,industry='Footwear',sector='Consumer')
        nike=dict(symbol='NKE',cik=2,industry='Apparel',sector='Different source sector')
        wrong=dict(symbol='BANK',cik=3,industry='Bank',sector='Financials')
        shot=dict(peers=['NKE','ONON'],source='labelled-source',retrievedAt=1)
        row=dict(symbol='NKE',fields={'Gross M':'40%','Profit M':'10%','Market Cap':'50B'},source='financial',retrievedAt=1)
        with patch.object(api,'members',return_value={'rows':[member,nike,wrong]}),patch.object(api,'snapshot',return_value=shot),patch.object(api,'financial_row',return_value=row) as acquire:
            result=api.peer_check(member,dict(market={'Gross M':'50%','Profit M':'20%'}))
        acquire.assert_called_once_with('NKE')
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['selection']['source'],'labelled-source')
        self.assertEqual(result['selection']['symbols'],['NKE'])

    def test_roic_retains_reported_tax_benefit_without_clamping(self):
        series={k:[dict(end='2025-12-31',val=x,source='filed')] for k,x in dict(income=110,operatingIncome=120,tax=-10,pretax=100,equity=200,debt=50,cash=30).items()}
        row=m.calculate(series)['roic'][0]
        self.assertAlmostEqual(row['val'],60)
        self.assertEqual(row['inputs'][1]['val'],-10)
        self.assertIn('tax benefits',row['method'])
        series.pop('tax')
        self.assertEqual(m.calculate(series).get('roic',[]),[])

    def test_membership_retains_share_classes(self):
        rows=[dict(symbol='X'+str(i),name='Issuer',sector='Information Technology',industry='Software',cik=i+1) for i in range(501)]
        rows += [dict(symbol='BRK.B',name='B',sector='Financials',industry='Insurance',cik=900),dict(symbol='BRK.A',name='A',sector='Financials',industry='Insurance',cik=900)]
        self.assertEqual(len(api.validate_members(rows)),503)
        with self.assertRaises(ValueError): api.validate_members(rows[:100])
        with self.assertRaises(ValueError): api.validate_members(rows+[rows[0]])
    def test_quick_no_valuation_or_moat_and_sector_exception(self):
        member=dict(industry='Systems Software',sector='Information Technology')
        self.assertFalse(api.quick(member,dict(ROE='20%',ROIC='20%',**{'EPS Next 5Y':'10%','P/E':'900'}),'moderate')['rejected'])
        self.assertTrue(api.quick(member,dict(ROE='11%'),'moderate')['rejected'])
        self.assertFalse(api.quick(dict(industry='Diversified Banks',sector='Financials'),dict(ROE='20%',ROIC='1%'),'moderate')['rejected'])
        self.assertFalse(api.quick(member,{},'moderate')['rejected'])
    def test_asset_managers_are_not_custody_banks(self):
        member=dict(industry='Asset Management & Custody Banks',sector='Financials',cik=2012383)
        self.assertFalse(api.sector(member)['bank'])
        self.assertTrue(api.quick(member,{'ROE':'20%','ROIC':'9%'},'moderate')['rejected'])
        for cik in (1390777,73124,93751):
            self.assertTrue(api.sector(dict(member,cik=cik))['bank'])
        self.assertFalse(api.sector(dict(member,industry='Investment Banking & Brokerage'))['bank'])

    def test_missing_bulk_row_retrieves_individual(self):
        members=[dict(symbol='A',industry='Software',sector='IT'),dict(symbol='B',industry='Software',sector='IT')]
        with patch.object(api,'members',return_value={'rows':members}),patch.object(api,'bulk',return_value={'A':dict(symbol='A',fields={},source='bulk')}),patch.object(api,'snapshot',return_value=dict(symbol='B',fields={},source='individual')) as snap:
            result=api.batch('financial','A,B');self.assertEqual(result['missing'],[]);snap.assert_called_once_with('B')
            self.assertEqual(len(result['rows']),2)
        with self.assertRaises(ValueError): api.batch('financial',','.join(['A']*21))
    def test_rejected_business_skips_ib_peers_and_valuation(self):
        member=dict(symbol='TEST',cik=1,industry='Software',sector='IT')
        data=dict(series={},current={},market={},sources=[])
        rejected=dict(status='REJECTED',failures=['Sourced failure'],pending=[])
        with patch.object(api,'members',return_value={'rows':[member]}),patch.object(m,'evidence',return_value=data),patch.object(m,'evaluate',return_value=rejected),patch('vmi_ib.available') as ib,patch.object(api,'peer_check') as peers,patch.object(api,'research_payload') as research:
            result=api.company('TEST')
        ib.assert_not_called();peers.assert_not_called();research.assert_not_called()
        self.assertIsNone(result['research']);self.assertFalse(result['evidence']['ibkr']['available'])
        self.assertNotIn('ibkr',data)  # Cached source evidence remains immutable.

    def test_early_gate_requires_complete_latest_filing_history(self):
        year=dt.date.today().year-1
        rows=[dict(end=f'{y}-12-31',val=100-i*10) for i,y in enumerate(range(year-4,year+1))]
        issuer={'filings':{'recent':{'form':['10-K'],'filingDate':[f'{year+1}-02-01'],'reportDate':[rows[-1]['end']]}}}
        member=dict(industry='Software',sector='IT')
        self.assertEqual(m.reported_rejections(member,{'revenue':rows},issuer,'moderate'),['revenue'])
        self.assertEqual(m.reported_rejections(member,{'revenue':rows[:-1]},issuer,'moderate'),[])
        self.assertEqual(m.reported_rejections(member,{'revenue':rows},issuer,'conservative'),[])
        self.assertEqual(m.reported_rejections(member,{'revenue':rows},issuer,None),[])
        self.assertEqual(m.reported_rejections(dict(industry='Diversified Banks',sector='Financials'),{'ocf':rows},issuer,'moderate'),[])
        issuer['filings']['recent']['reportDate']=[f'{year+1}-01-31']
        self.assertEqual(m.reported_rejections(member,{'revenue':rows},issuer,'moderate'),[])

    def test_reported_failure_skips_optional_downloads_not_full_research(self):
        import json
        year=dt.date.today().year-1;rows=[dict(start=f'{y}-01-01',end=f'{y}-12-31',filed=f'{y+1}-02-01',form='10-K',val=100-i*10) for i,y in enumerate(range(year-4,year+1))]
        raw={'cik':1,'facts':{'us-gaap':{'Revenues':{'units':{'USD':rows}}}}}
        issuer={'tickers':['TEST'],'filings':{'recent':{'form':['10-K'],'filingDate':[rows[-1]['filed']],'reportDate':[rows[-1]['end']]}}}
        member=dict(symbol='TEST',cik=1,sector='IT',industry='Software')
        with patch.object(m.v,'fetch',side_effect=lambda url:json.dumps(raw if 'companyfacts' in url else issuer)),patch.object(m,'yahoo',return_value={}) as yahoo,patch.object(api,'market_fields',return_value=dict(fields={},sources=[],attempts=[])) as market,patch('vmi_filings.recover',return_value=dict(attempts=[],source=None)) as filing:
            result=m.evidence(member,mode='moderate')
            yahoo.assert_not_called();market.assert_not_called();filing.assert_not_called()
            self.assertEqual(result['acquisition']['failedKeys'],['revenue'])
            self.assertEqual(m.evaluate(member,result)['status'],'REJECTED')
            m.evidence(member)
            yahoo.assert_called_once();market.assert_called_once();filing.assert_called_once()

    def test_no_missing_balance_becomes_zero(self):
        result=m.calculate({'revenue':[dict(end='2025-12-31',val=100)],'income':[dict(end='2025-12-31',val=20)]})
        self.assertEqual(result['roe'],[]);self.assertEqual(result.get('ccc',[]),[])
    def test_incomplete_debt_cannot_produce_total_debt_ratios(self):
        values=dict(income=20,operatingIncome=25,tax=5,pretax=25,equity=100,debt=30,cash=10,assets=200,ebitda=40)
        s={k:[dict(end='2025-12-31',val=value)] for k,value in values.items()}
        s['debt'][0]['completeDebt']=False
        result=m.calculate(s)
        for key in ('roic','gearing','debtEbitda'): self.assertEqual(result.get(key,[]),[],key)
        s['debt'][0]['completeDebt']=True
        result=m.calculate(s)
        for key in ('roic','gearing','debtEbitda'): self.assertEqual(len(result[key]),1,key)

    def test_debt_recovery_upgrades_incomplete_not_reported_totals(self):
        s={'longDebt':[dict(end='2025-12-31',val=40)]}
        s['debt']=m.derive_debt(s);self.assertFalse(s['debt'][0]['completeDebt'])
        s['shortDebt']=[dict(end='2025-12-31',val=10)]
        upgraded=m.derive_debt(s);self.assertEqual(upgraded[0]['val'],50);self.assertTrue(upgraded[0]['completeDebt'])
        reported=dict(end='2025-12-31',val=60,source='reported total')
        s['debt']=[reported];self.assertEqual(m.derive_debt(s),[reported])

    def test_derive_before_filing_but_incomplete_debt_still_recovers(self):
        import json
        member=dict(symbol='TEST',cik=1,industry='Software',sector='IT');year=dt.date.today().year-1
        facts={}
        for tag,flow,val in [('InterestExpenseNonoperating',True,2),('PaymentsToAcquirePropertyPlantAndEquipment',True,5),('LongTermDebt',False,40),('ShortTermBorrowings',False,10)]:
            rows=[]
            for y in range(year-4,year+1):
                row=dict(end=f'{y}-12-31',filed=f'{y+1}-02-01',val=val,form='10-K')
                if flow: row['start']=f'{y}-01-01'
                rows.append(row)
            facts[tag]={'units':{'USD':rows}}
        def fetch(url): return json.dumps({'cik':1,'facts':{'us-gaap':facts}} if 'companyfacts' in url else {'tickers':['TEST']})
        with patch.object(m.v,'fetch',side_effect=fetch),patch.object(m,'yahoo',return_value={}),patch.object(api,'market_fields',return_value=dict(fields={},sources=[],attempts=[])),patch('vmi_filings.recover',return_value=dict(attempts=[],source=None)) as recover:
            m.evidence(member);recover.assert_not_called()
            del facts['ShortTermBorrowings'];m.evidence(member);recover.assert_called_once()

    def test_buyback_exception_requires_statement_support(self):
        year=dt.date.today().year-1;dates=[f'{year-4+i}-12-31' for i in range(5)]
        s={key:[dict(end=end,val=100+i*10) for i,end in enumerate(dates)] for key in ('income','fcf')}
        current={key:dict(end=dates[-1],val=value) for key,value in dict(equity=-20,treasury=150,retained=130,income=140).items()}
        self.assertTrue(m.buyback_exception(s,current)[0])
        del current['treasury'];self.assertIsNone(m.buyback_exception(s,current)[0])
        current['treasury']=dict(end=dates[-1],val=10);self.assertFalse(m.buyback_exception(s,current)[0])
        current['treasury']['val']=150;s['fcf'][-1]['val']=-1;self.assertFalse(m.buyback_exception(s,current)[0])
        self.assertFalse(api.quick(dict(industry='Restaurants',sector='Consumer Discretionary'),{'ROE':'-50','ROIC':'20'},'moderate')['rejected'])

    def test_margin_fallback_independent_of_earnings(self):
        year=dt.date.today().year-1;s={}
        for i in range(5):
            for k,value in dict(revenue=100+i*30,income=20+i,operatingIncome=(100+i*30)*.3).items():
                s.setdefault(k,[]).append(dict(end=f'{year-4+i}-12-31',val=value))
        result=m.evaluate(dict(symbol='TEST',industry='Software',sector='IT'),dict(series=s,current={},market={},sources=[]))
        earnings=next(c for c in result['checks'] if c['key']=='earnings');margin=next(c for c in result['checks'] if c['key']=='profitMargin')
        self.assertEqual(earnings['status'],'PASS');self.assertEqual(margin['status'],'PASS')
        self.assertIn('Operating-margin substitute',margin['detail'])

    def test_consecutive_years_and_declines(self):
        year=dt.date.today().year-1
        rows=[dict(end=f'{year-4+i}-12-31',val=v) for i,v in enumerate([100,110,108,130,150])]
        self.assertTrue(m.growth_test(rows,5))
        self.assertIsNone(m.growth_test(rows[:-1],5))
        self.assertFalse(m.growth_test([dict(r,val=10-i) for i,r in enumerate(rows)],5))
    def test_ttm_uses_ytd_difference(self):
        annual=[dict(start='2024-01-01',end='2024-12-31',val=100,source='SEC')]
        facts={'Revenue':{'units':{'USD':[
            dict(start='2025-01-01',end='2025-06-30',filed='2025-07-20',val=60,form='10-Q'),
            dict(start='2024-01-01',end='2024-06-30',filed='2025-07-20',val=45,form='10-Q'),
            dict(start='2025-04-01',end='2025-06-30',filed='2025-07-20',val=35,form='10-Q')]}}}
        self.assertEqual(m.ttm(facts,['Revenue'],annual)['val'],115)
    def test_annual_balance_filter_preserves_long_history(self):
        # Quarter instants inside a 10-K must not consume the twelve-row limit.
        rows=[]; ends=set()
        for year in range(2014,2026):
            ends.add(f'{year}-12-31')
            for suffix in ('03-31','06-30','09-30','12-31'):
                rows.append(dict(end=f'{year}-{suffix}',filed=f'{year+1}-02-01',form='10-K',val=year))
        rows.append(dict(end='2026-03-31',filed='2026-05-01',form='10-Q',val=2026))
        facts={'StockholdersEquity':{'units':{'USD':rows}}}
        result=m.annual_balances(facts,['StockholdersEquity'],ends)
        self.assertEqual([r['end'] for r in result],sorted(ends))
        self.assertEqual(m.annual_balances(facts,['StockholdersEquity'],set()),[])

    def test_fix_unbilled_subtotal_cannot_impersonate_trade_receivables(self):
        ends=[f'{y}-12-31' for y in range(2021,2026)]
        vals={'BilledContractReceivables':[773716000,1000000000,1300000000,1800000000,2577858000],
              'UnbilledContractsReceivable':[61881000,80000000,90000000,95786000,123197000]}
        facts={tag:{'units':{'USD':[dict(end=end,filed=f'{int(end[:4])+1}-02-20',form='10-K',val=val)
               for end,val in zip(ends,values)]}} for tag,values in vals.items()}
        member=dict(symbol='FIX',cik=1035983,industry='Building Products',sector='Industrials')
        scoped=m.issuer_trade_receivables(member,facts,set(ends),'SEC')
        self.assertEqual([scoped['annual'][i]['val'] for i in (0,4)],[835597000,2701055000])
        self.assertEqual(scoped['current']['val'],2701055000)
        self.assertEqual(scoped['annual'][0]['inputs'][0]['source'],'SEC')
        revenue=[dict(end=end,val=val) for end,val in zip(ends,[3073636000,4000000000,5000000000,7000000000,9101641000])]
        data=dict(series=dict(revenue=revenue,ar=scoped['annual']),current={},market={},sources=[])
        check=next(c for c in m.evaluate(member,data)['checks'] if c['key']=='receivables')
        self.assertEqual(check['status'],'FAIL')
        self.assertIn('receivables 223.25%',check['detail'])
        self.assertIn('revenue 196.12%',check['detail'])
        # Other issuers' net/gross definitions are not inferred from FIX.
        self.assertIsNone(m.issuer_trade_receivables(dict(member,cik=1),facts,set(ends),'SEC'))
        del facts['UnbilledContractsReceivable']
        self.assertEqual(m.issuer_trade_receivables(member,facts,set(ends),'SEC')['annual'],[])

class StatementFlowTests(unittest.TestCase):
    def setUp(self):
        self.annual = [dict(end='2025-12-31', val=100, source='html', unit='USD')]
        self.quarters = [dict(end=end, val=value, source='html', unit='USD', basis='Quarterly statement, not TTM or YTD') for end,value in
            [('2025-03-31',20),('2025-06-30',25),('2025-09-30',25),('2025-12-31',30),('2026-03-31',35),('2026-06-30',40)]]

    def test_reconciled_quarters_and_annual_fallback(self):
        result = m.statement_ttm(self.annual, self.quarters)
        self.assertEqual(result['val'], 130)
        self.assertEqual(result['end'], '2026-06-30')
        self.assertEqual(len(result['inputs']), 4)
        self.assertEqual(result['annualReconciliation']['annual'], self.annual[0])
        self.assertEqual(m.statement_ttm(self.annual, [])['val'], 100)

    def test_no_ytd_missing_or_mixed_source_sum(self):
        variants = [self.quarters[1:], self.quarters[:2]+self.quarters[3:],
                    [dict(r,basis='YTD') for r in self.quarters],
                    [dict(r,source='other') for r in self.quarters],
                    [dict(r,val=r['val']*2) for r in self.quarters]]
        for rows in variants:
            with self.subTest(rows=rows): self.assertIsNone(m.statement_ttm(self.annual, rows))

    def test_scraped_flows_feed_current_when_sec_unavailable(self):
        api._CACHE.clear()
        scraped=dict(series={'income':self.annual}, quarterly={'income':self.quarters}, source='html')
        with patch.object(m.v,'fetch',side_effect=ValueError('SEC unavailable')), patch('vmi_web.statements',return_value=scraped), patch.object(m,'yahoo',return_value={}), patch.object(api,'market_fields',return_value=dict(fields={},sources=[],attempts=[])):
            result=m.evidence(dict(symbol='TEST',cik=1,industry='Software',sector='IT'))
        self.assertEqual(result['current']['income']['val'],130)
        self.assertEqual(result['series']['quarterly_income'],self.quarters)
        self.assertEqual(result['series']['income'],self.annual)

class SupplementMergeTests(unittest.TestCase):
    def test_partial_failure_preserves_inputs_dates_and_conflicts(self):
        import copy
        row=dict(end='2025-12-31',val=100,source='old',unit='USD')
        old=dict(series={'income':[row]},current={'income':row},market={'EPS next 5Y':'12%'},marketFieldSources={'EPS next 5Y':{'source':'old'}},sources=['old'],attempts=[],retrievedAt=1000)
        snapshot=copy.deepcopy(old)
        new=dict(series={'income':[dict(row,val=110,source='new')],'quarterly_income':[dict(end='2026-06-30',val=40)]},current={},market={'EPS next 5Y':'-'},sources=['new'],attempts=[{'error':'partial'}],retrievedAt=2000)
        result=m.merge_evidence(old,new)
        self.assertEqual(result['series']['income'][0]['val'],110)
        self.assertEqual(result['current']['income']['val'],100)
        self.assertEqual(result['market']['EPS next 5Y'],'12%')
        self.assertEqual(result['retrievedAt'],1000)
        self.assertEqual(result['observationAlternatives'][0]['alternative']['val'],100)
        self.assertEqual(old,snapshot)
        self.assertEqual(len(result['series']['quarterly_income']),1)
    def test_complete_debt_never_regresses(self):
        row=dict(end='2025-12-31',val=100,completeDebt=True)
        old=dict(series={'debt':[row]},current={'debt':row},retrievedAt=1000)
        new=dict(series={'debt':[dict(row,val=80,completeDebt=False)]},current={},retrievedAt=2000)
        self.assertEqual(m.merge_evidence(old,new)['series']['debt'][0],row)

class AccountingMigrationTests(unittest.TestCase):
    def test_legacy_fix_subtotal_and_quarter_instants_stay_quarantined(self):
        import copy
        member=dict(symbol='FIX',cik=1035983)
        annual=dict(end='2025-12-31',val=100,source='filed')
        subtotal=dict(annual,val=123197000,filed='2026-03-01')
        quarter=dict(end='2025-09-30',val=90,source='filed')
        prior=dict(series=dict(revenue=[annual],equity=[annual,quarter],ar=[subtotal]),
                   current=dict(ar=subtotal),completedSources=['html','yahoo'],retrievedAt=1000)
        snapshot=copy.deepcopy(prior)
        clean=m.normalize_evidence(member,prior)
        self.assertEqual(clean['series']['equity'],[annual])
        self.assertEqual(clean['series']['ar'],[])
        self.assertNotIn('ar',clean['current'])
        self.assertEqual(len(clean['observationAlternatives']),3)
        self.assertEqual(clean['completedSources'],['html','yahoo'])
        self.assertEqual(clean['retrievedAt'],1000)
        self.assertEqual(prior,snapshot)
        # A failed retry cannot resurrect the invalid prior subtotal.
        result=m.merge_evidence(clean,dict(series={},current={},retrievedAt=2000))
        self.assertEqual(result['series']['ar'],[])
        self.assertEqual(result['retrievedAt'],1000)
        self.assertEqual(m.normalize_evidence(member,clean),clean)

class SourceReuseTests(unittest.TestCase):
    def test_completed_independent_sources_not_downloaded_after_restart(self):
        api._CACHE.clear()
        member=dict(symbol='TEST',cik=1,industry='Software',sector='IT')
        prior=dict(completedSources=['html','yahoo'])
        with patch.object(m.v,'fetch',side_effect=ValueError('SEC unavailable')),patch('vmi_web.statements') as html,patch.object(m,'yahoo') as yahoo,patch.object(api,'market_fields',return_value=dict(fields={},sources=[],attempts=[])):
            m.evidence(member,prior=prior)
        html.assert_not_called();yahoo.assert_not_called()
        with patch.object(m.v,'fetch',side_effect=ValueError('SEC unavailable')),patch('vmi_web.statements',side_effect=ValueError('retry')) as html,patch.object(m,'yahoo',return_value={}) as yahoo,patch.object(api,'market_fields',return_value=dict(fields={},sources=[],attempts=[])):
            m.evidence(member,prior={})
        html.assert_called_once();yahoo.assert_called_once()

class SupplementValidationTests(unittest.TestCase):
    def test_validated_merge_recomputes_presets_and_does_not_seed_shared_cache(self):
        import time,copy
        api._CACHE.clear();stamp=int(time.time()*1000)-1000
        member=dict(symbol='TEST',cik=1,industry='Software',sector='IT')
        row=dict(end='2025-12-31',val=100,source='prior')
        old=dict(symbol='TEST',cik=1,retrievedAt=stamp,series={'income':[row]},current={},market={},sources=[],attempts=[])
        prior=dict(cacheVersion=2,member=member,collectedAt=stamp,evidence=old)
        self.assertEqual(api.validate_prior(prior,member),old)
        for key,value in [('collectedAt',0),('cacheVersion',99),('member',dict(symbol='OTHER',cik=1))]:
            bad=copy.deepcopy(prior);bad[key]=value
            with self.assertRaises(ValueError):api.validate_prior(bad,member)
        bad=copy.deepcopy(prior);bad['evidence']['series']['income'][0]['val']=float('nan')
        with self.assertRaises(ValueError):api.validate_prior(bad,member)
        new=dict(old,series={},retrievedAt=stamp+500);seen=[]
        def evaluate(member,data,mode):
            seen.append((mode,data['series']['income'][0]['val']))
            return dict(preset=mode,status='REJECTED',failures=['fixture'],pending=[],checks=[])
        with patch.object(api,'members',return_value={'rows':[member]}),patch.object(m,'evidence',return_value=new),patch.object(m,'evaluate',side_effect=evaluate):
            result=api.company('TEST',supplement=True,prior=prior)
        self.assertEqual({mode for mode,val in seen},set(api.MODES))
        self.assertTrue(all(val==100 for mode,val in seen))
        self.assertEqual(result['collectedAt'],stamp)
        self.assertNotIn(('evidence','TEST'),api._CACHE)

class ConcurrencyTests(unittest.TestCase):
    def setUp(self): api._CACHE.clear()

    def test_single_flight_success_failure_and_retry(self):
        import threading,time
        from concurrent.futures import ThreadPoolExecutor
        for fail in (False,True):
            api._CACHE.clear();calls=[];barrier=threading.Barrier(8)
            def load():
                calls.append(1);time.sleep(.1)
                if fail: raise ValueError('fixture')
                return 42
            def worker(_):
                barrier.wait()
                try: return api.cached(('flight',),60,load)
                except ValueError: return 'failure'
            with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(worker,range(8)))
            self.assertEqual(len(calls),1);self.assertEqual(len(set(results)),1)
            self.assertNotIn(('flight',),api._INFLIGHT)
            if fail: self.assertEqual(api.cached(('flight',),60,lambda:3),3)
        self.assertEqual(api.cached(('outer',),60,lambda:api.cached(('inner',),60,lambda:5)),5)

    def test_excluded_industry_needs_no_provider(self):
        member=dict(symbol='OIL',industry='Oil & Gas',sector='Energy')
        with patch.object(api,'members',return_value={'rows':[member]}),patch.object(api,'bulk') as bulk,patch.object(api,'snapshot') as snap:
            result=api.batch('financial','OIL')
            bulk.assert_not_called();snap.assert_not_called()
            self.assertTrue(result['rows'][0]['screen']['rejected'])

    def test_parallel_fallback_bound_and_cached_rows(self):
        import threading,time
        active=peak=0;lock=threading.Lock()
        members=[dict(symbol='X'+str(i),industry='Software',sector='IT') for i in range(10)]
        def load(ticker):
            nonlocal active,peak
            with lock: active+=1;peak=max(peak,active)
            time.sleep(.02)
            with lock: active-=1
            return dict(symbol=ticker,fields={'ROE':'20'},source='fixture')
        with patch.object(api,'members',return_value={'rows':members}),patch.object(api,'bulk',return_value={}) as bulk,patch.object(api,'snapshot',side_effect=load) as snap:
            result=api.batch('financial',','.join(r['symbol'] for r in members))
            self.assertEqual(len(result['rows']),10);self.assertGreater(peak,1);self.assertLessEqual(peak,4)
            api.batch('financial','X1,X0');self.assertEqual(snap.call_count,10);self.assertEqual(bulk.call_count,1)

    def test_company_slots_bounded_across_callers(self):
        import threading,time
        from concurrent.futures import ThreadPoolExecutor
        active=peak=0;lock=threading.Lock()
        def load(ticker,mode,supplement=False):
            nonlocal active,peak
            with lock: active+=1;peak=max(peak,active)
            time.sleep(.02)
            with lock: active-=1
            return ticker
        with patch.object(api,'_company',side_effect=load),ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(list(pool.map(api.company,range(12))),list(range(12)))
        self.assertEqual(peak,4)

class RefreshTests(unittest.TestCase):
    def setUp(self): api._CACHE.clear()

    def test_forced_sources_reuse_explicitly_refreshed_universe(self):
        old={'rows':[],'source':'old'};new={'rows':[],'source':'new'}
        api.cached(('members',),100,lambda:old)
        with patch.object(api,'universe',return_value=new) as load:
            self.assertEqual(api.members(force=True),new)
            with api.fresh_sources(True):
                self.assertEqual(api.members(),new)
                self.assertEqual(api.members(),new)
            load.assert_called_once()

    def test_force_bypasses_cik_and_ticker_keys_without_mutating_other_workers(self):
        keys=[('issuer',123),('filing-evidence',123),('evidence','TEST')]
        for key in keys: api.cached(key,100,lambda:'old')
        with api.fresh_sources(True):
            for key in keys:
                self.assertEqual(api.cached(key,100,lambda:'fresh'),'fresh')
                self.assertEqual(api.cached(key,100,lambda:'unexpected'),'fresh')
        for key in keys: self.assertEqual(api.cached(key,100,lambda:'unexpected'),'old')

    def test_force_bypasses_old_inflight_download(self):
        from concurrent.futures import Future
        key=('issuer',123);pending=Future();api._INFLIGHT[key]=pending
        try:
            with api.fresh_sources(True): self.assertEqual(api.cached(key,100,lambda:'new'),'new')
            self.assertIs(api._INFLIGHT[key],pending)
        finally: api._INFLIGHT.pop(key,None)

    def test_bulk_keeps_all_preset_decisions_and_force_bypasses_rows(self):
        member=dict(symbol='TEST',industry='Software',sector='IT')
        row=dict(symbol='TEST',fields={'ROE':'13'},source='fixture')
        api.cached(('row','financial','TEST'),100,lambda:dict(row,fields={'ROE':'1'}))
        with patch.object(api,'members',return_value={'rows':[member]}),patch.object(api,'bulk',return_value={'TEST':row}) as load:
            out=api.batch('financial','TEST',defer=True,force=True)
        load.assert_called_once();r=out['rows'][0]
        self.assertTrue(r['screens']['conservative']['rejected'])
        self.assertFalse(r['screens']['moderate']['rejected'])

if __name__=='__main__': unittest.main()
