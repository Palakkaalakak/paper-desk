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
    history=dict(symbol='TEST',conid=12345,stockType='COMMON',minTick=.01,updatedAt=OPEN+10000,
                 sessions=[dict(start=OPEN,end=OPEN+23400000)],minute=[],daily=[])
    for i in range(1500):
        close=8.99+i*.001
        history['minute'].append(dict(t=OPEN-(1500-i)*60000,o=close-.001,h=close+.011,l=close-.01,c=close,v=1000))
    for i in range(240):
        day=dt.date(2026,9,9)-dt.timedelta(days=239-i)
        history['daily'].append(dict(t=day.isoformat(),o=8,h=9.1,l=7.9,c=9,v=100000))
    errors=[]
    requests=[]
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
            if path=='/data/guns_scan':return r.fulfill(json={'rows':[dict(conid=12345,symbol='TEST',name='Fixture stock',secType='STK',brokerId=True)]})
            if path=='/data/search':
                sym=(params.get('q') or ['TEST'])[0].upper()
                return r.fulfill(json={'results':[dict(conid=12345 if sym=='TEST' else 23456,symbol=sym,name='Fixture '+sym,type='STK')]})
            if path=='/data/guns_bars':
                sym=(params.get('symbol') or ['TEST'])[0]
                return r.fulfill(json={**history,'symbol':sym,'conid':12345 if sym=='TEST' else 23456,'updatedAt':int(r.request.headers.get('x-fixture-now',str(OPEN+10000)))})
            if path=='/data/guns_schedule':return r.fulfill(json={'sessions':[dict(start=OPEN,end=OPEN+23400000)]})
            if path=='/data/guns_float':return r.fulfill(json={'floatShares':15000000,'date':'2026-09-09','source':'Fixture float'})
            if path=='/data/guns_sources':return r.fulfill(json={'rows':[dict(headline='Fixture linked company release',provider='Fixture RSS',time='2026-09-10',articleId='',url='https://example.com/release',contentStatus='excerpt',text='Fixture source excerpt')],'diagnostics':[]})
            if path=='/data/guns_verify':return r.fulfill(json=dict(conid=12345,at=OPEN+10000,sessionDate='2026-09-10',sessionKnown=True,stockType='COMMON',previousClose=9,previousCloseDate='2026-09-09',premarketVolume=330000,observedBars=330,source='Fixture IB TRADES',volumeUnit='shares',coverage='Observed bars only'))
            if path=='/data/guns_news':return r.fulfill(json={'source':'Fixture API news','at':OPEN+10000,'providers':[dict(code='TEST',name='Fixture provider')],'rows':[dict(time='2026-09-10',provider='TEST',articleId='story1',headline='Fixture earnings beat'),dict(time='2026-09-10',provider='TEST',articleId='footer',headline='Footer-only fixture')]})
            if path=='/data/guns_article' and params.get('articleId')==['footer']:return r.fulfill(json={'text':'','rawText':'(END) Copyright fixture','contentStatus':'incomplete'})
            if path=='/data/guns_article':return r.fulfill(json={'text':'Fixture earnings article <img src=x onerror=alert(1)>','provider':'TEST','articleId':'story1','contentStatus':'body_returned','rawText':'Fixture earnings article <img src=x onerror=alert(1)>'})
            if path=='/data/depth':return r.fulfill(json={'bids':[dict(price=10.48,size=100)],'asks':[dict(price=10.50,size=100)]})
            if path.startswith('/api/'):return r.fulfill(json={})
            return r.fulfill(status=404,body='Unexpected fixture request')
        page.route('**/*',route)
        # Direct test feed supplies quotes; avoid EOF/reconnect races in this fixture.
        page.add_init_script("const realFetch=window.fetch;window.fetch=(url,options={})=>{const headers=new Headers(options.headers);headers.set('X-Fixture-Now',String(Date.now()));return realFetch(url,{...options,headers});};")
        page.add_init_script('window.EventSource=class {constructor(){this.readyState=1;} close(){} addEventListener(){}};')
        page.add_init_script('localStorage.setItem("paperAccount",'+json.dumps(json.dumps(state))+');')
        page.goto('http://paper.test/',wait_until='domcontentloaded')
        page.wait_for_function('window.__gunsTest && __gunsTest.desk')
        page.evaluate('''() => {window.tick=(bid=10.48,ask=10.5,last=10.49,size=100)=>__gunsTest.feed({connected:true,feedHealthy:true,generation:0,quotes:{12345:{bid,ask,last,bidSize:size,askSize:size,status:'LIVE',at:Date.now(),receivedAt:Date.now()}}});tick();}''')
        page.locator('[data-tab="guns"]').click()
        page.locator('[data-guns="scan"]').click()
        page.locator('[data-guns-pick="12345"]').click()
        page.wait_for_function("document.querySelector('#guns-clock').textContent.includes('s old')")
        page.evaluate('__gunsTest.desk.pulse()')
        page.locator('.guns-stage-nav [data-guns-stage="scanner"]').click()
        page.wait_for_function("document.querySelector('#guns-scanner').textContent.includes('IBKR screened')")
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
            assert page.evaluate('__gunsTest.desk.execution.pending()[0]?.guns.setup')==setup,page.locator('#guns-error').inner_text()
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
        page.clock.run_for(31000)
        page.evaluate('__gunsTest.desk.pulse()')
        assert requests.count('/data/guns_scan')==scans
        # Fresh desktop context verifies that key 5 actually places S5, not a selector.
        history['minute'].append(dict(t=OPEN,o=10.51,h=10.54,l=10.51,c=10.53,v=1000))
        page.clock.set_fixed_time(dt.datetime.fromtimestamp((OPEN+61000)/1000,dt.timezone.utc))
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('window.__gunsTest && __gunsTest.desk')
        page.evaluate("__gunsTest.feed({connected:true,feedHealthy:true,generation:0,quotes:{12345:{bid:10.52,ask:10.54,last:10.53,bidSize:100,askSize:100,status:'LIVE',at:Date.now(),receivedAt:Date.now()}}})")
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('[data-tab="guns"]').click()
        page.locator('[data-guns="scan"]').click()
        page.locator('[data-guns-pick="12345"]').click()
        page.locator('#guns-catalyst').check();page.locator('#guns-room').check()
        page.locator('.guns-stage-nav [data-guns-stage="trade"]').click()
        page.locator('#guns-setup').select_option('5')
        page.wait_for_function("!document.querySelector('[data-guns-confirm=\"5\"]').disabled")
        page.evaluate('document.activeElement.blur()');page.keyboard.press('5')
        assert page.evaluate('__gunsTest.desk.execution.pending()[0]?.guns.setup')==5,page.locator('#guns-error').inner_text()
        page.locator('[data-guns-cancel]').click()
        assert not errors,errors
        print(json.dumps({'guns':'verified scanner, escaped articles, S1-S5 controls, four charts, hover capture, news fallback, focus guards, four-stock tutorial isolation, dynamic risk, paper lifecycle, persistence/mobile','browser_errors':errors}))
        page.unroute_all(behavior='ignoreErrors')
        browser.close()


if __name__=='__main__':main()
