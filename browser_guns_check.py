"""Offline GUNS end-to-end regression; all prices/news are isolated test fixtures."""
import datetime as dt
import json
import pathlib
import re
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright

ROOT=pathlib.Path(__file__).resolve().parent
NOW=dt.datetime(2026,9,10,13,30,10,tzinfo=dt.timezone.utc)
OPEN=int(NOW.timestamp()*1000)-10000


def main():
    html=(ROOT/'paper_local.html').read_text().replace('/* ---------- debug surface ---------- */', '''
    window.__gunsTest={get desk(){return guns;},feed:feedApply,createBook:bookCreate,save:save};
    /* ---------- debug surface ---------- */''')
    state=json.loads(re.search(r'<script id="st" type="application/json">(.*?)</script>',html).group(1))
    state['settings'].update(dataSource='free',provider='tws',auto=False)
    history=dict(symbol='TEST',conid=12345,stockType='COMMON',usListed=True,currency='USD',primaryExchange='NASDAQ',minTick=.01,updatedAt=OPEN+10000,
                 sessions=[dict(start=OPEN,end=OPEN+23400000)],minute=[],daily=[])
    for i in range(1500):
        close=8.99+i*.001
        history['minute'].append(dict(t=OPEN-(1500-i)*60000,o=close-.001,h=close+.011,l=close-.01,c=close,v=1000))
    for i in range(240):
        day=dt.date(2026,9,9)-dt.timedelta(days=239-i)
        history['daily'].append(dict(t=day.isoformat(),o=8,h=9.1,l=7.9,c=9,v=100000))
    errors=[]
    requests=[]
    credential_requests=[]
    scanner_symbols=[('TEST',12345)]
    def close_evidence(now):
        return dict(previousClose=9,previousCloseDate='2026-09-09',expectedPreviousCloseDate='2026-09-09',previousCloseVerified=True,previousCloseBasis='split-adjusted-not-dividend-adjusted',previousCloseSource='Fixture IB RTH',previousCloseAt=now,sessionDate='2026-09-10')
    depth_fixture={'mode':'missing','revision':0}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.clock.install(time=NOW)
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            path=urlsplit(r.request.url).path
            params=parse_qs(urlsplit(r.request.url).query)
            requests.append(path)
            if path=='/':return r.fulfill(content_type='text/html',body=html)
            if path.startswith('/assets/'):
                name=path.rsplit('/',1)[-1]
                if name in ('guns.js','guns-execution.js','guns-workflow.js','guns-tutorial.js','guns-ui.js','guns.css'):
                    return r.fulfill(content_type='text/css' if name.endswith('.css') else 'text/javascript',body=(ROOT/name).read_text())
            if path=='/data/providers':return r.fulfill(json={'providers':{'tws':{'name':'IB Gateway','local':True,'quotes':True,'chain':True}}})
            if path=='/data/subscriptions':return r.fulfill(json={'connected':True,'feedHealthy':True,'generation':0,'quotes':{}})
            if path=='/data/stream':return r.fulfill(content_type='text/event-stream',body=': fixture\n\n')
            if path in ('/data/twsstatus','/api/iserver/auth/status'):return r.fulfill(json={'connected':True,'authenticated':True})
            if path=='/data/guns_credentials':
                assert r.request.method=='POST'
                assert r.request.headers.get('x-paper-desk-settings')=='1'
                credential_requests.append(r.request.post_data_json)
                return r.fulfill(json={'configured':True})
            if path=='/data/guns_data_status':return r.fulfill(json={'floatConfigured':True,'quoteFallbackConfigured':True})
            if path=='/data/guns_ib_quote':return r.fulfill(json=dict(brokerConid=int(params['conid'][0]),bid=10.48,ask=10.50,last=10.49,source='IB Gateway',status='LIVE',at=int(r.request.headers['x-fixture-now']),tradeAt=int(r.request.headers['x-fixture-now']),screeningOnly=True))
            if path=='/data/guns_scan':return r.fulfill(json={'rows':[dict(conid=cid,symbol=sym,name='Fixture stock',secType='STK',brokerId=True) for sym,cid in scanner_symbols]})
            if path=='/data/search':
                sym=(params.get('q') or ['TEST'])[0].upper()
                return r.fulfill(json={'results':[dict(conid=12345 if sym=='TEST' else 23456,symbol=sym,name='Fixture '+sym,type='STK')]})
            if path=='/data/guns_bars':
                sym=(params.get('symbol') or ['TEST'])[0]
                return r.fulfill(json={**history,**close_evidence(int(r.request.headers['x-fixture-now'])),'symbol':sym,'conid':dict(scanner_symbols).get(sym,23456),'updatedAt':int(r.request.headers['x-fixture-now'])})
            if path=='/data/guns_schedule':return r.fulfill(json={'sessions':[dict(start=OPEN,end=OPEN+23400000)]})
            if path=='/data/guns_float':return r.fulfill(json={'floatShares':15000000,'date':'2026-09-09','source':'Fixture float'})
            if path=='/data/guns_sources':return r.fulfill(json={'rows':[dict(headline='Fixture linked company release',provider='Fixture RSS',time='2026-09-10',articleId='',url='https://example.com/release',contentStatus='excerpt',text='Fixture source excerpt')],'diagnostics':[]})
            if path=='/data/guns_verify':return r.fulfill(json=dict(conid=int(params['conid'][0]),at=int(r.request.headers['x-fixture-now']),**close_evidence(int(r.request.headers['x-fixture-now'])),sessionKnown=True,stockType='COMMON',usListed=True,currency='USD',primaryExchange='NASDAQ',premarketVolume=330000,observedBars=330,source='Fixture IB TRADES',volumeUnit='shares',coverage='Observed bars only'))
            if path=='/data/guns_news':return r.fulfill(json={'source':'Fixture API news','at':OPEN+10000,'providers':[dict(code='TEST',name='Fixture provider')],'rows':[dict(time='2026-09-10',provider='TEST',articleId='story1',headline='Fixture earnings beat'),dict(time='2026-09-10',provider='TEST',articleId='footer',headline='Footer-only fixture')]})
            if path=='/data/guns_article' and params.get('articleId')==['footer']:return r.fulfill(json={'text':'','rawText':'(END) Copyright fixture','contentStatus':'incomplete'})
            if path=='/data/guns_article':return r.fulfill(json={'text':'Fixture earnings article <img src=x onerror=alert(1)>','provider':'TEST','articleId':'story1','contentStatus':'body_returned','rawText':'Fixture earnings article <img src=x onerror=alert(1)>'})
            if path=='/data/depth':
                if depth_fixture['mode']=='missing':return r.fulfill(json={'bids':[],'asks':[]})
                # Each fixture response explicitly represents a new broker update.
                depth_fixture['revision']+=1
                return r.fulfill(json=dict(symbol='TEST',conid=12345,source='IB Gateway SMART depth',status='LIVE',
                    updatedAt=int(r.request.headers['x-fixture-now']),revision=depth_fixture['revision'],stream='fixture:1',
                    bids=[dict(price=10.19-i*.01,size=100) for i in range(3)],
                    asks=[dict(price=10.21+i*.01,size=500 if depth_fixture['mode']=='flag' else 100) for i in range(3)]))
            if path.startswith('/api/'):return r.fulfill(json={})
            return r.fulfill(status=404,body='Unexpected fixture request')
        page.route('**/*',route)
        # Direct test feed supplies quotes; avoid EOF/reconnect races in this fixture.
        page.add_init_script("const realFetch=window.fetch;window.fetch=(url,options={})=>{const headers=new Headers(options.headers);headers.set('X-Fixture-Now',String(Date.now()));return realFetch(url,{...options,headers});};")
        page.add_init_script('window.EventSource=class {constructor(){this.readyState=1;} close(){} addEventListener(){}};')
        page.add_init_script('localStorage.setItem("paperAccount",'+json.dumps(json.dumps(state))+');')
        page.goto('http://paper.test/',wait_until='domcontentloaded')
        page.wait_for_function('window.__gunsTest && __gunsTest.desk')
        page.evaluate('''() => {
          window.tick=(bid=10.48,ask=10.5,last=10.49,size=100)=>__gunsTest.feed({connected:true,feedHealthy:true,generation:0,quotes:{12345:{bid,ask,last,bidSize:size,askSize:size,status:'LIVE',at:Date.now(),tradeAt:Date.now(),receivedAt:Date.now()}}});
          tick(0,10.5); // A zero bid must wait for real quotes, not become spread failure.
          const fetchBeforeScan=window.fetch;
          window.quoteRecoveryChecks=0;
          window.fetch=async (...args)=>{
            const response=await fetchBeforeScan(...args);
            if(String(args[0]).includes('/data/guns_verify')&&quoteRecoveryChecks===0){
              quoteRecoveryChecks++;setTimeout(()=>tick(),500);
            }else if(String(args[0]).includes('/data/guns_float')&&quoteRecoveryChecks===1){
              // Reproduce bid/ask disappearing while fundamentals were loading.
              quoteRecoveryChecks++;tick(10.48,null); // Only the owner-loop IB snapshot can recover this side.
            }
            return response;
          };
        }''')
        page.locator('[data-tab="guns"]').click()
        page.locator('[data-guns="scan"]').click()
        page.locator('[data-guns-pick="12345"]').click()
        page.wait_for_function("document.querySelector('#guns-clock').textContent.includes('s old')")
        page.evaluate('__gunsTest.desk.pulse()')
        page.locator('.guns-stage-nav [data-guns-stage="scanner"]').click()
        page.wait_for_function("document.querySelector('#guns-scanner').textContent.includes('IBKR screened')")
        page.locator('#guns-source-settings summary').click()
        page.locator('#guns-fmp-key').fill('fixture_key_not_real')
        page.locator('#guns-source-save').click()
        page.wait_for_function("document.querySelector('#guns-source-notice').textContent.includes('Saved on this Paper Desk server')")
        assert credential_requests==[{'key':'fixture_key_not_real'}]
        assert page.locator('#guns-fmp-key').input_value()==''
        assert 'fixture_key_not_real' not in page.evaluate('JSON.stringify(localStorage)')
        assert 'fixture_key_not_real' not in page.locator('body').inner_text()
        assert page.evaluate('quoteRecoveryChecks')==2
        card=page.locator('.guns-candidate').inner_text()
        assert 'Bid $10.4800' in card and 'Ask $10.5000' in card and 'Spread $0.0200' in card,card
        assert 'unknown' not in card.lower() and '—' not in card
        assert 'Awaiting' not in page.locator('#guns-scan-diagnostics').inner_text()
        assert requests.count('/data/guns_scan')==1, 'Quote recovery must not rediscover/re-rank'
        assert requests.count('/data/guns_ib_quote')>=1, 'Read IBKR directly when SSE quote is incomplete'
        assert requests.count('/data/guns_quote')==0, 'Never replace a working IBKR quote with external data'
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.evaluate('tick()')
        page.locator('.guns-stage-nav [data-guns-stage="news"]').click()
        page.locator('[data-guns-article="TEST:story1"]').click()
        page.wait_for_function("document.querySelector('#guns-article').textContent.includes('Fixture earnings article')")
        assert page.locator('#guns-article img').count()==0
        assert not page.locator('#guns-catalyst').is_checked()
        assert 'Fixture provider' in page.locator('#guns-news-meta').inner_text()
        # Unrelated stories must not replace the clicked article.
        page.locator('[data-guns-article="TEST:footer"]').click()
        page.wait_for_function("document.querySelector('#guns-article-warning').textContent.includes('source-only')")
        assert 'Fixture earnings article' not in page.locator('#guns-article').inner_text()
        assert '(END)' in page.locator('#guns-article-raw').text_content(),page.locator('#guns-article-raw').text_content()
        page.locator('#guns-symbol').fill('OTHER');page.locator('#guns-symbol').press('Enter')
        page.locator('[data-guns-search-pick="0"]').click()
        assert page.evaluate('__gunsTest.desk.execution.book().desk.slots[0].inst.symbol')=='TEST'
        assert 'OTHER' in page.locator('#guns-article-heading').inner_text()
        page.locator('#guns-quicklist [data-guns-pick="12345"]').click()
        page.locator('[data-guns-article="TEST:story1"]').click()
        page.wait_for_function("document.querySelector('#guns-article').textContent.includes('Fixture earnings article')")
        page.locator('#guns-catalyst').check()
        page.locator('#guns-room').check()
        page.keyboard.press('1')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.locator('.guns-stage-nav [data-guns-stage="trade"]').click()
        page.locator('#guns-settings summary').click()
        page.locator('#guns-stopmode').select_option('FIXED')
        page.wait_for_function("document.querySelector('#guns-chart-1')?.getBoundingClientRect().height>=300")
        for tf in ['5','d','1']:
            page.locator('[data-guns-frame="'+tf+'"]').click()
            assert page.locator('#guns-chart-'+tf).count()==1
        assert page.locator('#guns-chart-quality').inner_text().startswith('Source:')
        page.evaluate('tick()')
        assert page.locator('[data-guns="arm"]').is_enabled(),page.locator('#guns-error').inner_text()
        assert page.locator('[data-guns-confirm]').count()==5
        assert page.locator('#guns-auto').count()==0
        # Selected-strategy context updates from real quote fields, without placing orders.
        page.locator('#guns-setup').select_option('1')
        page.evaluate('tick(10.28,10.3,10.29);__gunsTest.desk.live()')
        assert '$0.21 (2.00%) below premarket high $10.50' in page.locator('#guns-strategy-summary').inner_text()
        assert 'premarket high' in page.locator('#guns-setup').get_attribute('title')
        page.locator('#guns-strategy-guide summary').click()
        assert page.locator('#guns-strategy-details').is_visible()
        for strategy,label in [('2','lower pivot'),('3','premarket flag'),('4','opening flag'),('5','09:30 candle')]:
            page.locator('#guns-setup').select_option(strategy)
            assert 'S'+strategy in page.locator('#guns-strategy-title').inner_text()
            assert label in page.locator('#guns-strategy-summary').inner_text()
        assert 'later candles never substitute' in page.locator('#guns-strategy-details').inner_text()
        page.locator('#guns-setup').select_option('1')
        page.evaluate('tick(10.68,10.7,10.69);__gunsTest.desk.live()')
        assert 'above premarket high' in page.locator('#guns-strategy-summary').inner_text()
        assert page.locator('[data-guns-confirm="1"]').is_enabled()
        assert 'Order calculated from chart data' in page.locator('#guns-entry-blockers').inner_text()
        assert 'Warning:' not in page.locator('[data-guns-confirm="1"]').inner_text()
        page.evaluate("__gunsTest.feed({connected:false,feedHealthy:false,quotes:{}});__gunsTest.desk.live()")
        assert 'distance unavailable' in page.locator('#guns-strategy-summary').inner_text()
        page.evaluate('tick();__gunsTest.desk.live()')
        assert 'below premarket high' in page.locator('#guns-strategy-summary').inner_text()
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.locator('#guns-strategy-guide summary').click()
        page.locator('[data-guns-frame="1"]').click()
        # Four independent slots; research selection never silently changes execution.
        page.locator('[data-guns="layout"]').click()
        assert page.locator('canvas[data-chart-slot]').count()==4
        page.locator('[data-guns-slot="1"]').first.click()
        page.locator('#guns-symbol').fill('OTHER');page.locator('#guns-symbol').press('Enter')
        page.locator('[data-guns-search-pick="0"]').click()
        page.locator('#guns-slot-frame-1').select_option('d')
        assert page.evaluate('__gunsTest.desk.execution.book().desk.slots[1].inst.symbol')=='OTHER'
        page.locator('[data-guns-slot="0"]').first.click()
        assert 'TEST' in page.locator('[data-guns-slot="0"]').first.inner_text()
        page.locator('[data-guns="layout"]').click()
        # Four saved layout presets and direct per-panel ticker assignment.
        for preset,expected in [('m5',['5']*4),('daily',['d']*4),('m1',['1']*4),('execution',['1','5','d','15'])]:
            page.locator('[data-guns-layout="'+preset+'"]').click()
            assert page.locator('canvas[data-chart-slot]').count()==4
            assert page.evaluate('[...document.querySelectorAll("canvas[data-chart-slot]")].map(e=>e.dataset.chartFrame)')==expected
        assert page.evaluate('new Set(__gunsTest.desk.execution.book().desk.slots.map(s=>s.inst.symbol)).size')==1
        page.locator('[data-guns-layout="m1"]').click()
        page.locator('[data-guns-slot-search="2"] input').fill('THIRD')
        page.locator('[data-guns-slot-search="2"] input').press('Enter')
        page.locator('[data-guns-slot-result="2:0"]').click()
        assert page.evaluate('__gunsTest.desk.execution.book().desk.slots[2].inst.symbol')=='THIRD'
        page.locator('#guns-slot-symbol-2').select_option('12345')
        assert page.evaluate('__gunsTest.desk.execution.book().desk.slots[2].inst.symbol')=='TEST'
        page.locator('[data-guns-layout="m5"]').click()
        page.locator('[data-guns-layout="m1"]').click()
        assert page.evaluate('__gunsTest.desk.execution.book().desk.slots[2].inst.symbol')=='TEST'
        page.locator('[data-guns-slot="0"]').first.click()
        page.locator('[data-guns="layout"]').click()
        assert page.locator('canvas[data-chart-slot]').count()==1
        # Hidden slots remain selectable while focused.
        page.locator('[data-guns-slot="1"]').first.click()
        assert page.locator('canvas[data-chart-slot="1"]').count()==1
        page.locator('[data-guns-slot="0"]').first.click()
        page.locator('[data-guns-preset="riskPct:0.25"]').click()
        assert page.locator('#guns-quick-risk').input_value()=='0.25'
        assert page.locator('#guns-risk').input_value()=='0.25'
        page.locator('[data-guns-preset="rewardR:2.5"]').click()
        assert page.locator('#guns-quick-reward').input_value()=='2.5'
        page.evaluate('document.activeElement.blur()')
        page.keyboard.press('h')
        assert page.locator('#guns-hover').is_checked()
        page.keyboard.press('h')
        assert not page.locator('#guns-hover').is_checked()
        page.keyboard.press('q')
        assert not page.locator('#guns-quick-settings').evaluate('(e)=>e.open')
        page.keyboard.press('q')
        assert page.locator('#guns-quick-settings').evaluate('(e)=>e.open')
        # Quick settings retain focused editing and update risk/R without transmitting.
        page.locator('#guns-quick-settings').evaluate('(e)=>e.open=true')
        page.locator('#guns-quick-risk').fill('0.5');page.locator('#guns-quick-risk').press('Tab')
        assert page.evaluate('__gunsTest.desk.execution.cfg().riskPct')==0.5
        page.locator('#guns-quick-reward').fill('3');page.locator('#guns-quick-reward').press('Tab')
        assert page.evaluate('__gunsTest.desk.execution.cfg().rewardR')==3
        page.locator('#guns-quick-risk').fill('1');page.locator('#guns-quick-risk').press('Tab')
        page.locator('#guns-quick-reward').fill('2');page.locator('#guns-quick-reward').press('Tab')
        page.locator('#guns-hover').check()
        page.evaluate('document.activeElement.blur()')
        page.locator('#guns-chart-1').scroll_into_view_if_needed()
        rect=page.locator('#guns-chart-1').bounding_box()
        x=rect['x']+(rect['width']-55)*60.5/80
        page.mouse.move(x,rect['y']+80);page.clock.run_for(30)
        assert page.locator('#guns-anchor-mode').inner_text().startswith('HOVER')
        high=page.locator('#guns-anchor-mode').inner_text()
        page.mouse.move(x,rect['y']+180);page.clock.run_for(30)
        assert page.locator('#guns-anchor-mode').inner_text()==high
        page.keyboard.press('1')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        assert page.locator('#guns-order-preview').is_visible()
        assert 'TEST · S1' in page.locator('#guns-preview-title').inner_text()
        assert page.locator('#guns-order-preview dd').count()==6
        page.keyboard.press('Escape')
        assert page.locator('#guns-order-preview').count()==0
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.clock.run_for(600)
        page.evaluate('tick();document.activeElement.blur()')
        page.mouse.move(x,rect['y']+180);page.clock.run_for(30)
        page.keyboard.press('1')
        assert page.locator('#guns-order-preview').is_visible()
        page.evaluate('''document.querySelector('#guns-preview-submit').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',repeat:true,bubbles:true,cancelable:true}))''')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.keyboard.press('Enter')
        page.keyboard.press('Enter')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==1
        assert page.evaluate('__gunsTest.desk.execution.pending()[0]?.guns.notes.hover.enabled') is True,page.locator('#guns-error').inner_text()
        assert page.evaluate('__gunsTest.desk.execution.pending()[0].guns.plan.levelSource')=='hover'
        page.mouse.move(0,0);page.clock.run_for(30)
        assert page.locator('#guns-anchor-mode').inner_text().startswith('AUTO')
        page.locator('[data-guns-cancel]').click()
        page.clock.run_for(600)
        page.locator('#guns-hover').uncheck()
        page.evaluate('tick()')
        page.locator('#guns-fixed').focus();page.keyboard.press('1');page.locator('#guns-fixed').fill('0.2');page.locator('#guns-fixed').press('Tab')
        page.locator('[data-guns="arm"]').focus();page.keyboard.press('1')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.locator('[data-guns="arm"]').evaluate('(e)=>e.blur()')
        page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{code:'Digit1',repeat:true,bubbles:true}))")
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.locator('summary').filter(has_text='Entry keyboard shortcuts').click()
        page.locator('[data-guns-key="0"]').focus();page.keyboard.press('Alt+q')
        assert page.locator('[data-guns-key="0"]').input_value()=='Alt+Q'
        page.locator('[data-guns-key="1"]').focus();page.keyboard.press('Alt+q')
        assert page.locator('[data-guns-key="1"]').input_value()=='2'
        page.locator('[data-guns-key="1"]').evaluate('(e)=>e.blur()')
        assert page.evaluate('GunsWorkflow.bindings(__gunsTest.desk.execution.cfg())')==['Alt+Q','2','3','4','5']
        page.keyboard.press('1')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.keyboard.press('Alt+q')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        assert page.locator('#guns-order-preview').is_visible()
        original_book=page.evaluate('__paper.S.bookId')
        page.evaluate('__paper.S.bookId="preview-account-mismatch"')
        page.keyboard.press('Enter')
        assert 'Account changed' in page.locator('#guns-preview-error').inner_text()
        page.evaluate('(id)=>__paper.S.bookId=id',original_book)
        original_cash=page.evaluate('__paper.S.cash')
        page.evaluate('__paper.S.cash=80000')
        page.keyboard.press('Enter')
        assert 'quantity updated' in page.locator('#guns-preview-error').inner_text()
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.querySelector("#guns-order-preview").scrollWidth<=document.querySelector("#guns-order-preview").clientWidth+1')
        page.set_viewport_size({'width':1440,'height':1000})
        page.keyboard.press('Enter')
        page.evaluate('(cash)=>__paper.S.cash=cash',original_cash)
        assert page.evaluate('__gunsTest.desk.execution.pending()[0].guns.notes.chartSetup')==1
        assert page.evaluate('__gunsTest.desk.execution.pending()[0].guns.notes.newsEvidence.articleId')=='story1'
        page.locator('[data-guns="tutorial"]').click()
        assert page.locator('#guns-tutorial').count()==0
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==1
        page.evaluate('''() => {__paper.S.cash=90000;tick();__gunsTest.desk.pulse();}''')
        assert page.evaluate('__gunsTest.desk.execution.pending()[0].guns.budgetAtFill')==900
        page.evaluate('tick(10.51,10.53,10.52,7)')
        assert page.evaluate('__gunsTest.desk.execution.book().active.length')==1
        assert page.evaluate('__gunsTest.desk.execution.book().active[0].qty')==7
        assert page.evaluate('__paper.S.positions.find(p=>p.conid===12345).qty')==7
        page.evaluate('''() => {const id=__paper.S.bookId;__gunsTest.createBook('blocked',1000);if(__paper.S.bookId!==id)throw Error('Unprotected book switch');}''')
        page.evaluate('''() => {const b=__gunsTest.desk.execution.book().active[0];tick(b.entry+b.initialR+.01,b.entry+b.initialR+.02,b.entry+b.initialR+.01,100);}''')
        assert page.evaluate('''() => {const b=__gunsTest.desk.execution.book().active[0];return b.stop===b.entry;}''')
        page.wait_for_timeout(20)
        page.evaluate('''() => {const b=__gunsTest.desk.execution.book().active[0];tick(b.stop-.02,b.stop-.01,b.stop-.01,3);}''')
        assert page.evaluate('__gunsTest.desk.execution.book().active[0].qty')==4
        page.wait_for_timeout(20)
        page.evaluate('''() => {const b=__gunsTest.desk.execution.book().active[0];tick(b.entry+.01,b.entry+.02,b.entry+.01,100);}''')
        assert page.evaluate('__gunsTest.desk.execution.book().active.length')==0
        assert page.evaluate('__gunsTest.desk.execution.book().journal.length')==1
        assert page.evaluate('__paper.S.positions.find(p=>p.conid===12345).qty')==0
        with page.expect_download() as downloaded:page.locator('[data-guns="export"]').click()
        assert downloaded.value.suggested_filename=='guns-paper-research.json'
        page.evaluate('__gunsTest.save()');page.wait_for_timeout(250)
        assert page.evaluate('JSON.parse(localStorage.paperAccount).guns.books[__paper.S.bookId].journal.length')==1
        # Exercise the remaining confirmation buttons with reviewed levels.
        for setup in (2,3,4):
            page.clock.run_for(600)
            page.evaluate('tick(10.19,10.21,10.20,100)')
            page.locator('#guns-setup').select_option(str(setup))
            page.locator('#guns-review-settings').evaluate('(e)=>e.open=true')
            page.locator('#guns-trigger').fill('10.2');page.locator('#guns-trigger').press('Tab')
            if setup>=3:
                page.locator('#guns-candlelow').fill('10.1');page.locator('#guns-candlelow').press('Tab')
            button=page.locator('[data-guns-confirm="'+str(setup)+'"]')
            assert button.is_enabled(),button.get_attribute('title')
            button.click()
            assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
            assert page.locator('#guns-order-preview').is_visible()
            page.keyboard.press('Enter')
            assert page.evaluate('__gunsTest.desk.execution.pending()[0]?.guns.setup')==setup,page.locator('#guns-error').inner_text()
            if setup==4:
                print('Checking Level II off-tab hold/resume/cancel',flush=True)
                page.evaluate('__gunsTest.desk.execution.pulse();__gunsTest.desk.live()')
                assert page.evaluate('__gunsTest.desk.execution.pending()[0].guns.l2.mode')=='waiting'
                depth_fixture['mode']='flag'
                # Application tab changes must not stop S4/S5 monitoring.
                page.locator('[data-tab="trade"]').first.click()
                before_depth=requests.count('/data/depth')
                page.clock.run_for(1100)
                page.evaluate('__gunsTest.desk.pulse()')
                page.wait_for_function("__gunsTest.desk.execution.pending()[0]?.guns.l2.mode==='checking'",timeout=10000)
                page.clock.run_for(1100)
                page.evaluate('__gunsTest.desk.pulse()')
                page.wait_for_function("__gunsTest.desk.execution.pending()[0]?.guns.l2.mode==='review'",timeout=10000)
                assert requests.count('/data/depth')>=before_depth+2
                page.locator('[data-tab="guns"]').click()
                page.locator('#guns-depth-alerts [data-guns-depth-decision="resume"]').click()
                assert page.evaluate('__gunsTest.desk.execution.pending()[0].guns.l2.mode')=='clear'
                # Acceptance is temporary, not a permanent override.
                page.clock.run_for(5100)
                page.evaluate('__gunsTest.desk.pulse()')
                page.wait_for_function("__gunsTest.desk.execution.pending()[0]?.guns.l2.mode==='review'",timeout=10000)
                page.locator('#guns-depth-alerts [data-guns-depth-decision="cancel"]').click()
                assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
                page.locator('#guns-depth-settings').evaluate('(e)=>e.open=true')
                page.locator('#guns-l2-cancel').check()
                page.evaluate('tick(10.19,10.21,10.20,100)')
                page.locator('[data-guns-confirm="4"]').click()
                page.keyboard.press('Enter')
                page.clock.run_for(2200)
                page.evaluate('__gunsTest.desk.pulse()')
                page.wait_for_function("__gunsTest.desk.execution.pending().length===0",timeout=10000)
                print('Level II browser decisions passed',flush=True)
                assert page.evaluate("__paper.S.orders.at(-1).guns.l2.mode")=='cancelled'
                assert 'Level II:' in page.evaluate('__paper.S.orders.at(-1).note')
                page.locator('#guns-l2-cancel').uncheck()
                depth_fixture['mode']='missing'
            else:
                page.locator('[data-guns-cancel]').click()
        page.wait_for_timeout(250)
        before=page.evaluate('JSON.stringify(__paper.S)')
        account_storage=page.evaluate('localStorage.paperAccount')
        page.locator('[data-guns="tutorial"]').click()
        page.keyboard.press('/')
        assert page.locator('#guns-workspace').count()==1
        for lesson,symbol in enumerate(('ZORA-DEMO','NIMB-DEMO','LUMA-DEMO','VELA-DEMO')):
            page.locator('[data-lesson="pick"][data-value="'+symbol+'"]').click()
            assert page.locator('[data-lesson="news"]').is_disabled()
            page.locator('[data-lesson="read"]').click()
            page.locator('[data-lesson="news"]').click()
            page.locator('[data-lesson="chart"]').click()
            for equity,budget in [('90000','900'),('100100','1,001'),('100000','1,000')]:
                page.locator('[data-lesson-equity]').select_option(equity)
                assert '1% budget: $'+budget in page.locator('#guns-tutorial').inner_text()
            page.locator('[data-lesson="risk"]').click()
            page.keyboard.press('Alt+q' if lesson==0 else str(lesson+1))
            page.locator('[data-lesson="advance"]').click()
            if lesson==3:assert 'PROTECTED 7 shares' in page.locator('#guns-tutorial').inner_text()
            for _ in range(2):
                advance=page.locator('[data-lesson="advance"]')
                if advance.count():advance.click()
            assert ('TARGET:','STOP:','CANCELLED:','BREAKEVEN STOP:')[lesson] in page.locator('#guns-tutorial').inner_text()
            assert page.evaluate('JSON.stringify(__paper.S)')==before
            assert page.evaluate('localStorage.paperAccount')==account_storage
            if lesson<3:page.locator('[data-lesson="next"]').click()
        page.locator('#guns-tutorial').evaluate('(e)=>e.dataset.testChecked="true"')
        assert page.locator('#guns-tutorial .candle-body').count()==7
        assert page.locator('#guns-tutorial polyline').count()==0
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate("document.querySelector('#guns-tutorial').scrollWidth<=document.querySelector('#guns-tutorial').clientWidth+1"),page.evaluate("(()=>{const d=document.querySelector('#guns-tutorial'),r=d.getBoundingClientRect();return {width:d.clientWidth,scroll:d.scrollWidth,rect:r.toJSON(),children:[...d.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>r.right-14).map(e=>[e.tagName,String(e.className),e.getBoundingClientRect().toJSON()])};})()")
        page.locator('[data-lesson="close"]').click()
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
        for view in ['scanner','news','trade','rules','journal']:
            page.locator('.guns-stage-nav [data-guns-stage="'+view+'"]').click()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),view
        assert page.locator('#guns-journal').count()==1
        # Outside the T-30/open window neither pulse nor quote changes rediscover.
        scans=requests.count('/data/guns_scan')
        page.clock.fast_forward(31000)  # Advance the inactive-window check without a real 31-second wait.
        page.evaluate('__gunsTest.desk.pulse()')
        assert requests.count('/data/guns_scan')==scans
        # Fresh desktop context verifies key 5 previews S5 and Enter arms it.
        history['minute'].append(dict(t=OPEN,o=10.51,h=10.54,l=10.51,c=10.53,v=1000))
        page.clock.set_fixed_time(dt.datetime.fromtimestamp((OPEN+61000)/1000,dt.timezone.utc))
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('window.__gunsTest && __gunsTest.desk')
        page.evaluate("__gunsTest.feed({connected:true,feedHealthy:true,generation:0,quotes:{12345:{bid:10.52,ask:10.54,last:10.53,bidSize:100,askSize:100,status:'LIVE',at:Date.now(),tradeAt:Date.now(),receivedAt:Date.now()}}})")
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('[data-tab="guns"]').click()
        page.locator('[data-guns="scan"]').click()
        page.locator('[data-guns-pick="12345"]').click()
        page.locator('#guns-catalyst').check();page.locator('#guns-room').check()
        page.locator('.guns-stage-nav [data-guns-stage="trade"]').click()
        page.locator('#guns-setup').select_option('5')
        page.wait_for_function("document.querySelector('[data-guns-confirm=\"5\"]') && !document.querySelector('[data-guns-confirm=\"5\"]').textContent.includes('Warning:')")
        page.evaluate('document.activeElement.blur()');page.keyboard.press('5')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        assert page.locator('#guns-order-preview').is_visible()
        page.keyboard.press('Enter')
        assert page.evaluate('__gunsTest.desk.execution.pending()[0]?.guns.setup')==5,page.locator('#guns-error').inner_text()
        page.locator('[data-guns-cancel]').click()
        # User intent is never vetoed by absent live quotes or setup assessments.
        page.evaluate("__gunsTest.feed({connected:false,feedHealthy:false,quotes:{}});__gunsTest.desk.live()")
        page.clock.set_fixed_time(dt.datetime.fromtimestamp((OPEN+62000)/1000,dt.timezone.utc))
        before_quotes=page.evaluate('JSON.stringify(__paper.Q)')
        page.locator('[data-guns-confirm="1"]').click()
        page.wait_for_selector('#guns-order-preview')
        assert page.locator('#guns-manual').count()==0
        assert page.locator('#guns-order-preview input').count()==0
        page.keyboard.press('Enter')
        assert page.evaluate('__gunsTest.desk.execution.pending()[0].guns.confirmedPlan') is True
        assert page.evaluate('__gunsTest.desk.execution.pending()[0].filledQty')==0
        page.locator('[data-guns-cancel]').click()
        # Manual assumed fills remain an optional, explicitly selected tool.
        page.locator('[data-guns="manual"]').click()
        page.locator('#guns-manual [name="price"]').fill('250000')
        page.locator('#guns-manual [name="qty"]').fill('3')
        page.locator('#guns-manual [name="qty"]').press('Enter')
        page.wait_for_function('!document.querySelector("#guns-manual")')
        trade=page.evaluate('__paper.S.trades[0]')
        assert trade['priceSource']=='USER_ENTERED_PAPER' and trade['userOverride'] is True
        assert trade['price']==250000 and trade['qty']==3 and trade['cashAfter']<0
        assert 'Fresh LIVE Gateway quote required' in trade['warnings']
        assert page.evaluate('JSON.stringify(__paper.Q)')==before_quotes
        page.set_viewport_size({'width':390,'height':844})
        page.locator('[data-guns="manual"]').click()
        assert page.evaluate('document.querySelector("#guns-manual").scrollWidth<=document.querySelector("#guns-manual").clientWidth+1')
        page.locator('#guns-manual [name="side"]').select_option('SELL')
        page.locator('#guns-manual [name="price"]').fill('250000')
        page.locator('#guns-manual [name="qty"]').fill('3')
        page.locator('#guns-manual [name="qty"]').press('Enter')
        page.wait_for_function('!document.querySelector("#guns-manual")')
        assert page.evaluate('__paper.S.positions.find(p=>p.conid===12345).qty')==0
        assert page.evaluate('__paper.S.trades[0].priceSource')=='USER_ENTERED_PAPER'
        page.wait_for_timeout(300)
        assert page.evaluate('JSON.parse(localStorage.paperAccount).trades[0].priceSource')=='USER_ENTERED_PAPER'
        # Real scanner pipeline acquires reserves; exclusions promote without touching charts/trades.
        page.set_viewport_size({'width':1440,'height':1000})
        page.evaluate("__gunsTest.feed({connected:true,feedHealthy:true,generation:0,quotes:{12345:{bid:10.48,ask:10.5,last:10.49,bidSize:100,askSize:100,status:'LIVE',at:Date.now(),tradeAt:Date.now(),receivedAt:Date.now()}}});__gunsTest.desk.execution.book().scan.acquisition=null")
        scanner_symbols[:]=[('TEST',12345)]+[('RSV'+str(i),30000+i) for i in range(1,6)]
        page.locator('[data-guns="scan"]').click()
        page.wait_for_function('__gunsTest.desk.execution.book().scan.pool.length===6')
        before_desk=page.evaluate('JSON.stringify(__gunsTest.desk.execution.book().desk)')
        page.locator('[data-guns-exclude="12345"]').check()
        assert page.locator('.guns-candidate').count()==4
        assert page.locator('.guns-candidate[data-guns-pick="30004"]').count()==1
        assert page.evaluate('JSON.stringify(__gunsTest.desk.execution.book().desk)')==before_desk
        page.locator('[data-guns="load-charts"]').click()
        assert page.locator('#guns-quickload-charts').is_visible()
        page.locator('#guns-quickload-charts').click()
        assert page.locator('canvas[data-chart-slot]').count()==4
        before_orders=page.evaluate('__paper.S.orders.length')
        page.locator('canvas[data-chart-slot="1"]').click()
        assert page.evaluate('__gunsTest.desk.execution.book().desk.active')==1
        assert 'RSV1' in page.locator('.guns-quickload-bar').inner_text()
        assert page.evaluate('__paper.S.orders.length')==before_orders
        assert page.locator('dialog[open]').count()==0
        assert page.evaluate('__gunsTest.desk.execution.book().desk.slots.map(s=>s.inst.conid)')==[30004,30001,30002,30003]
        page.wait_for_function('[...document.querySelectorAll("canvas[data-chart-slot]")].every(c=>Number(c.dataset.premarketBands)>0)')
        page.locator('[data-guns-layout="daily"]').click()
        page.wait_for_function('[...document.querySelectorAll("canvas[data-chart-slot]")].every(c=>c.dataset.premarketBands==="0")')
        page.locator('.guns-stage-nav [data-guns-stage="scanner"]').click()
        # Restoring removes the checkbox; verify resulting state instead.
        page.locator('#guns-excluded [data-guns-exclude="12345"]').click()
        assert page.evaluate('__gunsTest.desk.execution.book().scan.excluded')==[]
        assert page.locator('.guns-candidate[data-guns-pick="12345"]').count()==1
        assert not errors,errors
        print(json.dumps({'guns':'verified scanner, local key form, advisory/manual fills, gap provenance, reserve promotion/quick-load/premarket shading, S1-S5, charts/hover/news, off-tab Level II hold/resume/auto-cancel, tutorial isolation, dynamic risk, paper lifecycle, persistence/mobile','browser_errors':errors}))
        page.unroute_all(behavior='ignoreErrors')
        browser.close()


if __name__=='__main__':main()
