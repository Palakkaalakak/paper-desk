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
                if name in ('guns.js','guns-execution.js','guns-ui.js','guns.css'):
                    return r.fulfill(content_type='text/css' if name.endswith('.css') else 'text/javascript',body=(ROOT/name).read_text())
            if path=='/data/providers':return r.fulfill(json={'providers':{'tws':{'name':'IB Gateway','local':True,'quotes':True,'chain':True}}})
            if path=='/data/subscriptions':return r.fulfill(json={'connected':True,'feedHealthy':True,'generation':0,'quotes':{}})
            if path=='/data/stream':return r.fulfill(content_type='text/event-stream',body=': fixture\n\n')
            if path in ('/data/twsstatus','/api/iserver/auth/status'):return r.fulfill(json={'connected':True,'authenticated':True})
            if path=='/data/guns_scan':return r.fulfill(json={'rows':[dict(conid=12345,symbol='TEST',name='Fixture stock',secType='STK',brokerId=True)]})
            if path=='/data/search':return r.fulfill(json={'results':[dict(conid=12345,symbol='TEST',name='Fixture stock',type='STK')]})
            if path=='/data/guns_bars':return r.fulfill(json=history)
            if path=='/data/guns_news':return r.fulfill(json={'rows':[dict(time='2026-09-10',provider='TEST',headline='Fixture earnings beat')]})
            if path=='/data/depth':return r.fulfill(json={'bids':[dict(price=10.48,size=100)],'asks':[dict(price=10.50,size=100)]})
            if path.startswith('/api/'):return r.fulfill(json={})
            return r.fulfill(status=404,body='Unexpected fixture request')
        page.route('**/*',route)
        page.add_init_script('localStorage.setItem("paperAccount",'+json.dumps(json.dumps(state))+');')
        page.goto('http://paper.test/',wait_until='domcontentloaded')
        page.wait_for_function('window.__gunsTest && __gunsTest.desk')
        page.evaluate('''() => {window.tick=(bid=10.48,ask=10.5,last=10.49,size=100)=>__gunsTest.feed({connected:true,feedHealthy:true,generation:0,quotes:{12345:{bid,ask,last,bidSize:size,askSize:size,status:'LIVE',at:Date.now(),receivedAt:Date.now()}}});tick();}''')
        page.locator('[data-tab="guns"]').click()
        page.locator('[data-guns="scan"]').click()
        page.locator('[data-guns-pick="0"]').click()
        page.wait_for_function("document.querySelector('#guns-clock').textContent.includes('s old')")
        page.locator('#guns-stopmode').select_option('FIXED')
        page.locator('#guns-catalyst').check()
        page.locator('#guns-room').check()
        page.evaluate('tick()')
        assert page.locator('[data-guns="arm"]').is_enabled(),page.locator('#guns-error').inner_text()
        page.locator('[data-guns="arm"]').click()
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
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
        assert not errors,errors
        print(json.dumps({'guns':'scanner, charts, review, dynamic sizing, partial fill, breakeven, latched exit, journal/export, persistence, mobile','browser_errors':errors}))
        browser.close()


if __name__=='__main__':main()
