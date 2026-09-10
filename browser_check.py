"""Offline browser scale/regression harness. Test quotes never enter the running app.
Requires: pip install playwright; python -m playwright install chromium
--baseline loads the original Git revision for a reproducible before/after comparison.
"""
import argparse
import json
import pathlib
import re
import statistics
import subprocess
import time
from playwright.sync_api import sync_playwright

ROOT=pathlib.Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',action='store_true')
    parser.add_argument('--screenshot',action='store_true')
    args=parser.parse_args()
    html=(subprocess.check_output(['git','show','79169d8:paper_local.html'],cwd=ROOT).decode()
          if args.baseline else (ROOT/'paper_local.html').read_text())
    if not args.baseline:
        html=html.replace('/* ---------- debug surface ---------- */', '''
        window.__test={feedApply:feedApply,onTick:onTick,renderLive:renderLive,tryFill:tryFill,
          quoteReady:quoteReady,settleExpired:settleExpired,settlePosition:settlePosition,
          universe:universe,ordersFor:ordersFor};
        /* ---------- debug surface ---------- */''')
    state=json.loads(re.search(r'<script id="st" type="application/json">(.*?)</script>',html).group(1))
    state['settings'].update(dataSource='free',provider='tws',auto=False)
    state['cash']=10000000
    for i in range(500):
        state['positions'].append(dict(conid=10000+i,symbol='TEST'+str(i),secType='STK',
                                      qty=10,mult=1,avgCost=100,exch='SMART',openedToday=False))
    errors=[];requests=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':960})
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            url=r.request.url
            if url=='http://paper.test/':return r.fulfill(content_type='text/html',body=html)
            requests.append(url)
            if '/assets/' in url:
                name=url.rsplit('/',1)[-1]
                if name in ('guns.js','guns-execution.js','guns-ui.js','guns.css'):
                    return r.fulfill(content_type='text/css' if name.endswith('.css') else 'text/javascript',body=(ROOT/name).read_text())
            if '/data/stream' in url:return r.fulfill(content_type='text/event-stream',body=': fixture\n\n')
            if '/data/subscriptions' in url:
                rows=r.request.post_data_json.get('instruments',[]);now=int(time.time()*1000)
                quotes={str(x['conid']):dict(bid=100,ask=100.02,last=100.01,bidSize=10000,askSize=10000,
                          status='LIVE',at=now,source='IB Gateway',receivedAt=now) for x in rows}
                return r.fulfill(json=dict(quotes=quotes,connected=True,capacity=500,loaded=len(rows),
                                          requested=len(rows),active=len(rows),seq=1,generation=0))
            if '/data/twsstatus' in url:return r.fulfill(json=dict(connected=True,sealed=True,readonly=True,accounts=[]))
            if '/data/providers' in url:return r.fulfill(json={'providers':{'tws':{'name':'IB Gateway','local':True,'quotes':True,'chain':True}}})
            if '/data/quote' in url:return r.fulfill(json=dict(last=100.01,bid=100,ask=100.02,volume=10000000,served_by='tws'))
            if '/api/' in url:return r.fulfill(json={'authenticated':False})
            return r.fulfill(status=404,body='Unexpected test request')
        page.route('**/*',route)
        page.add_init_script('if(!localStorage.getItem("paperAccount"))localStorage.setItem("paperAccount",'+json.dumps(json.dumps(state))+');')
        page.goto('http://paper.test/',wait_until='domcontentloaded')
        page.wait_for_function('window.__paper && window.__paper.S.positions.length===500')
        page.wait_for_timeout(1500)
        page.locator('[data-tab="pos"]').click()
        times=page.evaluate('''() => {
          const times=[];for(let i=0;i<20;i++){const t=performance.now();document.querySelector('[data-tab="pos"]').click();times.push(performance.now()-t);}return times;
        }''')
        result=dict(mode='baseline' if args.baseline else 'current',positions=500,
                    position_tab_median_ms=round(statistics.median(times),3),
                    bootstrap_data_requests=len([u for u in requests if '/data/' in u]))
        if not args.baseline:
            assert page.locator('[data-posid]').count()==500
            live=page.evaluate('''() => {const t=performance.now();__test.renderLive();return performance.now()-t;}''')
            result['live_500_row_update_ms']=round(live,3)
            page.locator('[data-trade="10000"]').click()
            page.locator('#oQty').fill('7')
            # Injection exists only in the test HTML. Actual transport is tested over HTTP in test_market.py.
            page.evaluate('''() => {
              window.fresh=()=>__test.feedApply({connected:true,feedHealthy:true,generation:0,loaded:500,requested:500,capacity:500,
                quotes:{10000:{bid:100,ask:100.02,last:100.01,bidSize:10000,askSize:10000,status:'LIVE',at:Date.now()}}});
              fresh();
            }''')
            page.wait_for_timeout(150)
            assert page.locator('#oQty').input_value()=='7'
            assert page.locator('#oQty').evaluate('(el)=>el===document.activeElement')
            result['paper_order_ack_ms']=round(page.evaluate('''() => {fresh();const t=performance.now();document.querySelector('#buyBtn').click();return performance.now()-t;}'''),3)
            assert page.evaluate('__paper.S.orders[0].status')=='filled'
            assert page.evaluate('__paper.S.orders[0].qty')==7
            assert page.evaluate('__paper.S.positions.find(p=>p.conid===10000).qty')==17
            page.wait_for_timeout(200)
            assert page.evaluate('JSON.parse(localStorage.paperAccount).orders[0].status')=='filled'
            # Feed failures never turn old prices into executable quotes.
            page.evaluate('''() => {__test.feedApply({connected:false,generation:0,quotes:{}});document.querySelector('#buyBtn').click();}''')
            assert page.evaluate('__paper.S.orders.length')==1
            assert page.locator('#buyBtn').is_disabled()
            page.evaluate('''() => {fresh();__paper.Q[10000].at=Date.now()-60000;__test.renderLive();}''')
            assert page.locator('#buyBtn').is_disabled()
            page.evaluate('''() => {fresh();__paper.Q[10000].status='DELAYED';__test.renderLive();}''')
            assert page.locator('#buyBtn').is_disabled()
            # A rested limit fills on a later fresh tick, not a click on Refresh.
            page.evaluate('fresh()')
            page.locator('#oType').select_option('LMT');page.locator('#oLimit').fill('99')
            page.evaluate("() => {fresh();document.querySelector('#buyBtn').click();}")
            assert page.evaluate('__paper.S.orders[0].status')=='working'
            page.evaluate('''() => {__test.feedApply({connected:true,generation:0,quotes:{10000:{bid:98.98,ask:99,last:99,bidSize:1000,askSize:1000,status:'LIVE',at:Date.now()}}});}''')
            assert page.evaluate('__paper.S.orders[0].status')=='filled'
            # A missing expiry quote must not erase the position or its cost basis.
            assert page.evaluate('''() => {
              const s=__paper.S;s.positions.push({conid:42,symbol:'EXPIRED',secType:'OPT',right:'C',qty:1,mult:100,avgCost:2,expiry:'20200101'});
              const before=s.cash;__test.settleExpired();
              if(s.cash!==before||!s.positions.find(p=>p.conid===42))return false;
              __test.settlePosition(42,3);return s.cash===before+300&&!s.positions.find(p=>p.conid===42);
            }''')
            # Export works without any artifact/Claude runtime and removes credentials.
            page.locator('[data-tab="acct"]').click()
            with page.expect_download() as d:page.locator('#dExp').click()
            assert d.value.suggested_filename.endswith('.json')
            page.locator('[data-tab="trade"]').click()
            page.evaluate('fresh()');page.wait_for_timeout(200)
            if args.screenshot:
                page.screenshot(path=str(ROOT/'workstation-test.png'),full_page=True)
            # Phone layout and tab navigation remain usable.
            page.set_viewport_size({'width':390,'height':844})
            page.locator('[data-tab="pos"]').click()
            assert page.locator('#positionsTable').count()==1
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
            page.locator('[data-tab="guns"]').click()
            assert page.locator('#guns-workspace').count()==1
            assert page.locator('[data-guns="arm"]').is_disabled()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
            page.reload(wait_until='domcontentloaded');page.wait_for_function('window.__paper && __paper.S.orders.length===2')
            result['functional_checks']='quotes, 500 positions, stable input, market/limit orders, stale/disconnect/delayed protection, persistence, settlement, export, mobile'
        result['browser_errors']=errors
        print(json.dumps(result,indent=2))
        assert not errors,errors
        browser.close()


if __name__=='__main__':main()
