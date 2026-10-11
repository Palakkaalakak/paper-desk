"""Offline full-app VMI workflow regression. Mocked IB data never enters a real account."""
import json
import pathlib
import re
import time
import urllib.parse
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent

def main():
    html = (ROOT / 'paper_local.html').read_text()
    state = json.loads(re.search(r'<script id="st" type="application/json">(.*?)</script>', html).group(1))
    state['settings'].update(dataSource='free', provider='tws', auto=False)
    state['account'].update(mode='Custom', type='cash', currency='USD')
    state.update(cash=100000, realized=0, positions=[], orders=[], trades=[])
    errors, requests = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440, 'height':960})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('dialog', lambda d: d.accept())
        def route(r):
            url = r.request.url
            if url == 'http://paper.test/': return r.fulfill(content_type='text/html', body=html)
            requests.append(url)
            now = int(time.time()*1000)
            if '/assets/' in url:
                name = url.rsplit('/',1)[-1]
                if name in ('guns.js','guns-execution.js','guns-workflow.js','guns-tutorial.js','guns-ui.js','guns.css','trading.js','trading-ui.js'):
                    return r.fulfill(content_type='text/css' if name.endswith('.css') else 'text/javascript',body=(ROOT/name).read_text())
            if '/data/vmi_sp500_status' in url:
                q=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                return r.fulfill(json=dict(version=1,at=now,cooldowns={'finvizSeconds':42,'yahooSeconds':0},requests=[dict(id=x,label='Detailed META',state='running',phase='download',provider='data.sec.gov',detail='Waiting for fixture response',elapsedSeconds=1,idleSeconds=1,counts={'download':1,'cache_hit':2},events=[]) for x in q.get('ids',[''])[0].split(',')]))
            if '/data/vmi_sp500_members' in url:
                return r.fulfill(json=dict(rows=[dict(symbol='T'+str(i),name='Fixture',industry='Software',sector='IT',cik=i+1) for i in range(502)]+[dict(symbol='META',name='Meta',industry='Software',sector='IT',cik=900)],source='fixture',retrievedAt=now))
            if '/data/vmi_sp500_batch' in url:
                q=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                return r.fulfill(json=dict(rows=[dict(symbol=x,fields={'ROE':'25%'},source='fixture',retrievedAt=now,screen=dict(rejected=x!='META',reasons=['Fixture threshold'])) for x in q['symbols'][0].split(',')],missing=[]))
            if '/data/vmi_sp500_company' in url:
                q=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                return r.fulfill(json=dict(member={'symbol':'META'},evidence={},result=dict(preset=q['mode'][0],status='QUALIFIED',checks=[],failures=[],pending=[]),research=dict(symbol='META',name='Meta',currency='USD',cik=900,metrics={'roe':25},sources=[],retrievedAt=now)))
            if '/data/vmi_fundamentals' in url: return r.fulfill(json={'symbol':'META','name':'<img src=x onerror=alert(1)>','currency':'USD','cik':123,'metrics':{'roe':25},'sources':[],'retrievedAt':now})
            if '/data/vmi_stock' in url: return r.fulfill(json={'symbol':'META','conid':123,'secType':'STK','currency':'USD','mult':1,'exch':'SMART','served_by':'tws'})
            option = dict(conid=456,symbol='META  990116C00100000',secType='OPT',right='C',root='META',underConid=123,currency='USD',mult=100,strike=100,expiry='20990116',exch='SMART',tradingClass='META',deliverableStatus='IB_STANDARD_CLASS_NOT_OCC_VERIFIED')
            if '/data/vmi_chain' in url:
                return r.fulfill(json=dict(symbol='META',underConid=123,expirations=['20990116'],calls=[option] if 'expiry=' in url else [],expiry='20990116' if 'expiry=' in url else None,excluded=0,served_by='tws',retrievedAt=now))
            if '/data/vmi_option' in url:
                return r.fulfill(json=dict(inst=option,quote=dict(brokerConid=456,bid=2,ask=2.1,last=None,status='LIVE',at=now,greeks={'delta':0.3}),served_by='tws',retrievedAt=now))
            if '/data/stream' in url: return r.fulfill(content_type='text/event-stream',body=': fixture\n\n')
            if '/data/subscriptions' in url:
                rows = r.request.post_data_json.get('instruments',[])
                quotes = {str(x['conid']):dict(bid=100,ask=101,last=100.5,bidSize=1000,askSize=1000,status='LIVE',at=now,source='IB Gateway') for x in rows}
                return r.fulfill(json=dict(quotes=quotes,connected=True,capacity=500,loaded=len(rows),requested=len(rows),active=len(rows),seq=1,generation=0))
            if '/data/twsstatus' in url: return r.fulfill(json=dict(connected=True,sealed=True,readonly=True,accounts=[]))
            if '/data/providers' in url: return r.fulfill(json={'providers':{'tws':{'name':'IB Gateway','local':True,'quotes':True,'chain':True}}})
            if '/data/quote' in url: return r.fulfill(json=dict(last=100.5,bid=100,ask=101,served_by='tws',status='LIVE',at=now))
            if '/api/' in url: return r.fulfill(json={'authenticated':False})
            return r.fulfill(status=404,body='Unexpected test request')
        page.route('**/*',route)
        page.add_init_script('if(!localStorage.getItem("paperAccount"))localStorage.setItem("paperAccount",'+json.dumps(json.dumps(state))+');')
        page.goto('http://paper.test/',wait_until='domcontentloaded')
        page.wait_for_function('window.__paper')
        page.locator('[data-tab="vmi"]').click()
        page.locator('#vmi-scan-start').wait_for()
        assert not any('/data/vmi_sp500_' in u for u in requests)
        page.evaluate('''() => {const original=window.fetch;window.fetch=(url,options)=>String(url).includes('/data/vmi_sp500_company?')?new Promise(resolve=>setTimeout(resolve,4500)).then(()=>original(url,options)):original(url,options);}''')
        page.locator('#vmi-scan-start').click()
        page.wait_for_function("document.querySelector('#vmi-scan-debug')?.textContent.includes('Waiting for fixture response')")
        before_heartbeat=page.locator('#vmi-scan-debug > p').first.inner_text()
        page.wait_for_timeout(1100)
        assert page.locator('#vmi-scan-debug > p').first.inner_text()!=before_heartbeat
        assert 'Cache hits 2' in page.locator('#vmi-scan-debug').inner_text()
        assert 'Finviz 42s' in page.locator('#vmi-scan-debug').inner_text()
        page.wait_for_function("document.querySelector('#vmi-scan-progress')?.textContent.includes('Scan complete')")
        assert page.locator('#vmi-scan-debug tbody tr').first.locator('td').nth(1).inner_text() == '503 / 503'
        assert page.locator('#vmi-scanner progress').get_attribute('max') == '1'
        assert page.locator('#vmi-scanner progress').get_attribute('value') == '1'
        assert any('/data/vmi_sp500_status?' in u for u in requests)
        assert page.locator('[data-scan-filter="all"], [data-scan-filter="rejected"], [data-scan-filter="acquiring"]').count() == 0
        assert page.locator('[data-scan-open]').count() == 1
        assert page.locator('[data-scan-open="T0"]').count() == 0
        assert page.locator('[data-scan-filter="qualified"] strong').inner_text() == '1'
        assert page.locator('#vmi-auto-settings').count() == 0
        page.locator('[data-scan-filter="qualified"]').click()
        page.locator('[data-scan-open="META"]').click()
        page.locator('#vmi-stock-ticket [name="qty"]').fill('3')
        page.locator('#vmi-stock-ticket [name="limit"]').fill('99')
        before = page.evaluate('JSON.stringify([__paper.S.cash,__paper.S.positions,__paper.S.orders,__paper.S.trades])')
        page.locator('#vmi-stock-ticket button').click()
        page.locator('#oQty').wait_for()
        assert page.locator('#oQty').input_value() == '3'
        assert page.locator('#oLimit').input_value() == '99'
        assert page.evaluate('JSON.stringify([__paper.S.cash,__paper.S.positions,__paper.S.orders,__paper.S.trades])') == before
        page.locator('[data-tab="vmi"]').click()
        page.locator('[data-page="missed"]').click()
        for name, value in dict(symbol='META', qty='100',price='90',fees='2',executedAt='2026-01-02T10:30:00-05:00',reason='Missed hypothetical paper fill').items():
            page.locator('#vmi-missed [name="'+name+'"]').fill(value)
        page.locator('#vmi-missed button').click()
        page.wait_for_timeout(300)
        assert page.locator('[data-action="record-missed"]').count(), page.locator('#vmi-message').inner_text()
        page.locator('[data-action="record-missed"]').wait_for()
        assert page.evaluate('__paper.S.trades.length') == 0
        page.locator('#vmi-missed [name="price"]').fill('91')
        assert page.locator('[data-action="record-missed"]').count() == 0
        page.locator('#vmi-missed button').click()
        page.locator('[data-action="record-missed"]').click()
        page.wait_for_function('__paper.S.trades.length===1')
        assert page.evaluate('__paper.S.cash') == 90898
        assert page.evaluate('__paper.S.positions[0].qty') == 100
        assert page.evaluate('JSON.parse(localStorage.paperAccount).paperCorrections.length') == 1
        assert page.evaluate('Object.keys(localStorage).filter(k=>k.startsWith("paperAccount.backup.missed-trade.")).length') == 1
        assert page.evaluate('__paper.S.trades[0].executedAt') == '2026-01-02T15:30:00.000Z'
        assert page.evaluate('__paper.S.trades[0].recordedAt!==__paper.S.trades[0].executedAt')
        page.locator('[data-page="calls"]').click()
        page.locator('#vmi-call-chain button').click()
        page.locator('#vmi-call-expiry').select_option('20990116')
        page.locator('#vmi-call-ticket').wait_for()
        assert page.locator('#vmi-call-ticket [name="limit"]').input_value() == '2'
        page.locator('#vmi-call-ticket [name="confirmed"]').check()
        account_before = page.evaluate('JSON.stringify([__paper.S.account,__paper.S.settings,__paper.S.cash,__paper.S.positions,__paper.S.orders,__paper.S.trades])')
        page.locator('#vmi-call-ticket button').click()
        page.locator('#oQty').wait_for()
        assert page.locator('#oQty').input_value() == '1'
        assert page.locator('#oLimit').input_value() == '2'
        assert page.evaluate('__paper.S.lastSelected.conid') == 456
        assert page.evaluate('JSON.stringify([__paper.S.account,__paper.S.settings,__paper.S.cash,__paper.S.positions,__paper.S.orders,__paper.S.trades])') == account_before
        assert sum('/data/vmi_option' in u for u in requests) >= 2
        page.locator('[data-tab="vmi"]').click()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('window.__paper && __paper.S.trades.length===1')
        assert page.evaluate('__paper.S.paperCorrections.length') == 1
        assert not any('/data/guns_bars' in u or '/data/vmi_scan?' in u for u in requests)
        assert not errors, errors
        print('VMI browser: stock/call handoffs without submission, chain routing, historical preview/recording, backup, persistence and mobile passed')
        browser.close()

if __name__ == '__main__': main()
