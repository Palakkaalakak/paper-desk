"""Offline GUNS end-to-end regression; all prices/news are isolated test fixtures."""
import datetime as dt
import json
import pathlib
import re
from urllib.parse import urlsplit
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
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.clock.install(time=NOW)
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            path=urlsplit(r.request.url).path
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
            if path=='/data/search':return r.fulfill(json={'results':[dict(conid=12345,symbol='TEST',name='Fixture stock',type='STK')]})
            if path=='/data/guns_bars':return r.fulfill(json={**history,'updatedAt':page.evaluate('Date.now()')})
            if path=='/data/guns_verify':return r.fulfill(json=dict(conid=12345,at=OPEN+10000,sessionDate='2026-09-10',sessionKnown=True,stockType='COMMON',previousClose=9,previousCloseDate='2026-09-09',premarketVolume=330000,observedBars=330,source='Fixture IB TRADES',volumeUnit='shares',coverage='Observed bars only'))
            if path=='/data/guns_news':return r.fulfill(json={'source':'Fixture API news','at':OPEN+10000,'providers':[dict(code='TEST',name='Fixture provider')],'rows':[dict(time='2026-09-10',provider='TEST',articleId='story1',headline='Fixture earnings beat')]})
            if path=='/data/guns_article':return r.fulfill(json={'text':'Fixture earnings article <img src=x onerror=alert(1)>','provider':'TEST','articleId':'story1','contentStatus':'body_returned','rawText':'Fixture earnings article <img src=x onerror=alert(1)>'})
            if path=='/data/depth':return r.fulfill(json={'bids':[dict(price=10.48,size=100)],'asks':[dict(price=10.50,size=100)]})
            if path.startswith('/api/'):return r.fulfill(json={})
            return r.fulfill(status=404,body='Unexpected fixture request')
        page.route('**/*',route)
        # Direct test feed supplies quotes; avoid EOF/reconnect races in this fixture.
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
        page.wait_for_function("document.querySelector('#guns-scanner').textContent.includes('SCREEN PASS')")
        page.locator('.guns-stage-nav [data-guns-stage="news"]').click()
        page.locator('[data-guns-article="0"]').click()
        page.wait_for_function("document.querySelector('#guns-article').textContent.includes('Fixture earnings article')")
        assert page.locator('#guns-article img').count()==0
        assert not page.locator('#guns-catalyst').is_checked()
        assert 'Fixture provider' in page.locator('#guns-news-meta').inner_text()
        page.locator('#guns-catalyst').check()
        page.locator('#guns-room').check()
        page.keyboard.press('1')
        assert page.evaluate('__gunsTest.desk.execution.pending().length')==0
        page.locator('.guns-stage-nav [data-guns-stage="trade"]').click()
        page.locator('#guns-settings summary').click()
        page.locator('#guns-stopmode').select_option('FIXED')
        assert page.locator('#guns-chart-1').bounding_box()['height']>=300
        for tf in ['5','d','1']:
            page.locator('[data-guns-frame="'+tf+'"]').click()
            assert page.locator('#guns-chart-'+tf).count()==1
        assert page.locator('#guns-chart-quality').inner_text().startswith('Source:')
        page.evaluate('tick()')
        assert page.locator('[data-guns="arm"]').is_enabled(),page.locator('#guns-error').inner_text()
        assert page.locator('[data-guns-confirm]').count()==4
        assert page.locator('#guns-auto').count()==0
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
        assert page.evaluate('GunsWorkflow.bindings(__gunsTest.desk.execution.cfg())')==['Alt+Q','2','3','4']
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
        assert not errors,errors
        print(json.dumps({'guns':'verified scanner, escaped articles, S1-S4 controls, focus guards, four-stock tutorial isolation, dynamic risk, paper lifecycle, persistence/mobile','browser_errors':errors}))
        browser.close()


if __name__=='__main__':main()
