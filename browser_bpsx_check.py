"""Offline complete-app BPSX regression. No real orders or market requests."""
import datetime as dt
import json
from pathlib import Path
import re
import time
import urllib.parse
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
html=(ROOT/'paper_local.html').read_text()
state=json.loads(re.search(r'<script id="st" type="application/json">(.*?)</script>',html).group(1))
state['settings'].update(dataSource='free',provider='tws',auto=False,allowShort=True)
state['account'].update(mode='Custom',type='margin',currency='USD')
state.update(cash=100000,realized=0,positions=[],orders=[],trades=[])
now=dt.datetime.now(dt.timezone.utc)
expiry=(now+dt.timedelta(days=26)).strftime('%Y%m%d')
earnings=(now+dt.timedelta(days=70)).date().isoformat()
bars=[dict(date=(now-dt.timedelta(days=200-i)).date().isoformat(),o=60+i*.2,h=61+i*.2,l=59+i*.2,c=60.2+i*.2,v=2000000) for i in range(200)]
iv=[dict(date=(now-dt.timedelta(days=260-i)).date().isoformat(),value=.2+i*.001) for i in range(260)]
def chain(ticker,quoted):
    return dict(symbol=ticker,underConid=100,expirations=[expiry],expiry=expiry if quoted else None,served_by='tws',excluded=0,puts=[dict(conid=i,strike=k,expiry=expiry,secType='OPT',right='P',root=ticker,underConid=100,currency='USD',mult=100,symbol=f'{ticker} {expiry} {k} P',exch='SMART',tradingClass=ticker,deliverableStatus='IB_STANDARD_CLASS_NOT_OCC_VERIFIED',quote=dict(brokerConid=i,bid=bid,ask=ask,status='LIVE',at=int(time.time()*1000),bidSize=100,askSize=100,greeks={'delta':delta})) for i,k,bid,ask,delta in [(1,80,.2,.25,-.06),(2,85,.8,.85,-.1)]] if quoted else [])
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('dialog',lambda d:d.accept())
    def route(r):
        u=urllib.parse.urlsplit(r.request.url);q=urllib.parse.parse_qs(u.query);kind=u.path.rsplit('/',1)[-1]
        if u.path=='/':return r.fulfill(content_type='text/html',body=html)
        if u.path.startswith('/assets/') and (ROOT/kind).exists():return r.fulfill(content_type='text/css' if kind.endswith('.css') else 'text/javascript',body=(ROOT/kind).read_text())
        ticker=q.get('symbol',['TEST'])[0]
        if kind=='bpsx_scan':return r.fulfill(json=dict(rows=[dict(symbol='TEST',marketCapScreen=True),dict(symbol='NEXT',marketCapScreen=True)],source='Fixture IB screen',note='Offline test'))
        if kind=='bpsx_stock':return r.fulfill(json=dict(symbol=ticker,underConid=100,bars=bars,ivHistory=iv,industry='Software',category='Technology',served_by='tws'))
        if kind=='bpsx_chain':return r.fulfill(json=chain(ticker,'expiry' in q))
        if kind=='providers':return r.fulfill(json={'providers':{'tws':{'name':'IB Gateway','local':True,'quotes':True,'chain':True}}})
        if kind=='twsstatus':return r.fulfill(json=dict(connected=True,sealed=True,readonly=True,accounts=[]))
        if kind=='stream':return r.fulfill(content_type='text/event-stream',body=': fixture\n\n')
        if kind=='subscriptions':return r.fulfill(json=dict(connected=True,capacity=500,loaded=0,requested=0,active=0,seq=1,generation=0,quotes={}))
        if kind=='quote':return r.fulfill(json=dict(last=100,bid=99.9,ask=100.1,status='LIVE',at=int(time.time()*1000),served_by='tws'))
        if u.path.startswith('/api/'):return r.fulfill(json=dict(authenticated=False))
        return r.fulfill(json={})
    page.route('**/*',route)
    page.add_init_script('if(!localStorage.getItem("paperAccount"))localStorage.setItem("paperAccount",'+json.dumps(json.dumps(state))+');')
    page.goto('http://paper.test/',wait_until='domcontentloaded')
    page.wait_for_function('window.__paper')
    assert page.locator('[data-tab="bpsx"]').is_visible()
    page.locator('[data-tab="bpsx"]').click()
    page.locator('#bpsx-root [data-action="scan"]').click()
    page.wait_for_selector('#bpsx-disqualify')
    page.locator('#bpsx-disqualify').click()
    page.wait_for_function("document.querySelector('#bpsx-root').textContent.includes('NEXT · daily chart')")
    page.locator('#bpsx-root [data-action="qualify"]').click()
    page.wait_for_selector('#bpsx-short')
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-long').input_value()=='1'
    assert page.locator('#bpsx-root svg').count()==1
    page.locator('#bpsx-root [data-mode="selected"]').click()
    page.locator('#bpsx-search input').fill('DIRECT')
    page.locator('#bpsx-search button').click()
    page.wait_for_selector('#bpsx-short')
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-root .bpsx-layout > div').bounding_box()['x']<page.locator('#bpsx-root .bpsx-layout > section').bounding_box()['x']
    page.locator('#bpsx-send').click()
    page.wait_for_function("document.querySelector('#bpsx-message').textContent.includes('earnings')")
    assert page.evaluate('window.__paper.S.orders.length')==0
    for key in ('cap','support','sector','weekly','deliverable'):
        page.locator('[data-ticket="'+key+'"]').check()
    page.locator('[data-ticket="earnings"]').fill(earnings)
    page.locator('[data-ticket="qty"]').fill('1')
    page.locator('[data-ticket="qty"]').press('Tab')
    page.locator('#bpsx-send').click()
    page.wait_for_function("document.querySelector('#bpsx-message').textContent.includes('Paper spread submitted')")
    orders=page.evaluate('window.__paper.S.orders')
    assert len(orders)==1,orders
    assert orders[0]['strategy']=='BPSX' and orders[0]['limit']==-.55
    assert [(l['side'],l['strike']) for l in orders[0]['legs']]==[('SELL',85),('BUY',80)]
    page.locator('#bpsx-send').click()
    page.wait_for_function("document.querySelector('#bpsx-message').textContent.includes('Existing option exposure')")
    assert page.evaluate('window.__paper.S.orders.length')==1
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.querySelector("#bpsx-root").scrollWidth')<=page.evaluate('document.querySelector("#bpsx-root").clientWidth')+2
    assert not errors,errors
    browser.close()
print('BPSX browser passed: visible navigation, scan, reject-next, qualify, direct lookup, left-chain/right-chart, spread selection, incomplete-ticket block, paper submission, duplicate guard, mobile and no JS errors')
