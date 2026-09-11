"""Offline regression tests. Run: python3 -m unittest -v

All brokerage/provider calls are mocked. No credentials or live services needed.
"""
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
    def fixture(self, day='2026-09-10'):
        opening=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(9,30),ZoneInfo('America/New_York'))
        detail=NS(contract=NS(conId=123,secType='STK',currency='USD'),stockType='COMMON',liquidSessions=lambda:[NS(start=opening,end=opening+dt.timedelta(hours=6.5))])
        minute=[NS(date=opening-dt.timedelta(minutes=1),volume=100),NS(date=opening,volume=999)]
        daily=[NS(date=opening.date()-dt.timedelta(days=1),close=9),NS(date=opening.date(),close=10)]
        return detail,minute,daily,opening

    def test_completed_premarket_and_prior_close_across_dst(self):
        for day,hour in [('2026-09-10',13),('2026-11-02',14)]:
            detail,minute,daily,opening=self.fixture(day)
            out=guns_data.verification(detail,minute,daily,int(opening.timestamp()*1000))
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
        engine=NS(_ib=ib)
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
        self.assertEqual((await guns_data.article(engine,'P1','story/1'))['text'],'Headline\nRevenue & earnings')
        ib.reqNewsArticleAsync.return_value=NS(articleType=1,articleText='binary')
        self.assertIn('Binary/PDF',(await guns_data.article(engine,'P1','1'))['warning'])
        ib.reqNewsArticleAsync.return_value=None
        with self.assertRaises(ValueError):await guns_data.article(engine,'P1','1')
        calls=ib.reqNewsArticleAsync.await_count
        for provider,article_id in [('bad/code','x'),('P1',''),('P1','x'*257),('P1','x\n')]:
            with self.assertRaises(ValueError):await guns_data.article(engine,provider,article_id)
        self.assertEqual(ib.reqNewsArticleAsync.await_count,calls)


if __name__ == '__main__':
    unittest.main()
