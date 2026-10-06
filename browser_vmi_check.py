"""Offline full-app VMI workflow regression. Mocked IB data never enters a real account."""
import json
import pathlib
import re
import time
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
            if '/data/vmi_scan' in url: return r.fulfill(json={'rows':[{'symbol':'META','fields':{'ROE':'25%'}}],'nextPage':1,'source':'fixture','retrievedAt':now})
            if '/data/vmi_fundamentals' in url: return r.fulfill(json={'symbol':'META','name':'<img src=x onerror=alert(1)>','currency':'USD','cik':123,'metrics':{'roe':25},'sources':[],'retrievedAt':now})
            if '/data/vmi_stock' in url: return r.fulfill(json={'symbol':'META','conid':123,'secType':'STK','currency':'USD','mult':1,'exch':'SMART','served_by':'tws'})
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
        page.locator('[data-discovered="META"]').click()
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
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('window.__paper && __paper.S.trades.length===1')
        assert page.evaluate('__paper.S.paperCorrections.length') == 1
        assert not any('/data/guns_bars' in u for u in requests)
        assert not errors, errors
        print('VMI browser: stock handoff, automatic discovery, historical preview/invalidation/recording, backup, persistence and mobile passed')
        browser.close()

if __name__ == '__main__': main()
