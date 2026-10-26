"""Offline complete-app Bull Call Spread regression (BPSX tab, Bull call spread mode). No real orders or market requests."""
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
expiry=(now+dt.timedelta(days=30)).strftime('%Y%m%d')
bars=[dict(date=(now-dt.timedelta(days=200-i)).date().isoformat(),o=60+i*.2,h=61+i*.2,l=59+i*.2,c=60.2+i*.2,v=2000000) for i in range(200)]
iv=[dict(date=(now-dt.timedelta(days=260-i)).date().isoformat(),value=.2+i*.001) for i in range(260)]
STRIKES=[(11,95,7.0,7.2,.70),(12,100,3.4,3.6,.55),(13,105,1.7,1.8,.38),(14,110,.75,.85,.24),(15,115,.3,.4,.12)]
def calls(bump=0):
    at=int(time.time()*1000)
    return [dict(conid=c,strike=k,right='C',expiry=expiry,root='TEST',tradingClass='TEST',symbol='TEST C%d'%k,secType='OPT',currency='USD',mult=100,underConid=100,quoteState='READY',bidAskResolved=True,
        quote=dict(bid=b+(bump if k<=100 else 0),ask=a+(bump if k<=100 else 0),status='LIVE',at=at,brokerConid=c,greeks=dict(delta=d,iv=.3,gamma=.02,theta=-.05,vega=.1))) for c,k,b,a,d in STRIKES]
def snapshot(bump=0):
    return dict(symbol='TEST',underConid=100,expirations=[expiry],expiry=expiry,served_by='tws',excluded=0,puts=[],calls=calls(bump),streamId='bcs-stream-1',streaming=True,callsComplete=True,
        underlyingQuote=dict(brokerConid=100,last=101,bid=100.98,ask=101.02,status='LIVE',at=int(time.time()*1000)))
control={'bump':0}
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
        if kind=='guns_schedule':return r.fulfill(json=dict(sessions=[dict(start=int(time.time()*1000)-3600000,end=int(time.time()*1000)+3600000)],source='IB fixture session',at=int(time.time()*1000)))
        if kind=='bpsx_earnings':return r.fulfill(json=dict(symbol=ticker,status='found',date=(now+dt.timedelta(days=70)).date().isoformat(),timing='AMC',source='fixture'))
        if kind=='bpsx_stock':return r.fulfill(json=dict(symbol=ticker,underConid=100,bars=bars,ivHistory=iv,industry='Software',category='Technology',served_by='tws'))
        if kind=='bcs_chain':
            if 'expiry' not in q:return r.fulfill(json=dict(symbol=ticker,underConid=100,expirations=[expiry],calls=[],puts=[]))
            return r.fulfill(json=snapshot(control['bump']))
        if kind=='bpsx_quotes':return r.fulfill(json=snapshot(control['bump']))
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
    page.locator('[data-tab="bcs"]').click()
    assert page.locator('#bcs-root [data-mode]').count()==0 and page.locator('#bpsx-root').count()==0
    page.locator('#bcs-search input').fill('TEST')
    page.locator('#bcs-search button').click()
    page.wait_for_selector('#bcs-panel table')
    # Auto-selection follows the course: long at/in the money near 0.55 delta, short about one standard deviation up.
    page.wait_for_function("document.querySelector('#bcs-long')?.value&&document.querySelector('#bcs-short')?.value")
    long_conid=page.locator('#bcs-long').input_value();short_conid=page.locator('#bcs-short').input_value()
    assert long_conid=='12',long_conid                    # strike 100 (0.55 delta), not the 0.70-delta 95
    assert short_conid=='14',short_conid                  # strike 110 = 101 + 1 sd (30% iv, 30 days)
    assert page.locator('#bcs-auto').count()==1
    txt=page.locator('#bcs-auto').inner_text();print(txt)
    assert re.search(r'(\d+) spreads?',txt) and int(re.search(r'(\d+) spreads?',txt).group(1))>=1
    assert page.locator('#bcs-send').inner_text()=='Execute order'
    page.locator('#bcs-send').click()
    page.wait_for_function("/Order (executed|working)/.test(document.querySelector('#bpsx-message').textContent)")
    # Feed the real app: the order fills as soon as both legs have fresh live quotes.
    page.evaluate("""()=>{const now=Date.now();window.__paper.applyFeed({connected:true,feedHealthy:true,generation:0,quotes:{
      12:{brokerConid:12,bid:3.4,ask:3.6,status:'LIVE',at:now,bidSize:100,askSize:100},
      14:{brokerConid:14,bid:.75,ask:.85,status:'LIVE',at:now,bidSize:100,askSize:100}}});}""")
    page.wait_for_function("window.__paper.S.orders.find(o=>o.strategy==='BCS')?.status==='filled'",timeout=20000)
    orders=page.evaluate('window.__paper.S.orders')
    assert len(orders)==1 and orders[0]['strategy']=='BCS',orders
    assert [(l['side'],l['strike']) for l in orders[0]['legs']]==[('BUY',100),('SELL',110)]
    debit=orders[0]['avgFill'];assert debit>0 and abs(debit-2.76)<0.011,debit        # mid 2.65 + 40% of 0.2 half-spreads... rounded up
    # One expandable position row.
    page.locator('[data-tab="pos"]').click()
    assert page.locator('#positionsTable tr.pos-group').count()==1 and 'call spread' in page.locator('#positionsTable tr.pos-group').inner_text()
    assert page.locator('#positionsTable tr.pos-child').count()==2
    # Profit target: the spread's mid value reaches 80% of max profit -> one automatic closing combo.
    tp=page.evaluate("window.__paper.S.orders[0].bcs.exits.tpValue")
    page.evaluate("""(q)=>{const now=Date.now();window.__paper.applyFeed({connected:true,feedHealthy:true,generation:0,quotes:{
      12:{brokerConid:12,bid:q+.5,ask:q+.6,status:'LIVE',at:now,bidSize:100,askSize:100},
      14:{brokerConid:14,bid:.45,ask:.55,status:'LIVE',at:now,bidSize:100,askSize:100}}});}""",tp)
    page.wait_for_function("window.__paper.S.orders.some(o=>o.bcsExit&&o.status==='filled')",timeout=20000)
    exits=page.evaluate('window.__paper.S.orders.filter(o=>o.bcsExit)')
    assert len(exits)==1 and exits[0]['bcsExit']['reason']=='TP' and [l['side'] for l in exits[0]['legs']]==['SELL','BUY']
    assert page.evaluate('window.__paper.S.positions.every(p=>p.qty===0)')
    page.wait_for_timeout(1500)
    assert page.evaluate('window.__paper.S.orders.filter(o=>o.bcsExit).length')==1
    assert not errors,errors
    browser.close()
print('BCS browser passed: auto strikes, auto sizing, instant debit fill, grouped position, one automatic 80% take-profit close')
