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
control={'puts_ready':False,'calls_ready':False,'expire':False,'polls':0,'chain_loads':0,'queries':[]}
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
        if kind=='bpsx_chain':
            out=chain(ticker,'expiry' in q)
            if 'expiry' in q:
                if ticker=='DIRECT':
                    control['chain_loads']+=1
                    control['queries'].append(q)
                out['calls']=[dict(row,conid=row['conid']+100,right='C',quoteState='QUEUED') for row in out['puts']]
                for row in out['calls']:row.pop('quote',None)
                out.update(streamId='fixture-stream-'+ticker,total=2,readyCount=2 if 'ids' in q else 0)
                if 'ids' not in q:
                    for row in out['puts']:row.pop('quote',None);row['quoteState']='QUEUED'
            return r.fulfill(json=out)
        if kind=='bpsx_quotes':
            ticker=q['stream'][0].replace('fixture-stream-','')
            if ticker=='DIRECT':
                control['polls']+=1
                if control['expire']:
                    control['expire']=False
                    return r.fulfill(json={'error':'IB Gateway: Chain stream expired; reload the chain'})
            out=dict(chain(ticker,True),streamId=q['stream'][0],total=2,readyCount=2,settledCount=2)
            out['calls']=[dict(row,conid=row['conid']+100,right='C') for row in out['puts']]
            if ticker=='DIRECT' and not control['puts_ready']:
                # A bid with no ask, even after a timeout, is NOT a completed put.
                out['puts'][1]['quote'].pop('ask')
                out['puts'][1]['quoteState']='NO_QUOTE'
            if ticker=='DIRECT' and not control['calls_ready']:
                for row in out['calls']:row.pop('quote',None);row['quoteState']='QUEUED'
            return r.fulfill(json=out)
        if kind=='bpsx_close':return r.fulfill(json={'closed':True})
        if kind=='bpsx_chart':return r.fulfill(json=dict(symbol=ticker,underConid=100,frame=q['frame'][0],bars=bars,served_by='tws',source='IB fixture '+q['frame'][0]))
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
    page.wait_for_function("document.querySelector('#bpsx-short')?.value==='2'")
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-long').input_value()=='1'
    assert page.locator('#bpsx-root svg').count()==1
    page.locator('#bpsx-root [data-mode="selected"]').click()
    page.locator('#bpsx-search input').fill('DIRECT')
    page.locator('#bpsx-search button').click()
    page.wait_for_selector('.bpsx-chain-progress')
    page.wait_for_timeout(1200)
    assert control['polls']>0
    assert page.locator('#bpsx-chain-live table').count()==0
    assert page.locator('#bpsx-short').is_disabled()
    assert page.locator('[data-action="send"]').is_disabled()
    control['puts_ready']=True
    page.wait_for_function("document.querySelector('#bpsx-short')?.value==='2'")
    assert page.locator('#bpsx-chain-live table.chain').count()==1
    assert 'CALLS' in page.locator('#bpsx-chain-live').inner_text()
    assert 'Loading…' in page.locator('#bpsx-chain-live tbody tr').first.locator('td').first.inner_text()
    assert 'Loading' not in page.locator('.bpsx-put-price').all_inner_texts()
    assert page.locator('tr.bpsx-short[data-chain-row="2"] .bpsx-short-badge').inner_text()=='SELL'
    assert page.locator('tr.bpsx-long[data-chain-row="1"] .bpsx-long-badge').inner_text()=='BUY'
    control['calls_ready']=True
    page.wait_for_function("!document.querySelector('#bpsx-chain-live').textContent.includes('Loading')")
    # Window controls use the Trade-style bounded backend request, not all 199 strikes.
    with page.expect_response(lambda r:'/data/bpsx_chain?' in r.url and 'count=13' in r.url):
        page.locator('#bpsx-chain-count').select_option('13')
    page.wait_for_function("document.querySelector('#bpsx-short')?.value==='2'")
    assert control['queries'][-1]['count']==['13']
    page.locator('#bpsx-chain-center').fill('85')
    with page.expect_response(lambda r:'/data/bpsx_chain?' in r.url and 'center=85' in r.url):
        page.locator('#bpsx-chain-center').press('Tab')
    page.wait_for_function("document.querySelector('#bpsx-short')?.value==='2'")
    assert control['queries'][-1]['center']==['85']
    # Idle expiry renews without erasing an edited ticket or exposing partial puts.
    page.locator('[data-ticket="credit"]').fill('0.56')
    loads=control['chain_loads'];control['expire']=True;control['puts_ready']=False
    page.wait_for_function("document.querySelector('.bpsx-chain-progress')!==null")
    assert control['chain_loads']>loads
    assert page.locator('#bpsx-chain-live table').count()==0
    assert page.locator('[data-ticket="credit"]').input_value()=='0.56'
    control['puts_ready']=True
    page.wait_for_selector('#bpsx-chain-live table')
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-long').input_value()=='1'
    assert page.locator('[data-ticket="credit"]').input_value()=='0.56'
    page.locator('[data-ticket="credit"]').fill('0.55')
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-root .bpsx-layout > div').bounding_box()['x']<page.locator('#bpsx-root .bpsx-layout > section').bounding_box()['x']
    assert 'Unverified' not in page.locator('#bpsx-chain-live').inner_text()
    assert page.locator('[data-chain-row]').count()==2
    assert page.locator('[data-study="bollinger"]').count()==3
    for frame in ('5m','15m','1h','w','1m','d'):
        page.locator('#bpsx-chart-frame').select_option(frame)
        if frame!='d':page.wait_for_function("document.querySelector('#bpsx-chart-status').textContent.includes('IB fixture '+"+json.dumps(frame)+")")
        assert page.locator('#bpsx-chart-frame').input_value()==frame
    page.locator('#bpsx-chart-bands').uncheck()
    assert page.locator('[data-study="bollinger"]').count()==0
    page.locator('#bpsx-chart-bands').check()
    page.locator('#bpsx-chart-band-settings').select_option('50:2')
    assert page.locator('[data-study="bollinger"]').count()==3
    before=page.locator('#bpsx-chart-panel svg').inner_html()
    page.locator('[data-action="chart-zoom-in"]').click()
    assert page.locator('#bpsx-chart-panel svg').inner_html()!=before
    page.locator('[data-action="chart-older"]').click()
    page.locator('[data-action="chart-latest"]').click()
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
print('BPSX browser passed: pending/partial puts hidden, calls can load later, Trade-style window controls, distinct selected-leg highlights, expired-session recovery preserves ticket, all chart controls, paper submission, duplicate guard, mobile and no JS errors')
