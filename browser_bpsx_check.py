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
    return dict(symbol=ticker,underConid=100,expirations=[expiry],expiry=expiry if quoted else None,served_by='tws',excluded=0,listedStrikes=[80,85,99],atmStrike=99,coverageDone=True,underlyingQuote=dict(brokerConid=100,last=99.8,status='LIVE',at=int(time.time()*1000)),puts=[dict(conid=i,strike=k,expiry=expiry,secType='OPT',right='P',root=ticker,underConid=100,currency='USD',mult=100,symbol=f'{ticker} {expiry} {k} P',exch='SMART',tradingClass=ticker,deliverableStatus='IB_STANDARD_CLASS_NOT_OCC_VERIFIED',quote=dict(brokerConid=i,bid=bid,ask=ask,status='LIVE',at=int(time.time()*1000),bidSize=100,askSize=100,greeks={'delta':delta if ticker!='NOPAIR' else -.5})) for i,k,bid,ask,delta in [(1,80,.2,.25,-.06),(2,85,.8,.85,-.1),(3,99,4.9,5.,-.5)]] if quoted else [])
control={'wide':False,'puts_ready':False,'calls_ready':False,'expire':False,'polls':0,'chain_loads':0,'queries':[]}
errors=[]
import re as _re
def _mk(page):
    def auto_qty(): return int(_re.search(r'(\d+) spreads?',page.locator('#bpsx-autofields').inner_text()).group(1))
    def auto_credit(): return float(_re.search(r'credit \$([0-9.]+)',page.locator('#bpsx-autofields').inner_text()).group(1))
    return auto_qty,auto_credit
with sync_playwright() as p:
    browser=p.chromium.launch(args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000})
    auto_qty,auto_credit=_mk(page)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('dialog',lambda d:d.accept())
    def route(r):
        u=urllib.parse.urlsplit(r.request.url);q=urllib.parse.parse_qs(u.query);kind=u.path.rsplit('/',1)[-1]
        if u.path=='/':return r.fulfill(content_type='text/html',body=html)
        if u.path.startswith('/assets/') and (ROOT/kind).exists():return r.fulfill(content_type='text/css' if kind.endswith('.css') else 'text/javascript',body=(ROOT/kind).read_text())
        ticker=q.get('symbol',['TEST'])[0]
        if kind=='guns_schedule':return r.fulfill(json=dict(sessions=[dict(start=int(time.time()*1000)-3600000,end=int(time.time()*1000)+3600000)],source='IB fixture session',at=int(time.time()*1000)))
        if kind=='bpsx_earnings':return r.fulfill(json=dict(symbol=ticker,status='found',date=earnings,timing='AMC',source='fixture'))
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
                out.update(streamId='fixture-stream-'+ticker,total=3,readyCount=3 if 'ids' in q else 0)
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
            out=dict(chain(ticker,True),streamId=q['stream'][0],total=3,readyCount=3,settledCount=3)
            out['calls']=[dict(row,conid=row['conid']+100,right='C') for row in out['puts']]
            if ticker=='DIRECT' and control['wide']:
                for row in out['puts'][:2]:row['quote'].update(bid=.1,ask=20)
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
    assert page.locator('#bpsx-next-bar button[data-action="next"]').count()==1  # Next stock is reachable at the top
    page.locator('#bpsx-disqualify').click()
    page.wait_for_function("document.querySelector('#bpsx-root').textContent.includes('NEXT · daily chart')")
    # Undo an accidental disqualify: the first stock comes back and the queue holds it again.
    page.locator('#bpsx-next-bar [data-restore]').click()
    page.wait_for_function("document.querySelector('#bpsx-root').textContent.includes('TEST · daily chart')")
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
    # Auto-selection must not be blocked just because a review input has focus.
    page.locator('#bpsx-review-note').fill('Keep this review note')
    control['puts_ready']=True
    page.wait_for_function("document.querySelector('#bpsx-short')?.value==='2'")
    assert page.locator('#bpsx-review-note').input_value()=='Keep this review note'
    assert page.locator('#bpsx-review-note').evaluate('(e)=>e===document.activeElement')
    assert page.locator('#bpsx-chain-live table.chain').count()==1
    assert 'Call bid / ask' in page.locator('#bpsx-chain-live').text_content()
    assert page.locator('#bpsx-chain-live thead th').all_text_contents()==['Call bid / ask','','Strike','Put bid / ask','+ BUY / − SELL']
    assert '$99.8' in page.locator('.bpsx-share-price').inner_text()
    assert 'LIVE' in page.locator('.bpsx-share-price').inner_text()
    assert 'ATM' in page.locator('tr.atm[data-chain-row="3"] .bpsx-atm-badge').inner_text()
    assert page.locator('[data-chain-row]').first.get_attribute('data-chain-row')=='3'
    assert 'SELL 85 PUT' in page.locator('.bpsx-selected-summary').inner_text()
    assert 'BUY 80 PUT' in page.locator('.bpsx-selected-summary').inner_text()
    assert page.locator('[data-bpsx-leg="short"][data-conid="2"]').get_attribute('aria-pressed')=='true'
    assert page.locator('[data-bpsx-leg="long"][data-conid="1"]').get_attribute('aria-pressed')=='true'
    page.locator('#bpsx-guide summary').click()
    assert '4×credit closing debit' in page.locator('#bpsx-guide').inner_text()
    page.locator('#bpsx-guide summary').click()
    assert '$99.8' in page.locator('.bpsx-market-row').inner_text()
    assert 'Loading…' in page.locator('#bpsx-chain-live tr[data-chain-row]').first.locator('td').first.inner_text()
    assert 'Loading' not in page.locator('.bpsx-put-price').all_inner_texts()
    assert page.locator('tr.bpsx-short[data-chain-row="2"] .bpsx-short-badge').inner_text()=='SELL'
    assert page.locator('tr.bpsx-long[data-chain-row="1"] .bpsx-long-badge').inner_text()=='BUY'
    control['calls_ready']=True
    page.wait_for_function("!document.querySelector('#bpsx-chain-live').textContent.includes('Loading')")
    # Sizing reacts to edits without replacing focused inputs or the chain DOM.
    # Everything but the allocation % is automatic: spreads, credit and earnings have no inputs.
    for sel in ('[data-ticket="qty"]','[data-ticket="credit"]','[data-ticket="earnings"]','[data-ticket="autoSize"]'):
        assert page.locator(sel).count()==0,sel
    assert auto_qty()==3
    assert abs(auto_credit()-.58)<1e-9,auto_credit()                       # mid credit 0.60 minus 40% of H=0.05
    assert 'AMC' in page.locator('#bpsx-autofields').inner_text() and 'unavailable' not in page.locator('#bpsx-autofields').inner_text()
    page.locator('#bpsx-allocation').fill('10')                            # 10% of the 100,000 account = 10,000
    assert auto_qty()==1
    page.locator('#bpsx-allocation').fill('20')
    assert auto_qty()==3
    # Account funds and existing pending BPSX exposure affect the real adapter.
    page.evaluate('window.__paper.S.cash=900')   # the allocation is a % of account value, so a tiny account sizes to nothing
    page.wait_for_function("document.querySelector('#bpsx-autofields b').textContent.startsWith('0 spread')")
    page.evaluate('window.__paper.S.cash=100000')
    page.wait_for_function("document.querySelector('#bpsx-autofields b').textContent.startsWith('3 spread')")
    page.evaluate("window.__paper.S.orders.push({id:'size-fixture',status:'working',qty:40,bpsx:{credit:.5,width:5},legs:[{conid:777},{conid:778}]})")
    page.wait_for_function("document.querySelector('#bpsx-autofields b').textContent.startsWith('0 spread')")
    page.evaluate("window.__paper.S.orders=window.__paper.S.orders.filter(o=>o.id!=='size-fixture')")
    page.wait_for_function("document.querySelector('#bpsx-autofields b').textContent.startsWith('3 spread')")
    # A valid initial selection can worsen: keep its identity, never imply suitability.
    page.evaluate("window.__qualityRow=document.querySelector('[data-chain-row=\"2\"]')")
    control['wide']=True
    page.wait_for_function("document.querySelector('.bpsx-selected-summary').textContent.includes('NOT currently eligible')")
    assert '0.10 / 20.00' in page.locator('tr[data-chain-row="2"] .bpsx-put-price').inner_text()
    assert 'LIVE' in page.locator('tr[data-chain-row="2"] .bpsx-put-price').inner_text()
    assert 'WIDE' in page.locator('tr[data-chain-row="2"] .bpsx-quote-quality').last.inner_text()
    assert 'width $19.90' in page.locator('tr[data-chain-row="2"] .bpsx-put-price').inner_text()
    assert 'Natural credit/share: -19.9' in page.locator('.bpsx-selected-summary').inner_text()
    assert auto_qty()==0
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-long').input_value()=='1'
    assert page.evaluate("window.__qualityRow===document.querySelector('[data-chain-row=\"2\"]')")
    assert 'spread exceeds' in page.locator('#bpsx-blockers').inner_text()
    control['wide']=False
    page.wait_for_function("!document.querySelector('.bpsx-selected-summary').textContent.includes('NOT currently eligible')")
    assert auto_qty()==3
    page.locator('#bpsx-guide summary').click()
    for text in ('not a hard cutoff','13.3%','Pepsi','BABA','19%','round DOWN','not promised income'):
        assert text in page.locator('#bpsx-guide').inner_text(),text
    page.locator('#bpsx-guide summary').click()
    # Window controls use the Trade-style bounded backend request, not all 199 strikes.
    with page.expect_response(lambda r:'/data/bpsx_chain?' in r.url and 'count=13' in r.url):
        page.locator('#bpsx-chain-count').select_option('13')
    page.wait_for_function("document.querySelector('#bpsx-short')?.value==='2'")
    assert control['queries'][-1]['count']==['13']
    with page.expect_response(lambda r:'/data/bpsx_chain?' in r.url and 'center=100' in r.url):
        page.locator('[data-action="chain-atm"]').click()
    page.wait_for_function("document.querySelector('#bpsx-short')?.value==='2'")
    assert control['queries'][-1]['center']==['100']
    # Direct Trade-style row controls replace either leg and keep the other leg.
    page.locator('[data-bpsx-leg="short"][data-conid="3"]').click()
    assert page.locator('#bpsx-short').input_value()=='3'
    assert page.locator('#bpsx-long').input_value()=='1'
    assert page.locator('tr.bpsx-short[data-chain-row="3"]').count()==1
    page.locator('[data-bpsx-leg="long"][data-conid="2"]').click()
    assert page.locator('#bpsx-long').input_value()=='2'
    page.wait_for_timeout(1100)
    assert page.locator('#bpsx-short').input_value()=='3'  # polling must not undo edits
    page.locator('[data-bpsx-leg="short"][data-conid="2"]').click()
    assert page.locator('#bpsx-long').input_value()==''  # same-contract conflict cleared
    page.locator('[data-bpsx-leg="long"][data-conid="1"]').click()
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-long').input_value()=='1'
    for theme in ('light','dark'):
        page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
        short_color=page.locator('tr.bpsx-short .k').evaluate('(e)=>getComputedStyle(e).backgroundColor')
        long_color=page.locator('tr.bpsx-long .k').evaluate('(e)=>getComputedStyle(e).backgroundColor')
        assert short_color=='rgb(237, 135, 80)',short_color
        assert long_color=='rgb(71, 184, 224)',long_color
    # Active auto/manual leg buttons toggle OFF, and polling must not undo it.
    page.locator('[data-bpsx-leg="short"][data-conid="2"]').click()
    assert page.locator('#bpsx-short').input_value()==''
    page.wait_for_timeout(1000)
    assert page.locator('#bpsx-short').input_value()==''
    page.locator('[data-bpsx-leg="short"][data-conid="2"]').click()
    page.locator('[data-bpsx-leg="long"][data-conid="1"]').click()
    assert page.locator('#bpsx-long').input_value()==''
    page.locator('[data-bpsx-leg="long"][data-conid="1"]').click()
    # Multiple incomplete polls cannot hide the chain or destroy last complete prices.
    page.evaluate("window.__bpsxRow=document.querySelector('[data-chain-row=\"2\"]');window.__bpsxButton=document.querySelector('[data-bpsx-leg=\"short\"][data-conid=\"2\"]')")
    control['puts_ready']=False
    page.wait_for_function("document.querySelector('#bpsx-chain-live').textContent.includes('last complete markets retained')")
    page.wait_for_timeout(1800)
    assert page.locator('#bpsx-chain-live table').count()==1
    assert page.locator('.bpsx-chain-progress').count()==0
    assert page.evaluate("window.__bpsxRow===document.querySelector('[data-chain-row=\"2\"]')")
    assert page.evaluate("window.__bpsxButton===document.querySelector('[data-bpsx-leg=\"short\"][data-conid=\"2\"]')")
    assert '0.80 / 0.85' in page.locator('tr[data-chain-row="2"] .bpsx-put-price').inner_text()
    assert page.locator('#bpsx-short').is_enabled()
    # Idle renewal also keeps the table, selected legs, scroll and edited credit.

    loads=control['chain_loads'];control['expire']=True;control['puts_ready']=False
    with page.expect_response(lambda r:'/data/bpsx_chain?' in r.url):
        page.wait_for_timeout(1500)
    assert control['chain_loads']>loads
    assert page.locator('#bpsx-chain-live table').count()==1
    assert page.locator('.bpsx-chain-progress').count()==0
    assert abs(auto_credit()-.58)<1e-9
    control['puts_ready']=True
    page.wait_for_function("!document.querySelector('#bpsx-send').disabled")
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-long').input_value()=='1'
    assert abs(auto_credit()-.58)<1e-9
    assert page.locator('#bpsx-short').input_value()=='2'
    assert page.locator('#bpsx-root .bpsx-layout > div').bounding_box()['x']<page.locator('#bpsx-root .bpsx-layout > section').bounding_box()['x']
    assert 'Unverified' not in page.locator('#bpsx-chain-live').inner_text()
    assert page.locator('[data-chain-row]').count()==3
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
    # Unmet requirements are shown in red but never block the order button.
    assert 'Confirm' in page.locator('#bpsx-blockers').inner_text()   # unconfirmed checklist items, shown in red
    assert page.locator('#bpsx-send').is_enabled()
    assert page.evaluate('window.__paper.S.orders.length')==0
    for key in ('cap','support','sector','weekly','deliverable'):
        page.locator('[data-ticket="'+key+'"]').check()
    page.locator('[data-ticket="bufferReason"]').fill('Fixture: stable support and low historical volatility')
    assert page.locator('[data-payoff="max-profit"]').inner_text()=='$174.00', (page.locator('.bpsx-payoff').inner_text(),auto_credit(),auto_qty(),errors)
    assert page.locator('[data-payoff="max-loss"]').inner_text()=='$1326.00',page.locator('[data-payoff="max-loss"]').inner_text()
    assert page.locator('[data-payoff="tp-profit"]').inner_text()=='$156.60'
    assert page.locator('[data-payoff="sl-loss"]').inner_text()=='$522.00'
    page.locator('#bpsx-send').click()
    page.wait_for_function("/Order (executed|working)/.test(document.querySelector('#bpsx-message').textContent)")
    orders=page.evaluate('window.__paper.S.orders')
    assert len(orders)==1,orders
    assert orders[0]['strategy']=='BPSX' and abs(orders[0]['limit']+.58)<1e-9
    assert [(l['side'],l['strike']) for l in orders[0]['legs']]==[('SELL',85),('BUY',80)]
    page.locator('#bpsx-send').click()
    page.wait_for_function("document.querySelector('#bpsx-message').textContent.includes('Existing option exposure')")
    assert page.evaluate('window.__paper.S.orders.length')==1
    assert orders[0]['bpsx']['exits']['enabled'] is True
    # Feed the real app, not a mock fill function: entry then one automatic TP combo.
    page.evaluate("""() => {const now=Date.now();window.__paper.applyFeed({connected:true,feedHealthy:true,generation:0,quotes:{
      1:{brokerConid:1,bid:.2,ask:.25,status:'LIVE',at:now,bidSize:100,askSize:100},
      2:{brokerConid:2,bid:.8,ask:.85,status:'LIVE',at:now,bidSize:100,askSize:100}}});}""")
    page.wait_for_function("window.__paper.S.orders.find(o=>o.bpsx)?.status==='filled'")
    page.wait_for_function("window.__paper.S.orders.find(o=>o.bpsx)?.bpsx.exits.status==='ARMED'")
    # Positions: the spread is ONE row; the legs are inside it until expanded.
    page.locator('[data-tab="pos"]').click()
    assert page.locator('#positionsTable tr.pos-group').count()==1
    assert 'put spread' in page.locator('#positionsTable tr.pos-group').inner_text()
    assert page.locator('#positionsTable tr.pos-child:visible').count()==0 and page.locator('#positionsTable tr.pos-child').count()==2
    page.locator('[data-toggle-group]').click()
    assert page.locator('#positionsTable tr.pos-child:visible').count()==2
    assert page.locator('#positionsTable tr[data-posid]:not(.pos-child)').count()==0   # no loose leg rows
    page.locator('[data-toggle-group]').click()
    page.locator('[data-tab="bpsx"]').click()
    cash_before=page.evaluate('window.__paper.S.cash')
    page.evaluate("""() => {const now=Date.now();window.__paper.applyFeed({connected:true,feedHealthy:true,generation:0,quotes:{
      1:{brokerConid:1,bid:.22,ask:.24,status:'LIVE',at:now,bidSize:100,askSize:100},
      2:{brokerConid:2,bid:.26,ask:.27,status:'LIVE',at:now,bidSize:100,askSize:100}}});}""")
    page.wait_for_function("window.__paper.S.orders.some(o=>o.bpsxExit?.reason==='TP'&&o.status==='filled')")
    exits=page.evaluate('window.__paper.S.orders.filter(o=>o.bpsxExit)')
    assert len(exits)==1
    # Marketable TP (natural 0.05 <= limit): mid 0.035 plus 40% of the 0.015 mid-to-natural distance.
    assert abs(exits[0]['avgFill']-(.035+.40*.015))<1e-6,exits[0]['avgFill']
    assert page.evaluate('window.__paper.S.positions.every(p=>p.qty===0)')
    assert page.evaluate('window.__paper.S.cash')<cash_before  # buyback debit + actual fees
    page.wait_for_timeout(1200)
    assert page.evaluate('window.__paper.S.orders.filter(o=>o.bpsxExit).length')==1
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.querySelector("#bpsx-root").scrollWidth')<=page.evaluate('document.querySelector("#bpsx-root").clientWidth')+2
    # No eligible observed deltas: say so, never fabricate an automatic pair.
    page.set_viewport_size({'width':1440,'height':1000})
    page.locator('#bpsx-search input').fill('NOPAIR')
    page.locator('#bpsx-search button').click()
    page.wait_for_selector('#bpsx-chain-live table')
    assert 'No valid automatic pair yet.' in page.locator('.bpsx-selected-summary').inner_text()
    assert page.locator('#bpsx-short').input_value()==''
    assert page.locator('tr.bpsx-selected').count()==0
    page.locator('[data-bpsx-leg="short"][data-conid="2"]').click()
    page.locator('[data-bpsx-leg="long"][data-conid="1"]').click()
    page.wait_for_timeout(1100)
    assert page.locator('tr.bpsx-short[data-chain-row="2"]').count()==1
    assert 'Observed short put delta' in page.locator('#bpsx-blockers').inner_text()
    assert len(page.evaluate('window.__paper.S.orders'))==2
    # Execute despite unmet requirements (delta, earnings...): red warning, but the order goes through.
    page.locator('#bpsx-send').click()
    page.wait_for_function("window.__paper.S.orders.length===3",timeout=15000)
    page.wait_for_function("/UNMET REQUIREMENTS|working/.test(document.querySelector('#bpsx-message').textContent)")
    assert not errors,errors
    browser.close()
print('BPSX browser passed: reactive automatic/manual sizing with funds and pending exposure caps, raw wide LIVE quote warnings, nuanced buffer guide, marked ATM-down ordering, auto/manual active leg buttons and deselection, construction guide, payoff dollars, stable quotes/renewal, real paper TP execution with one closing combo, duplicate safeguards, chart controls, light/dark themes and mobile')
