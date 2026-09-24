"""Offline Trading/Custom browser regressions; never connects to a broker."""
import datetime as dt
import json
import pathlib
import re
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
ROOT=pathlib.Path(__file__).resolve().parent
NOW=int(dt.datetime(2026,9,10,14,30,tzinfo=dt.timezone.utc).timestamp()*1000)

def main():
    html=(ROOT/'paper_local.html').read_text().replace('/* ---------- debug surface ---------- */', '''
    window.__deskTest={get engine(){return trading;},feed:feedApply,sweep:sweepOrders,save:save,
    select:function(){sel={conid:123,symbol:'TEST',secType:'STK',mult:1,exch:'SMART',brokerId:true};tab='trade';render();syncSubs();}};
    /* ---------- debug surface ---------- */''')
    state=json.loads(re.search(r'<script id="st" type="application/json">(.*?)</script>',html).group(1))
    state['settings'].update(dataSource='free',provider='tws',auto=False)
    state['cash']=100000
    history=dict(symbol='TEST',conid=123,minTick=.01,updatedAt=NOW,minute=[],daily=[])
    for i in range(450):
        c=10+i*.01
        history['minute'].append(dict(t=NOW-(450-i)*60000,o=c,h=c+.1,l=c-.1,c=c,v=1000))
    errors=[];requests=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1300,'height':950})
        page.clock.install(time=dt.datetime.fromtimestamp(NOW/1000,tz=dt.timezone.utc))
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            path=urlsplit(r.request.url).path;requests.append(path)
            if path=='/':return r.fulfill(content_type='text/html',body=html)
            if path.startswith('/assets/'):
                name=path.rsplit('/',1)[-1]
                if name in ('guns.js','guns-execution.js','guns-workflow.js','guns-tutorial.js','guns-ui.js','guns.css','trading.js','trading-ui.js'):
                    return r.fulfill(content_type='text/css' if name.endswith('.css') else 'text/javascript',body=(ROOT/name).read_text())
            if path=='/data/providers':return r.fulfill(json={'providers':{'tws':{'name':'IB Gateway','local':True,'quotes':True,'chain':True}}})
            if path=='/data/subscriptions':return r.fulfill(json={'connected':True,'feedHealthy':True,'generation':0,'quotes':{}})
            if path=='/data/stream':return r.fulfill(content_type='text/event-stream',body=': fixture\n\n')
            if path=='/data/twsstatus':return r.fulfill(json={'connected':True,'sealed':True,'readonly':True,'accounts':[]})
            if path=='/data/guns_bars':return r.fulfill(json=history)
            if path=='/data/guns_schedule':return r.fulfill(json={'sessions':[]})
            if path.startswith('/api/'):return r.fulfill(json={'authenticated':False})
            return r.fulfill(status=404,body='No fixture')
        page.route('**/*',route)
        page.add_init_script('if(!localStorage.paperAccount)localStorage.paperAccount='+json.dumps(json.dumps(state))+';')
        page.goto('http://paper.test/',wait_until='domcontentloaded')
        page.wait_for_function('window.__deskTest && __deskTest.engine')
        page.locator('[data-tab="acct"]').click()
        page.locator('#bkName').fill('ATR book');page.locator('#bkCash').fill('100000');page.locator('#bkMode').select_option('Trading');page.locator('#bkAdd').click()
        assert page.evaluate('__paper.S.account.mode')=='Trading'
        book=page.evaluate('__paper.S.bookId');page.evaluate('__deskTest.select()')
        def feed(bid=19.99,ask=20):
            page.evaluate('''([bid,ask])=>__deskTest.feed({connected:true,feedHealthy:true,generation:0,quotes:{123:{bid,ask,last:ask,bidSize:1,askSize:1,status:'LIVE',at:Date.now(),source:'IB Gateway'}}})''',[bid,ask])
        def shortcut():
            page.evaluate('document.activeElement.blur()');page.keyboard.press('1')
        feed();shortcut();page.wait_for_selector('#desk-confirm:not([disabled])')
        assert page.locator('#guns-order-preview').count()==0 and page.evaluate('__paper.S.orders.length')==0
        page.keyboard.press('Escape');page.wait_for_selector('#desk-order-preview',state='detached')
        shortcut();page.wait_for_selector('#desk-confirm:not([disabled])')
        page.locator('#desk-tag').fill('VWAP reclaim');page.locator('#desk-minutes').fill('1');page.locator('#desk-later').check()
        page.locator('#desk-recalculate').click();page.wait_for_selector('#desk-confirm:not([disabled])')
        page.keyboard.press('Enter');page.wait_for_selector('#desk-order-preview',state='detached')
        assert page.evaluate('__paper.S.orders.length')==1
        feed();page.evaluate('__deskTest.sweep()')
        b=page.evaluate('__deskTest.engine.book().active[0]')
        assert b['qty']>1 and b['target'] is None and b['budget']==1000 and b['atr']['value']>0
        assert page.evaluate('__paper.S.trades[0].strategyType')=='VWAP reclaim'
        page.evaluate("__paper.bookSwitch('b1')");assert page.evaluate('__paper.S.bookId')==book
        feed(20.5,20.51);page.evaluate('__deskTest.engine.manage()')
        assert page.evaluate('__deskTest.engine.book().active[0].stop')==b['stop']
        page.once('dialog',lambda d:d.accept('21'));page.locator('[data-desk-target]').click()
        feed(21.2,21.21);page.evaluate('__deskTest.engine.manage()')
        j=page.evaluate('__deskTest.engine.book().journal[0]')
        assert j['outcome']=='TARGET' and j['exitPrice']==21 and j['tracking']['minutes']==1
        cash=page.evaluate('__paper.S.cash')
        page.clock.run_for(1100);feed(21.5,21.51);page.evaluate('__deskTest.engine.track()')
        assert page.evaluate('__deskTest.engine.book().journal[0].tracking.max')==21.5 and page.evaluate('__paper.S.cash')==cash
        page.evaluate("__paper.bookCreate('Manual book',20000,'Custom')")
        custom=page.evaluate('__paper.S.bookId')
        assert page.evaluate('__deskTest.engine.settings().trackMinutes')==5 and page.evaluate('__deskTest.engine.book().journal.length')==0
        page.clock.run_for(1100);feed(22,22.01);page.evaluate('__deskTest.engine.track()')
        assert page.evaluate('(id)=>__paper.bookOf(id).data.desk.journal[0].tracking.max',book)==22
        page.evaluate('__deskTest.select()');feed();calls=requests.count('/data/guns_bars');shortcut()
        assert page.locator('#desk-qty').input_value()=='' and requests.count('/data/guns_bars')==calls
        page.locator('#desk-qty').fill('10');page.locator('#desk-tag').fill('Discretionary');page.locator('#desk-recalculate').click()
        page.wait_for_selector('#desk-confirm:not([disabled])');page.keyboard.press('Enter');page.wait_for_selector('#desk-order-preview',state='detached')
        feed();page.evaluate('__deskTest.sweep()')
        b=page.evaluate('__deskTest.engine.book().active[0]')
        assert b['qty']==10 and b['stop'] is None and b['target'] is None and b['atr'] is None
        page.locator('[data-desk-flat]').click()
        assert page.evaluate('__deskTest.engine.book().journal[0].strategy')=='Discretionary'
        page.set_viewport_size({'width':390,'height':844});shortcut()
        assert page.locator('#desk-order-preview').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        page.keyboard.press('Escape');page.wait_for_selector('#desk-order-preview',state='detached')
        page.clock.fast_forward(65000);page.evaluate('__deskTest.engine.track();__deskTest.save()');page.clock.run_for(200)
        tracking=page.evaluate('(id)=>__paper.bookOf(id).data.desk.journal[0].tracking',book)
        assert tracking['status']=='finished with gaps' and tracking['gapMs']>0
        page.locator('[data-tab="hist"]').click();assert 'Discretionary' in page.locator('.desk-journal').inner_text()
        page.reload(wait_until='domcontentloaded');page.wait_for_function('window.__deskTest && __deskTest.engine')
        assert page.evaluate('__paper.S.bookId')==custom and page.evaluate('__paper.S.account.mode')=='Custom'
        assert page.evaluate('__deskTest.engine.book().journal[0].strategy')=='Discretionary'
        page.evaluate('(id)=>__paper.bookSwitch(id)',book)
        assert page.evaluate('__paper.S.account.mode')=='Trading' and page.evaluate('__deskTest.engine.book().journal[0].tracking.status')=='finished with gaps'
        assert not errors,errors
        print(json.dumps({'Trading_Custom_ATR_TP_later_tracking_mobile_persistence':'passed','browser_errors':errors}))
        browser.close()
if __name__=='__main__':main()
