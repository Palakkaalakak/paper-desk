"""Browser regression/scale harness. Fixture data is test-only, never served by the app.
Run: python3 browser_check.py [--baseline]
Requires development-only playwright + Chromium. No live gateway is contacted.
"""
import argparse
import json
import pathlib
import re
import statistics
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    html = (ROOT / 'paper_local.html').read_text()
    state = json.loads(re.search(r'<script id="st" type="application/json">(.*?)</script>', html).group(1))
    state['settings'].update(dataSource='free', provider='tws', auto=False)
    state['cash'] = 10000000
    for i in range(500):
        state['positions'].append(dict(conid=10000+i, symbol='TEST'+str(i), secType='STK',
                                      qty=10, mult=1, avgCost=100, exch='SMART', openedToday=False))
    errors = []
    requests = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(r):
            url = r.request.url
            if url == 'http://paper.test/':
                return r.fulfill(content_type='text/html', body=html)
            requests.append(url)
            if '/data/stream' in url:
                return r.fulfill(content_type='text/event-stream', body=': fixture\n\n')
            if '/data/subscriptions' in url:
                instruments = r.request.post_data_json.get('instruments', [])
                quotes = {str(x['conid']): {'bid': 100, 'ask': 100.02, 'last':100.01,
                          'bidSize':10000, 'askSize':10000, 'status':'LIVE', 'at': 9999999999999,
                          'source':'IB Gateway', 'receivedAt':9999999999999} for x in instruments}
                return r.fulfill(json={'quotes':quotes,'connected':True,'capacity':500,'requested':len(instruments), 'active':len(instruments), 'seq':1})
            if '/data/twsstatus' in url:
                return r.fulfill(json={'connected':True,'sealed':True,'readonly':True,'accounts':[]})
            if '/data/providers' in url:
                return r.fulfill(json={'providers':{'tws':{'name':'IB Gateway','local':True,'quotes':True,'chain':True}}})
            if '/data/quote' in url:
                return r.fulfill(json={'last':100.01,'bid':100,'ask':100.02,'volume':10000000,'served_by':'tws'})
            if '/api/' in url:
                return r.fulfill(json={'authenticated':False})
            return r.fulfill(status=404, body='Test harness: unexpected request')
        page.route('**/*', route)
        page.add_init_script('localStorage.setItem("paperAccount", '+json.dumps(json.dumps(state))+');')
        page.goto('http://paper.test/', wait_until='domcontentloaded')
        page.wait_for_function('window.__paper && window.__paper.S.positions.length === 500')
        page.wait_for_timeout(1500)
        page.locator('[data-tab="pos"]').click()
        timing = page.evaluate('''() => {
          const times=[];
          for(let i=0;i<20;i++) {
            const t=performance.now();
            document.querySelector('[data-tab="pos"]').click();
            times.push(performance.now()-t);
          }
          return times;
        }''')
        print(json.dumps({'mode':'baseline' if args.baseline else 'current',
                          'positions':500, 'position_tab_median_ms':round(statistics.median(timing),3),
                          'data_requests':len([u for u in requests if '/data/' in u]),
                          'browser_errors':errors}, indent=2))
        assert not errors, errors
        browser.close()


if __name__ == '__main__':
    main()
