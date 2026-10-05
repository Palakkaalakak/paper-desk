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
    window.__deskTest={get engine(){return trading;},get ui(){return tradingUI;},feed:feedApply,sweep:sweepOrders,save:save,
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
            if path=='/data/guns_schedule':return r.fulfill(json={'sessions':[{'start':NOW-3600000,'end':NOW+19800000}]})
            if path.startswith('/api/'):return r.fulfill(json={'authenticated':False})
            return r.fulfill(status=404,body='No fixture')
        page.route('**/*',route)
        page.add_init_script('if(!localStorage.paperAccount)localStorage.paperAccount='+json.dumps(json.dumps(state))+';')
        page.goto('http://localhost:8765/',wait_until='domcontentloaded')
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
        assert page.evaluate('__paper.S.orders[0].desk.paused') is True
        assert page.locator('[data-desk-cancel]').count()==1
        feed();page.evaluate('__deskTest.sweep()')
        assert page.evaluate('__deskTest.engine.book().active.length')==0
        feed(20.4,20.5)  # Outside entry limit while testing pause, not a fill race.
        page.locator('[data-desk-start]').click();page.locator('[data-desk-pause]').click()
        feed();page.evaluate('__deskTest.sweep()')
        assert page.evaluate('__deskTest.engine.book().active.length')==0
        page.locator('[data-desk-start]').click()
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
        page.locator('[data-desk-start]').click()
        feed();page.evaluate('__deskTest.sweep()')
        b=page.evaluate('__deskTest.engine.book().active[0]')
        assert b['qty']==10 and b['stop'] is None and b['target'] is None and b['atr'] is None
        page.once('dialog',lambda d:d.accept());page.locator('[data-desk-flat]').click()
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
        # Two stocks remain visible, cancelled entries cannot fill, and 10197
        # blocks otherwise LIVE quotes for both entry and protective exits.
        page.evaluate("__paper.bookCreate('Safety fixture',100000,'Custom');__deskTest.select()")
        def both(bid=19.99,ask=20,problem=''):
            page.evaluate('''([bid,ask,problem])=>{const q={bid,ask,last:ask,bidSize:1,askSize:1,status:'LIVE',at:Date.now(),source:'IB Gateway'};__deskTest.feed({connected:true,feedHealthy:true,generation:0,problem,quotes:{201:q,202:q}});__deskTest.sweep();__deskTest.engine.manage();__deskTest.ui.live();}''',[bid,ask,problem])
        both()
        page.evaluate('''()=>{for(const [conid,symbol] of [[201,'BKYI'],[202,'AVAT']]){const inst={conid,symbol,secType:'STK',mult:1,brokerId:true};const p=__deskTest.engine.plan(inst,{entry:20,stop:19,target:22,qty:10,timeframe:'5',trackMinutes:1,type:'LMT',sessions:[{start:Date.now()-3600000,end:Date.now()+18000000}]},null);__deskTest.engine.arm(inst,p,__paper.S.bookId);}__deskTest.ui.live();}''')
        panel=page.locator('#desk-active').inner_text()
        assert 'BKYI' in panel and 'AVAT' in panel and page.locator('[data-desk-cancel]').count()==2
        avat=page.evaluate("__deskTest.engine.pending().find(o=>o.symbol==='AVAT').id")
        page.locator('[data-desk-cancel="'+avat+'"]').click()
        both();assert page.evaluate("__paper.S.orders.find(o=>o.id==='"+avat+"').status")=='cancelled'
        levels=page.evaluate('__deskTest.ui.levels({conid:201})')
        assert levels=={'entry':20,'stop':19,'target':22}
        assert page.evaluate('__deskTest.ui.levels({conid:999})')=={}
        both(problem='10197: No market data during competing live session')
        page.locator('[data-desk-start]').click();both(problem='10197: No market data during competing live session')
        assert page.evaluate('__deskTest.engine.book().active.length')==0
        both();assert page.evaluate('__deskTest.engine.book().active[0].inst.symbol')=='BKYI'
        both(18.5,18.51,'10197: No market data during competing live session')
        assert page.evaluate('__deskTest.engine.book().active.length')==1
        both(18.5,18.51);assert page.evaluate('__deskTest.engine.book().journal[0].outcome')=='STOP'
        panel=page.locator('#desk-active').inner_text();assert 'BKYI · CLOSED' in panel and 'AVAT · CANCELLED' in panel
        owner_checks(browser,route,state,errors)
        assert not errors,errors
        print(json.dumps({'Trading_Custom_ATR_TP_later_tracking_mobile_persistence':'passed','browser_errors':errors}))
        browser.close()
def owner_checks(browser,route,state,errors):
    fields=['account','cash','realized','lastDay','positions','orders','trades','cashflows','equity','watchlist','log','desk']
    original=json.loads(json.dumps(state))
    base={k:original[k] for k in fields if k in original}
    desk=json.loads(json.dumps(base));pam=json.loads(json.dumps(base))
    desk.update(account={'name':'Paper-Desk','mode':'Trading','type':'margin','currency':'USD'},cash=81234.5,realized=234.5,
                positions=[],orders=[],trades=[],watchlist=[],log=[],equity=[],desk={'settings':{},'active':[],'journal':[]})
    pam.update(account={'name':'PAM','mode':'Trading','type':'margin','currency':'USD'},cash=98566.8,realized=-1433.2,
               positions=[{'conid':999,'symbol':'PAMONLY','qty':1,'avgCost':12,'secType':'STK','mult':1}],
               orders=[{'id':'pam-order','conid':999,'status':'working'}],trades=[{'symbol':'AVAT','realized':-1662.21}],
               desk={'settings':{},'active':[],'journal':[]})
    original.update(pam);original.update(bookId='pam',books=[{'id':'desk','name':'Paper-Desk','data':desk},{'id':'pam','name':'PAM','data':pam}],guns={'config':{},'books':{'desk':{'notes':{},'active':[],'journal':[]},'pam':{'notes':{},'active':[],'journal':[{'id':'pam-journal'}]}}})
    seed=json.dumps(original);backup='paperAccount.backup.paperdesk-owner-results-20260930-v1'
    for failure in (None,'backup','account'):
        context=browser.new_context();context.route('**/*',route)
        script='if(!localStorage.paperAccount)localStorage.paperAccount='+json.dumps(seed)+';'
        if failure:
            condition="k.startsWith('paperAccount.backup.')" if failure=='backup' else "k==='paperAccount'"
            script+="const originalSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if("+condition+")throw new DOMException('fixture storage failure','QuotaExceededError');return originalSet.call(this,k,v);};"
        context.add_init_script(script)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://localhost:8765/',wait_until='domcontentloaded')
        if failure:
            page.wait_for_function("document.querySelector('#banner').textContent.includes('Account update NOT applied')")
            assert page.evaluate('localStorage.paperAccount')==seed
            assert page.evaluate('__paper.S.bookId')=='pam' and page.evaluate('__deskTest.engine') is None
            assert page.evaluate('(key)=>localStorage.getItem(key)',backup)==(None if failure=='backup' else seed)
        else:
            try:
                page.wait_for_function('window.__deskTest && __deskTest.engine',timeout=5000)
            except Exception as e:
                raise AssertionError((page.locator('#banner').text_content(),errors)) from e
            assert page.evaluate('__paper.S.bookId')=='desk'
            assert page.evaluate('__paper.S.cash')==87314.5 and page.evaluate('__paper.S.realized')==6314.5
            assert page.evaluate('__paper.S.positions.length+__paper.S.orders.length')==0
            assert page.evaluate('__paper.S.books.map(b=>b.name)')==['Paper-Desk']
            assert page.evaluate('__paper.S.equity.at(-1).equity')==87314.5
            assert page.evaluate('(key)=>localStorage.getItem(key)',backup)==seed
            page.locator('[data-tab="hist"]').click();journal=page.locator('.desk-journal').inner_text()
            for symbol in ('JAGX','USDE','CYPH'):assert symbol in journal
            assert '20:55 Asia/Bangkok; date not supplied' in journal
            assert 'Exit time unknown' in journal and '1970' not in journal and 'unknown quantity' in journal
            assert 'OWNER-REQUESTED CREDIT' in page.locator('.desk-journal').text_content()
            page.reload(wait_until='domcontentloaded');page.wait_for_function('window.__deskTest && __deskTest.engine')
            assert page.evaluate('__paper.S.cash')==87314.5 and page.evaluate('__paper.S.trades.length')==3
            assert page.evaluate('(key)=>localStorage.getItem(key)',backup)==seed
            other=context.new_page();other.goto('http://localhost:8765/',wait_until='domcontentloaded')
            other.wait_for_function("document.querySelector('#banner').textContent.includes('already open in another tab')")
            assert other.evaluate('__deskTest.engine') is None
            assert other.evaluate('JSON.parse(localStorage.paperAccount).cash')==87314.5
        context.close()
    print('Owner boot: backup, credits, PAM isolation, equity, reload, storage failures and single writer passed')

if __name__=='__main__':main()
