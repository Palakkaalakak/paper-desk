"""Offline regression tests. Run: python3 -m unittest -v

All brokerage/provider calls are mocked. No credentials or live services needed.
"""
import asyncio
import contextlib
import http.client
import io
import json
import threading
import unittest
from unittest.mock import Mock, patch, AsyncMock
from types import SimpleNamespace as NS
import datetime as dt
from zoneinfo import ZoneInfo
import guns_data

import providers
import serve
import tws


class ServerSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = serve.Server(('127.0.0.1', 0), serve.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.access = patch.object(serve, 'ACCESS', 'test-access-token')
        self.access.start()
        self.addCleanup(self.access.stop)
        self.upstream = patch.object(serve.urllib.request, 'urlopen')
        self.urlopen = self.upstream.start()
        self.addCleanup(self.upstream.stop)
        response = self.urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"ok":true}'
        response.status = 200
        response.headers = {'Content-Type': 'application/json'}
        providers._CACHE.clear()
        self.addCleanup(providers._CACHE.clear)

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def authed(self, **extra):
        return {'Cookie': 'pd_token=test-access-token', **extra}

    def test_fmp_settings_requires_auth_and_local_form_headers(self):
        headers=self.authed(**{'Content-Type':'application/json','X-Paper-Desk-Settings':'1'})
        body=json.dumps({'key':'fixture_key_not_real'})
        with patch.object(serve,'save_fmp_key') as save:
            for hs in [{},self.authed(),{**headers,'Origin':'https://untrusted.example'}]:
                status,_,_=self.request('POST','/data/guns_credentials',hs,body)
                self.assertEqual(status,403)
            save.assert_not_called()
            status,_,out=self.request('POST','/data/guns_credentials',headers,body)
            self.assertEqual(status,200);save.assert_called_once_with('fixture_key_not_real')
            self.assertNotIn(b'fixture_key_not_real',out)
        with patch.object(serve,'save_fmp_key',side_effect=ValueError('do not expose details')):
            status,_,out=self.request('POST','/data/guns_credentials',headers,body)
            self.assertEqual(status,400);self.assertNotIn(b'do not expose details',out)
        # Simulate a network client even when authenticated: loopback only.
        handler=object.__new__(serve.Handler)
        handler.client_address=('192.0.2.10',1234);handler._reject=Mock()
        handler._configure_fmp();handler._reject.assert_called_once()
        self.assertEqual(handler._reject.call_args.args[0],403)

    def test_fmp_file_save_is_private_and_preserves_other_settings(self):
        import os, tempfile, pathlib
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ):
            path=pathlib.Path(directory)/'.env'
            path.write_text('PAPER_TWS_PORT=4002\nPAPER_FMP_KEY=old_fixture_key\n')
            serve.save_fmp_key('fixture_key_not_real',str(path))
            self.assertEqual(path.read_text(),'PAPER_TWS_PORT=4002\nPAPER_FMP_KEY=fixture_key_not_real\n')
            self.assertEqual(os.environ['PAPER_FMP_KEY'],'fixture_key_not_real')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with self.assertRaises(ValueError):serve.save_fmp_key('invalid\nPAPER_PORT=9999',str(path))
            self.assertNotIn('9999',path.read_text())

    def test_every_private_route_requires_authentication(self):
        for method, path in [
            ('GET', '/'), ('GET', '/data/providers'), ('GET', '/ws'),
            ('GET', '/api/iserver/accounts'),
            ('POST', '/api/iserver/auth/status'),
            ('DELETE', '/api/iserver/account/DU123/order/1'),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path)[0], 403)
        self.urlopen.assert_not_called()

    def test_guns_assets_and_data_are_authenticated_and_allowlisted(self):
        for name in ('guns.js','guns-execution.js','guns-workflow.js','guns-tutorial.js','guns-ui.js','guns.css'):
            path='/assets/'+name
            self.assertEqual(self.request('GET',path)[0],403)
            status,headers,body=self.request('GET',path,self.authed())
            self.assertEqual(status,200)
            self.assertTrue(body)
            self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(self.request('GET','/assets/GUNS_MASTER_DOCUMENT.md',self.authed())[0],404)
        with patch.object(serve.market.ENGINE,'call',return_value={'rows':[]}) as call:
            for kind in ('guns_scan','guns_bars','guns_news'):
                path='/data/'+kind+'?symbol=TEST'
                self.assertEqual(self.request('GET',path)[0],403)
                self.assertEqual(self.request('GET',path,self.authed())[0],200)
            self.assertEqual(call.call_count,3)
            call.assert_called_with('guns_news','TEST',timeout=40)
            for path,args,timeout in [('/data/guns_verify?conid=123',('guns_verify','123'),40),('/data/guns_article?newsProvider=TEST&articleId=story%2F1',('guns_article','TEST','story/1'),20)]:
                self.assertEqual(self.request('GET',path)[0],403)
                self.assertEqual(self.request('GET',path,self.authed())[0],200)
                call.assert_called_with(*args,timeout=timeout)

    def test_cookie_must_match_both_name_and_entire_value(self):
        for value in ['pd_token=test-access-token-extra',
                      'other_pd_token=test-access-token',
                      'other="pd_token=test-access-token"', 'pd_token=wrong']:
            with self.subTest(cookie=value):
                self.assertEqual(self.request('GET', '/', {'Cookie': value})[0], 403)
        self.assertEqual(self.request('GET', '/', self.authed())[0], 200)

    def test_login_redirect_hides_token_and_hardens_cookie(self):
        status, headers, body = self.request('GET', '/?t=test-access-token',
                                            {'X-Forwarded-Proto': 'https'})
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertEqual(body, b'')
        for attr in ['HttpOnly', 'Secure', 'SameSite=Lax', 'Path=/']:
            self.assertIn(attr, headers['Set-Cookie'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')

    def test_http_local_login_cookie_still_works(self):
        status, headers, _ = self.request('GET', '/?t=test-access-token')
        self.assertEqual(status, 303)
        self.assertNotIn('Secure', headers['Set-Cookie'])
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.assertEqual(self.request('GET', '/', {'Cookie': cookie})[0], 200)

    def test_public_install_assets_do_not_require_auth(self):
        for path in ['/manifest.json', '/icon.png', '/favicon.ico']:
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 200)

    def test_cross_origin_requests_never_reach_gateway(self):
        for method, path in [('POST', '/api/iserver/auth/status'), ('GET', '/ws')]:
            with self.subTest(method=method):
                self.assertEqual(self.request(method, path,
                    self.authed(Origin='https://unrelated.example'))[0], 403)
        self.urlopen.assert_not_called()

    def test_local_no_token_mode_still_blocks_foreign_origins(self):
        with patch.object(serve, 'ACCESS', ''):
            self.assertEqual(self.request('GET', '/')[0], 200)
            self.assertEqual(self.request('POST', '/api/iserver/auth/status',
                {'Origin': 'null'})[0], 403)
        self.urlopen.assert_not_called()

    def test_cross_site_fetch_metadata_is_rejected(self):
        self.assertEqual(self.request('GET', '/data/providers', self.authed(**{
            'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'cors'}))[0], 403)

    def test_authenticated_same_origin_preview_is_forwarded_unchanged(self):
        body = '{"orders":[{"conid":123,"quantity":1,"side":"BUY"}]}'
        headers = self.authed(Origin='http://127.0.0.1:%s' % self.port)
        status, response_headers, _ = self.request('POST',
            '/api/iserver/account/DU123/orders/whatif', headers, body)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers['Cache-Control'], 'no-store')
        req = self.urlopen.call_args.args[0]
        self.assertEqual(req.method, 'POST')
        self.assertEqual(req.data, body.encode())
        self.assertEqual(req.full_url, serve.GATEWAY + '/iserver/account/DU123/orders/whatif')

    def test_supported_session_posts_and_data_gets_are_allowed(self):
        for path in serve.GATEWAY_POST_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.request('POST', '/api' + path + '?force=true',
                                              self.authed(), '{}')[0], 200)
        for path in serve.GATEWAY_GET_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', '/api' + path, self.authed())[0], 200)

    def test_real_order_writes_and_encoded_bypasses_are_forbidden(self):
        for method, path in [
            ('POST', '/iserver/account/DU123/orders'),
            ('POST', '/iserver/account/DU123/order/1'),
            ('DELETE', '/iserver/account/DU123/order/1'),
            ('POST', '/iserver/reply/1'),
            ('POST', '/iserver/account/DU123/orders/whatif/../'),
            ('POST', '/iserver/account/DU123%2f..%2fDU999/orders/whatif'),
            ('POST', '/iserver/account/DU123/orders/whatif;anything'),
            ('GET', '/logout'),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, '/api' + path, self.authed())[0], 403)
        self.urlopen.assert_not_called()

    def test_invalid_or_oversized_bodies_are_rejected(self):
        for length, expected in [('-1', 400), ('nope', 400), ('9' * 50, 400),
                                 (str(serve.MAX_PROXY_BODY + 1), 413)]:
            with self.subTest(length=length):
                headers = self.authed(**{'Content-Length': length})
                self.assertEqual(self.request('POST', '/api/iserver/auth/status', headers)[0], expected)
        headers = self.authed(**{'Transfer-Encoding': 'chunked'})
        self.assertEqual(self.request('POST', '/api/iserver/auth/status', headers)[0], 400)
        self.urlopen.assert_not_called()

    def test_quotes_searches_and_chains_are_cached_per_credential(self):
        with patch.object(providers, 'cascade', side_effect=lambda kind, prov, key, **kw:
                          {'served_by': prov, 'credential_marker': key}) as cascade:
            for kind in ['quote', 'search', 'chain']:
                for key in ['first-key', 'second-key', 'first-key']:
                    url = '/data/%s?provider=alpaca&symbol=AAPL&q=apple&key=%s' % (kind, key)
                    status, headers, body = self.request('GET', url, self.authed())
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['Cache-Control'], 'no-store')
                    self.assertEqual(json.loads(body)['credential_marker'], key)
            self.assertEqual(cascade.call_count, 6)
        self.assertNotIn('first-key', repr(list(providers._CACHE)))
        self.assertNotIn('second-key', repr(list(providers._CACHE)))

    def test_logs_do_not_include_query_credentials(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.request('GET', '/?t=test-access-token')
            self.request('GET', '/data/providers?key=private-provider-key', self.authed())
        self.assertNotIn('test-access-token', output.getvalue())
        self.assertNotIn('private-provider-key', output.getvalue())
        self.assertIn('[redacted]', output.getvalue())


class AdapterTests(unittest.TestCase):
    def test_numeric_parser_rejects_non_json_numbers(self):
        for value in [float('nan'), float('inf'), float('-inf'), 'Infinity', '-Infinity', None, '--']:
            with self.subTest(value=value):
                self.assertIsNone(providers._f(value))
        self.assertEqual(providers._f('12.5'), 12.5)
        self.assertEqual(providers._f(0), 0)

    def test_tws_write_methods_are_sealed(self):
        class FakeIB:
            pass
        ib = FakeIB()
        originals = []
        for name in tws._WRITE_METHODS:
            fn = Mock()
            originals.append(fn)
            setattr(ib, name, fn)
        tws._seal(ib)
        self.assertTrue(tws.is_sealed(ib))
        for name in tws._WRITE_METHODS:
            with self.assertRaises(PermissionError):
                getattr(ib, name)()
        for fn in originals:
            fn.assert_not_called()


class GunsDataTests(unittest.IsolatedAsyncioTestCase):
    def test_prior_close_rejects_old_invalid_duplicate_or_current_daily_bars(self):
        expected='2026-09-09';now=1789047010000
        older=NS(date='2026-09-08',close=1);right=NS(date=expected,close=9)
        for rows in [[older],[older,NS(date=expected,close=float('nan'))],[right,right],[NS(date='2026-09-10',close=12)]]:
            out=guns_data.close_reference(rows,expected,now)
            self.assertFalse(out['previousCloseVerified']);self.assertIsNone(out['previousClose'])
        out=guns_data.close_reference([right,older],expected,now)
        self.assertEqual(out['previousClose'],9);self.assertEqual(out['previousCloseDate'],expected)
        self.assertEqual(out['previousCloseBasis'],'split-adjusted-not-dividend-adjusted')
        self.assertIsNone(guns_data.close_reference([right],None,now)['previousClose'])
        self.assertIsNone(guns_data.daily_date('2026-02-30'))
        self.assertIsNone(guns_data.daily_date('2026-09-09junk'))

    async def test_prior_calendar_shared_across_workers_and_respects_holiday_dst_dates(self):
        for current,prior in [('2026-09-08T13:30:00+00:00','20260904'),('2026-11-02T14:30:00+00:00','20261030'),('2026-04-06T13:30:00+00:00','20260402')]:
            ib=NS(reqHistoricalScheduleAsync=AsyncMock(return_value=NS(sessions=[NS(refDate=prior)])))
            engine=NS(_ib=ib,_stock=AsyncMock(return_value=NS(conId=99)),info={'generation':1})
            now=int(dt.datetime.fromisoformat(current).timestamp()*1000)
            dates=await asyncio.gather(*(guns_data.prior_session(engine,now) for _ in range(6)))
            self.assertEqual(dates,[guns_data.daily_date(prior)]*6)
            ib.reqHistoricalScheduleAsync.assert_awaited_once()
            self.assertTrue(ib.reqHistoricalScheduleAsync.call_args.kwargs['useRTH'])
            engine.info['generation']=2;await guns_data.prior_session(engine,now)
            self.assertEqual(ib.reqHistoricalScheduleAsync.await_count,2)

    def test_sip_daily_close_is_never_assumed_regular_session(self):
        detail,minute,daily,opening=self.fixture();detail.contract.symbol='TEST'
        now=int(opening.timestamp()*1000)
        rows=[{'t':(opening-dt.timedelta(minutes=1)).isoformat(),'c':10,'v':100}]
        reference=guns_data.close_reference(daily,str(daily[0].date),now)
        with patch.object(guns_data,'sip_bars',return_value=rows) as fetch:
            out=guns_data.sip_verification(detail,now,str(daily[0].date),reference)
            self.assertEqual(fetch.call_count,1);self.assertEqual(fetch.call_args.args[-1],'1Min')
            self.assertEqual(out['previousClose'],9);self.assertEqual(out['previousCloseSource'],'IB Gateway RTH TRADES')

    def test_us_stock_definition_rejects_foreign_currency_venue_and_smart_only(self):
        base=dict(secType='STK',currency='USD',primaryExchange='NASDAQ')
        self.assertTrue(guns_data.us_stock(NS(**base,issuerCountry='China')))
        for patch in [dict(currency='CAD'),dict(secType='OPT'),dict(primaryExchange='LSE'),dict(primaryExchange='SMART'),dict(primaryExchange='')]:
            self.assertFalse(guns_data.us_stock(NS(**{**base,**patch})))

    async def test_preliminary_scanner_retains_missing_metadata_for_verification(self):
        contracts=[NS(conId=i,symbol='TEST',secType='STK',currency='USD',primaryExchange=ex) for i,ex in enumerate(['NASDAQ','','LSE','SMART'],1)]
        rows=[NS(rank=i,contractDetails=NS(contract=c,longName='Test',stockType='COMMON')) for i,c in enumerate(contracts)]
        ib=NS(reqScannerDataAsync=AsyncMock(return_value=rows))
        out=await guns_data.scan(NS(_ib=ib))
        self.assertEqual([r['conid'] for r in out['rows']],[1,2]);self.assertFalse(out['rows'][1]['usListed'])
        self.assertEqual(ib.reqScannerDataAsync.call_args.args[0].locationCode,'STK.US.MAJOR')
        self.assertEqual(ib.reqScannerDataAsync.call_args.args[0].scanCode,'TOP_PERC_GAIN')
        tags=ib.reqScannerDataAsync.call_args.kwargs['scannerSubscriptionFilterOptions']
        self.assertEqual([(t.tag,t.value) for t in tags],[('changePercAbove','5')])
        self.assertEqual([r['rank'] for r in out['rows']],[0,1])
        self.assertTrue(all(r['scannerCode']=='TOP_PERC_GAIN' and r['scannerAt']==out['at'] for r in out['rows']))

    def test_footer_only_news_is_incomplete_not_a_story(self):
        footer='(END) Dow Jones Newswires\nSeptember 09, 2026 15:34 ET (19:34 GMT)\nCopyright (c) 2026 Dow Jones & Company, Inc.\nThe statements in this document shall not be considered as an objective or independent explanation.'
        out=guns_data.article_content(footer)
        self.assertEqual(out['contentStatus'],'incomplete')
        self.assertEqual(out['text'],'')
        self.assertIn('Copyright',out['rawText'])
        self.assertIn('no story text',out['warning'])
        body=' '.join(['Company reported higher revenue and earnings in its quarterly release.']*5)
        out=guns_data.article_content('<p>'+body+'</p>'+footer)
        self.assertEqual(out['contentStatus'],'body_returned')
        self.assertEqual(out['text'],body)
        self.assertIn('Copyright',out['legalText'])

    def fixture(self, day='2026-09-10'):
        opening=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(9,30),ZoneInfo('America/New_York'))
        detail=NS(contract=NS(conId=123,secType='STK',currency='USD',primaryExchange='NASDAQ'),stockType='COMMON',liquidSessions=lambda:[NS(start=opening,end=opening+dt.timedelta(hours=6.5))])
        minute=[NS(date=opening-dt.timedelta(minutes=1),volume=100),NS(date=opening,volume=999)]
        daily=[NS(date=opening.date()-dt.timedelta(days=1),close=9),NS(date=opening.date(),close=10)]
        return detail,minute,daily,opening

    def test_completed_premarket_and_prior_close_across_dst(self):
        for day,hour in [('2026-09-10',13),('2026-11-02',14)]:
            detail,minute,daily,opening=self.fixture(day)
            out=guns_data.verification(detail,minute,daily,int(opening.timestamp()*1000),str(daily[0].date))
            self.assertEqual(out['premarketVolume'],100)
            self.assertEqual(out['observedBars'],1)
            self.assertEqual(out['previousClose'],9)
            self.assertEqual(out['conid'],123)
            self.assertEqual(dt.datetime.fromtimestamp(out['premarketEnd']/1000,dt.timezone.utc).hour,hour)
            self.assertIn('not proof',out['coverage'])

    def test_unknown_volume_and_absent_sessions_remain_unknown(self):
        detail,minute,daily,opening=self.fixture()
        now=int(opening.timestamp()*1000)
        for value in [None,float('nan'),-1]:
            minute[0].volume=value
            self.assertIsNone(guns_data.verification(detail,minute,daily,now)['premarketVolume'])
        detail.liquidSessions=lambda:[]
        out=guns_data.verification(detail,minute,[],now)
        self.assertFalse(out['sessionKnown'])
        self.assertIsNone(out['premarketVolume'])
        self.assertIsNone(out['previousClose'])

    async def test_verification_identity_validation_and_read_only_snapshots(self):
        detail,minute,daily,opening=self.fixture()
        ib=NS(reqContractDetailsAsync=AsyncMock(return_value=[detail]),reqHistoricalDataAsync=AsyncMock(side_effect=[minute,daily]))
        ib.reqHistoricalScheduleAsync=AsyncMock(return_value=NS(sessions=[NS(refDate=str(daily[0].date))]))
        engine=NS(_ib=ib,_stock=AsyncMock(return_value=detail.contract))
        with patch.object(guns_data.time,'time',return_value=opening.timestamp()):
            out=await guns_data.verify(engine,'123')
        self.assertEqual(out['premarketVolume'],100)
        self.assertEqual(ib.reqHistoricalDataAsync.await_count,2)
        for call in ib.reqHistoricalDataAsync.await_args_list:self.assertFalse(call.kwargs['keepUpToDate'])
        detail.contract.conId=999;engine._guns_verify_next=0
        with self.assertRaises(ValueError):await guns_data.verify(engine,'123')
        for bad in ['-1','1.5','abc',str(2**53)]:
            with self.assertRaises(ValueError):await guns_data.verify(engine,bad)
        self.assertEqual(ib.reqContractDetailsAsync.await_count,2)

    async def test_news_providers_entitlement_and_safe_article_text(self):
        ib=NS(reqNewsProvidersAsync=AsyncMock(return_value=[]),reqHistoricalNewsAsync=AsyncMock(return_value=[]),reqNewsArticleAsync=AsyncMock())
        engine=NS(_ib=ib,_stock=AsyncMock(return_value=NS(conId=123)))
        self.assertIn('entitlements',(await guns_data.news(engine,'TEST'))['warning'])
        ib.reqHistoricalNewsAsync.assert_not_awaited()
        ib.reqNewsProvidersAsync.return_value=[NS(code='P1',name='One'),NS(code='P2',name='Two')]
        out=await guns_data.news(engine,'TEST')
        self.assertEqual(len(out['providers']),2)
        self.assertIn('does not establish',out['warning'])
        self.assertEqual(ib.reqHistoricalNewsAsync.await_args.args[1],'P1+P2')
        ib.reqHistoricalNewsAsync.return_value=None
        with self.assertRaises(ValueError):await guns_data.news(engine,'TEST')
        ib.reqNewsArticleAsync.return_value=NS(articleType=0,articleText='<h1>Headline</h1><script>evil()</script><style>hidden</style><p>Revenue &amp; earnings</p>')
        self.assertEqual((await guns_data.article(engine,'P1','story/1'))['text'],'Headline\n\nRevenue & earnings')
        ib.reqNewsArticleAsync.return_value=NS(articleType=1,articleText='binary')
        self.assertEqual((await guns_data.article(engine,'P1','1'))['contentStatus'],'incomplete')
        ib.reqNewsArticleAsync.return_value=None
        with self.assertRaises(ValueError):await guns_data.article(engine,'P1','1')
        calls=ib.reqNewsArticleAsync.await_count
        for provider,article_id in [('bad/code','x'),('P1',''),('P1','x'*257),('P1','x\n')]:
            with self.assertRaises(ValueError):await guns_data.article(engine,provider,article_id)
        self.assertEqual(ib.reqNewsArticleAsync.await_count,calls)


class GunsSourceAndStreamTests(unittest.IsolatedAsyncioTestCase):
    def test_public_feed_is_excerpt_and_unsafe_links_are_rejected(self):
        feed=b'<rss><channel><item><title>Company news</title><link>https://example.com/release</link><description>&lt;p&gt;Actual excerpt&lt;/p&gt;</description></item><item><title>bad</title><link>javascript:alert(1)</link></item></channel></rss>'
        with patch.dict(guns_data.os.environ,{},clear=True),patch.object(guns_data,'source_fetch',return_value=feed):out=guns_data.source_news('TEST')
        self.assertEqual(len(out['rows']),1);self.assertEqual(out['rows'][0]['contentStatus'],'excerpt');self.assertEqual(out['rows'][0]['text'],'Actual excerpt')
        self.assertEqual(guns_data.public_link('https://user:password@example.com'),'')
        with self.assertRaises(ValueError):guns_data.source_fetch('127.0.0.1','/',{})

    def test_full_body_identity_and_float_not_outstanding(self):
        body='Company reported higher revenue and strong earnings. '*10
        data=[dict(id=1,title='Report',body=body,stocks=[dict(name='TEST')]),dict(id=2,title='Other',body=body,stocks=[dict(name='OTHER')])]
        with patch.dict(guns_data.os.environ,{'PAPER_BENZINGA_KEY':'fixture-secret'},clear=True),patch.object(guns_data,'source_fetch',side_effect=[json.dumps(data).encode(),b'<rss/>']):out=guns_data.source_news('TEST')
        self.assertEqual(len(out['rows']),1);self.assertEqual(out['rows'][0]['contentStatus'],'body_returned');self.assertNotIn('fixture-secret',repr(out))
        with patch.dict(guns_data.os.environ,{},clear=True),patch.object(guns_data,'source_fetch',return_value=b'{}'):self.assertIsNone(guns_data.float_reference('TEST')['floatShares'])
        for field,expected in [('outstandingShares',None),('floatShares',15000000)]:
            with patch.dict(guns_data.os.environ,{'PAPER_FMP_KEY':'fixture-secret'},clear=True),patch.object(guns_data,'source_fetch',return_value=json.dumps([dict(symbol='TEST',date='2026-09-10',**{field:15000000})]).encode()):self.assertEqual(guns_data.float_reference('TEST')['floatShares'],expected)

    def test_provider_errors_redact_keys_and_redirects_are_refused(self):
        with patch.object(guns_data.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=ValueError('https://api.benzinga.com?token=fixture-secret')
            with self.assertRaises(ValueError) as error:guns_data.source_fetch('api.benzinga.com','/api/v2/news',{'token':'fixture-secret'})
            self.assertNotIn('fixture-secret',str(error.exception))
        with self.assertRaises(ValueError):guns_data.NoRedirect().redirect_request(None,None,302,'',{},'http://127.0.0.1')

    def engine(self):
        from ib_async.objects import BarData,BarDataList
        async def get(contract,*args,**kwargs):
            await asyncio.sleep(.002)
            rows=BarDataList();rows.append(BarData(date=dt.datetime(2026,9,10,13,29,tzinfo=dt.timezone.utc),open=10,high=10.1,low=9.9,close=10,volume=100));return rows
        ib=NS(reqHistoricalDataAsync=AsyncMock(side_effect=get),reqContractDetailsAsync=AsyncMock(side_effect=lambda c:[NS(contract=c,minTick=.01,stockType='COMMON',liquidSessions=lambda:[])]),cancelHistoricalData=Mock())
        return NS(_ib=ib,info={'generation':0},_stock=AsyncMock(side_effect=lambda s:NS(conId=sum(map(ord,s)),symbol=s)))

    async def test_concurrent_same_symbol_reuses_history_and_cap_is_six(self):
        e=self.engine();await asyncio.gather(*(guns_data.bars(e,'TEST') for _ in range(4)))
        self.assertEqual(e._ib.reqHistoricalDataAsync.await_count,2)
        await asyncio.gather(*(guns_data.bars(e,s) for s in ['AAA','BBB','CCC','DDD','EEE','FFF']))
        self.assertEqual(len(e._guns_streams),6);self.assertEqual(e._ib.cancelHistoricalData.call_count,2)

    async def test_cached_chart_bypasses_lock_and_old_ids_are_not_cancelled(self):
        e=self.engine();await guns_data.bars(e,'TEST')
        async with e._guns_bars_lock:await asyncio.wait_for(guns_data.bars(e,'TEST'),.1)
        e.info['generation']=1;await guns_data.bars(e,'TEST')
        e._ib.cancelHistoricalData.assert_not_called();self.assertEqual(e._ib.reqHistoricalDataAsync.await_count,4)

    async def test_cancelled_acquisition_cleans_streams(self):
        e=self.engine();started=asyncio.Event();release=asyncio.Event();original=e._ib.reqHistoricalDataAsync.side_effect
        async def delayed(*args,**kwargs):
            started.set();await release.wait();return await original(*args,**kwargs)
        e._ib.reqHistoricalDataAsync.side_effect=delayed
        task=asyncio.create_task(guns_data.bars(e,'TEST'));await started.wait();task.cancel();release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(e._guns_streams);self.assertEqual(e._ib.cancelHistoricalData.call_count,2)


class Guns15Tests(unittest.IsolatedAsyncioTestCase):
    def test_leading_copyright_and_news_flashes(self):
        body='Company reported record quarterly revenue and raised guidance. '*6
        out=guns_data.article_content('<p>Copyright (c) 2026 Publisher</p><p>'+body+'</p>')
        self.assertEqual(out['contentStatus'],'body_returned')
        self.assertIn('raised guidance',out['text'])
        self.assertEqual(guns_data.article_content('Company raises annual earnings outlook')['contentStatus'],'brief')

    def test_broker_float_units_date_and_conservative_bound(self):
        now=int(dt.datetime(2026,9,10,tzinfo=dt.timezone.utc).timestamp()*1000)
        out=guns_data.broker_float('<Report><FloatShares Date="2026-09-09" Unit="millions">15</FloatShares></Report>','TEST',123,now)
        self.assertEqual(out['floatShares'],15000000)
        out=guns_data.broker_float('<Report><SharesOut Date="2026-09-09">20000000</SharesOut></Report>','TEST',123,now)
        self.assertIsNone(out['floatShares']);self.assertEqual(out['upperBoundShares'],20000000)
        for source in ['<Report><FloatShares>15</FloatShares></Report>','<Report><FloatShares Date="2020-01-01">15</FloatShares></Report>','<Report><FloatShares Date="2026-09-09" Unit="percent">15</FloatShares></Report>']:
            self.assertIsNone(guns_data.broker_float(source,'TEST',123,now))

    async def test_valid_ib_pdf_is_returned_for_reader(self):
        import base64
        encoded=base64.b64encode(b'%PDF-1.4\nfixture').decode()
        engine=NS(_ib=NS(reqNewsArticleAsync=AsyncMock(return_value=NS(articleType=1,articleText=encoded))))
        out=await guns_data.article(engine,'BZ','BZ$1')
        self.assertEqual(out['contentStatus'],'pdf');self.assertEqual(out['pdfBase64'],encoded)


class PublicFloatTests(unittest.TestCase):
    def fixture(self, floated='1,484,043', outstanding='3,061,919', ticker='MEDS', age=0):
        import time
        now=int(time.time()*1000)
        page=('data:{info:{type:"stocks",subtype:"stock",symbol:"'+ticker.lower()+'",ticker:"'+ticker+'"}},'
              'data:{trust:{sources:[],lastUpdated:'+str(now-age)+',topic:"statistics",ticker:"'+ticker+'"},valuation:{},'
              'shares:{text:"Published counts",data:[{id:"float",title:"Float",value:"1.48M",hover:"'+floated+'"},'
              '{id:"sharesout",title:"Shares Outstanding",value:"3.06M",hover:"'+outstanding+'"}]}}')
        return page.encode(),now

    def test_public_default_never_sends_or_waits_for_fmp_key(self):
        payload,now=self.fixture()
        for env in [{},{'PAPER_FMP_KEY':'fixture-secret'}]:
            with patch.dict(guns_data.os.environ,env,clear=True),patch.object(guns_data,'source_fetch',return_value=payload) as fetch:
                out=guns_data.float_reference('MEDS')
            self.assertEqual(out['floatShares'],1484043);self.assertEqual(out['dateBasis'],'provider-statistics-update')
            self.assertIsNone(out['effectiveDate']);self.assertNotIn('fixture-secret',repr(out))
            self.assertEqual(fetch.call_count,1);self.assertEqual(fetch.call_args.args,('stockanalysis.com','/stocks/meds/statistics/',{}))

    def test_missing_float_is_labeled_outstanding_bound(self):
        payload,now=self.fixture('n/a','3,201,764','YFOR')
        with patch.object(guns_data,'source_fetch',return_value=payload):out=guns_data.public_float_reference('YFOR',now)
        self.assertIsNone(out['floatShares']);self.assertEqual(out['upperBoundShares'],3201764)
        self.assertEqual(out['basis'],'outstanding-upper-bound')

    def test_invalid_counts_identity_schema_and_dates_fail_closed(self):
        examples=[self.fixture(*args) for args in [('0','300'),('1.4M','300'),('42%','300'),('301','300'),('n/a','n/a'),('100','300','OTHER')]]
        examples += [self.fixture(age=46*86400000),self.fixture(age=-10000)]
        payload,now=self.fixture();examples += [(payload+payload,now),(payload.replace(b'lastUpdated:',b'quoteUpdated:'),now),(b'<html>Float 1.4M</html>',now)]
        for payload,now in examples:
            with patch.object(guns_data,'source_fetch',return_value=payload),self.assertRaises(guns_data.SourceError):guns_data.public_float_reference('MEDS',now)

    def test_optional_fmp_fallback_and_safe_failure_categories(self):
        good=json.dumps([dict(symbol='MEDS',floatShares=1234567,date=dt.datetime.now(dt.timezone.utc).isoformat())]).encode()
        with patch.dict(guns_data.os.environ,{'PAPER_FMP_KEY':'fixture-secret'},clear=True),patch.object(guns_data,'source_fetch',side_effect=[guns_data.SourceError('schema','changed public page'),good]):
            out=guns_data.float_reference('MEDS')
        self.assertEqual(out['floatShares'],1234567);self.assertEqual(out['diagnostics'][0]['source'],'Stock Analysis')
        with patch.dict(guns_data.os.environ,{'PAPER_FMP_KEY':'fixture-secret'},clear=True),patch.object(guns_data,'source_fetch',side_effect=[guns_data.SourceError('rate_limited','detail',429),guns_data.SourceError('subscription_coverage','fixture-secret',402)]):
            out=guns_data.float_reference('MEDS')
        self.assertEqual(out['status'],'unavailable');self.assertIn('HTTP 402',out['warning']);self.assertIn('HTTP 429',out['warning'])
        self.assertNotIn('fixture-secret',repr(out))
        with self.assertRaises(ValueError):guns_data.source_fetch('stockanalysis.com','/account/',{})
        with self.assertRaises(ValueError):guns_data.source_fetch('stockanalysis.com','/stocks/meds/statistics/',{'apikey':'secret'})


class ScannerProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_scan_never_waits_for_prior_close_or_calendar(self):
        detail,minute,daily,opening=GunsDataTests().fixture()
        ib=NS(reqContractDetailsAsync=AsyncMock(return_value=[detail]),reqHistoricalDataAsync=AsyncMock(return_value=minute))
        with patch.object(guns_data,'prior_session',side_effect=AssertionError('No local gap calendar')),patch.object(guns_data.time,'time',return_value=opening.timestamp()),patch.object(guns_data,'scanner_sources',return_value={'quoteFallbackConfigured':False}):
            out=await guns_data.verify(NS(_ib=ib),'123',False)
        self.assertEqual(out['premarketVolume'],100)
        self.assertIsNone(out['previousClose'])
        self.assertEqual(ib.reqHistoricalDataAsync.call_count,1)
        self.assertEqual(ib.reqHistoricalDataAsync.call_args.args[3],'1 min')

    async def test_three_history_jobs_overlap_without_serial_response_lock(self):
        active=peak=0
        async def history(*args,**kwargs):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            await asyncio.sleep(.9)
            active-=1
            return [object()]
        ib=NS(reqContractDetailsAsync=AsyncMock(side_effect=lambda c:[NS(contract=NS(conId=c.conId,secType='STK',currency='USD',primaryExchange='NASDAQ',symbol='TEST'))]),reqHistoricalDataAsync=history)
        engine=NS(_ib=ib)
        with patch.object(guns_data,'verification',return_value={'premarketVolume':30000,'previousClose':9}),patch.object(guns_data,'scanner_sources',return_value={'quoteFallbackConfigured':False}):
            result=await asyncio.gather(*(guns_data.verify(engine,str(i)) for i in [1,2,3]))
        self.assertEqual(len(result),3);self.assertEqual(peak,6)

    def test_sip_requests_are_explicit_and_credentials_stay_in_headers(self):
        with patch.dict(guns_data.os.environ,{'PAPER_ALPACA_KEY':'test-key','PAPER_ALPACA_SECRET':'test-secret'},clear=True),patch.object(guns_data,'source_fetch',return_value=b'{}') as fetch:
            guns_data.alpaca_data('/v2/stocks/snapshots',{'symbols':'TEST','feed':'iex'})
            self.assertEqual(fetch.call_args.args[2]['feed'],'sip')
            self.assertNotIn('test-secret',repr(fetch.call_args.args))
            self.assertEqual(fetch.call_args.kwargs['headers']['APCA-API-SECRET-KEY'],'test-secret')
            self.assertNotIn('test-secret',repr(guns_data.scanner_sources()))
        with self.assertRaises(ValueError):guns_data.source_fetch('data.alpaca.markets','/v2/orders',{})

    def test_sip_quotes_require_exact_symbol_two_sides_and_provider_timestamps(self):
        now=int(dt.datetime(2026,9,10,13,30,tzinfo=dt.timezone.utc).timestamp()*1000)
        quote=dict(bp=10,ap=10.02,bs=100,**{'as':100},t='2026-09-10T13:29:59Z')
        trade=dict(p=10.01,t='2026-09-10T13:29:59Z')
        with patch.object(guns_data,'alpaca_data',return_value={'TEST':dict(latestQuote=quote,latestTrade=trade)}):
            result=guns_data.sip_quote('TEST',now)
            self.assertEqual(result['bid'],10);self.assertEqual(result['source'],'Alpaca SIP consolidated NBBO')
        for changes in [dict(bp=0),dict(ap=None),dict(ap=9),dict(t='2026-09-10T13:10:00Z')]:
            with patch.object(guns_data,'alpaca_data',return_value={'TEST':dict(latestQuote={**quote,**changes},latestTrade=trade)}):
                with self.assertRaises((ValueError,TypeError)):guns_data.sip_quote('TEST',now)
        with patch.object(guns_data,'alpaca_data',return_value={'OTHER':dict(latestQuote=quote,latestTrade=trade)}):
            with self.assertRaises(ValueError):guns_data.sip_quote('TEST',now)

    def test_sip_history_consumes_all_pages_and_rejects_repeated_cursor(self):
        with patch.object(guns_data,'alpaca_data',side_effect=[{'bars':{'TEST':[{'v':1}]},'next_page_token':'next'},{'bars':{'TEST':[{'v':2}]}}]) as get:
            rows=guns_data.sip_bars('TEST','2026-09-09','2026-09-10','1Min')
            self.assertEqual(len(rows),2);self.assertEqual(get.call_args.args[1]['page_token'],'next')
        with patch.object(guns_data,'alpaca_data',return_value={'bars':{'TEST':[]},'next_page_token':'same'}):
            with self.assertRaises(ValueError):guns_data.sip_bars('TEST','2026-09-09','2026-09-10','1Min')

    async def test_float_never_calls_removed_ibkr_fundamentals(self):
        result=dict(symbol='TEST',status='returned',floatShares=1000000)
        engine=NS(_stock=AsyncMock(side_effect=AssertionError('IBKR must not be queried')))
        with patch.object(guns_data,'float_reference',return_value=result):self.assertEqual(await guns_data.float_data(engine,'TEST'),result)
        engine._stock.assert_not_called()

    async def test_ibkr_history_failure_uses_optional_sip_without_changing_contract(self):
        detail,minute,daily,opening=GunsDataTests().fixture()
        detail.contract.symbol='TEST'
        ib=NS(reqContractDetailsAsync=AsyncMock(return_value=[detail]),reqHistoricalDataAsync=AsyncMock(return_value=[]))
        expected=guns_data.verification(detail,minute,daily,int(opening.timestamp()*1000),str(daily[0].date))
        with patch.object(guns_data,'scanner_sources',return_value={'quoteFallbackConfigured':True}),patch.object(guns_data,'sip_verification',return_value=expected) as fallback:
            out=await guns_data.verify(NS(_ib=ib),'123')
            self.assertEqual(out['conid'],123);fallback.assert_called_once()

    def test_float_rejects_stale_dates_and_does_not_invent_them(self):
        with patch.dict(guns_data.os.environ,{'PAPER_FMP_KEY':'fixture-key'},clear=True),patch.object(guns_data,'source_fetch',return_value=json.dumps([dict(symbol='TEST',floatShares=1000000,date='2000-01-01')]).encode()):
            self.assertEqual(guns_data.float_reference('TEST')['status'],'unavailable')
        with patch.dict(guns_data.os.environ,{},clear=True):
            self.assertFalse(guns_data.scanner_sources()['floatConfigured'])


if __name__ == '__main__':
    unittest.main()
