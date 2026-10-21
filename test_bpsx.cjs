const {test}=require('node:test'),assert=require('node:assert/strict'),C=require('./bpsx.js'),fs=require('node:fs');
const now=Date.now(),date=new Date(now-86400000).toISOString().slice(0,10),expiry=new Date(now+26*86400000).toISOString().slice(0,10).replaceAll('-','');
function put(id,strike,bid,ask,delta){return {conid:id,strike,expiry,secType:'OPT',right:'P',root:'TEST',underConid:100,currency:'USD',mult:100,deliverableStatus:'IB_STANDARD_CLASS_NOT_OCC_VERIFIED',quote:{brokerConid:id,bid,ask,status:'LIVE',at:now,greeks:{delta}}};}
const long=put(1,80,.2,.25,-.06),short=put(2,85,.8,.85,-.1);
test('the committed served file contains the visible BPSX tab, engine, UI and adapter',()=>{const html=fs.readFileSync('paper_local.html','utf8');assert.ok(html.includes("['bpsx','BPSX']"));for(const tag of ['bpsx-engine','bpsx-ui','bpsx-style'])assert.ok(html.includes('id="'+tag+'"'));assert.ok(html.includes('bpsx=window.BPSXUI.create('));assert.ok(html.includes("if(tab==='bpsx' && bpsx)bpsx.mount()"));});
test('10 delta, one lower strike, executable credit and correct 3x-loss closing debit',()=>{const p=C.recommend([long,short],100,now);assert.equal(p.short.conid,2);assert.equal(p.long.conid,1);assert.equal(p.credit,.55);assert.deepEqual(C.validate(short,long,.55,2,100,100,now),[]);const e=C.economics(short,long,.55,2);assert.ok(Math.abs(e.credit-110)<1e-8);assert.equal(e.maxLoss,890);assert.equal(e.lossAlertDebit,2.2);});
test('missing delta, excessive delta, stale/delayed quotes and >10x risk never recommend',()=>{for(const change of [x=>delete x.quote.greeks.delta,x=>x.quote.greeks.delta=-.16,x=>x.quote.status='DELAYED',x=>x.quote.at=now-61000,x=>x.quote.bid=.3]){const s=structuredClone(short);change(s);assert.equal(C.recommend([long,s],100,now),null);}});
test('identity, multiplier, expiry and whole quantity fail closed',()=>{for(const change of [x=>x.right='C',x=>x.mult=10,x=>x.underConid=7,x=>x.expiry='20200101',x=>x.currency='EUR']){const s=structuredClone(short);change(s);assert.ok(C.validate(s,long,.55,1,100,100,now).length);}assert.ok(C.validate(short,long,.55,1.2,100,100,now).length);});
test('daily trend uses both exact MA pairs; unknown history and IV do not pass',()=>{const bars=Array.from({length:200},(_,i)=>({date,o:60+i/5,h:62+i/5,l:59+i/5,c:61+i/5,v:2000000})),ivHistory=Array.from({length:252},(_,i)=>({date,value:.2+i/1000})),d={bars,ivHistory,industry:'Software'};assert.equal(C.metrics(d).uptrend,true);assert.equal(C.metrics(d).ivPercentile,100);assert.equal(C.screen(d,{trend:true}).status,'PASS');assert.equal(C.metrics({bars:bars.slice(0,20)}).uptrend,null);assert.equal(C.screen({bars,ivHistory:[]}).status,'UNVERIFIED');assert.equal(C.screen({...d,industry:'Biotechnology'}).status,'FAIL');assert.equal(C.screen({...d,ivHistory:ivHistory.map(x=>({...x,date:'2000-01-01'}))}).status,'UNVERIFIED');});
test('Bollinger Bands use SMA and population standard deviation without lookahead',()=>{const b=C.bollinger([1,2,3,4],3,2);assert.deepEqual(b.middle,[null,null,2,3]);assert.equal(b.upper[0],null);assert.ok(Math.abs(b.upper[2]-(2+2*Math.sqrt(2/3)))<1e-12);assert.ok(Math.abs(b.lower[3]-(3-2*Math.sqrt(2/3)))<1e-12);assert.throws(()=>C.bollinger([1,2],1,2));});
test('tiny put deltas keep significant digits and legitimate zero bids are displayable',()=>{assert.equal(C.deltaText(-.000023),'-0.000023');assert.equal(C.deltaText(-0),'0');const l=structuredClone(long);l.quote.bid=0;assert.equal(C.fresh(l,now),true);});
test('frozen recommendations are inspection only and never bypass order validation',()=>{const s=structuredClone(short),l=structuredClone(long);s.quote.status=l.quote.status='FROZEN';assert.equal(C.recommend([l,s],100,now),null);assert.ok(C.recommend([l,s],100,now,true));assert.ok(C.validate(s,l,.55,1,100,100,now).length);});
test('Bollinger bands use 20-period SMA and population standard deviation',()=>{const xs=Array.from({length:25},(_,i)=>i+1),b=C.bollinger(xs,20,2);assert.equal(b.middle[19],10.5);assert.equal(b.upper[18],null);assert.ok(Math.abs(b.upper[19]-(10.5+2*Math.sqrt(33.25)))<1e-10);const flat=C.bollinger(Array(25).fill(5));assert.equal(flat.upper[24],5);assert.equal(flat.lower[24],5);assert.throws(()=>C.bollinger(xs,1,2));});
test('tiny deltas never format as misleading minus zero',()=>{assert.equal(C.deltaText(-.00000000123),'-1.23e-9');assert.equal(C.deltaText(-0),'0');assert.equal(C.deltaText(-.095),'-0.095');});
test('frozen quotes allow inspection recommendations but cannot validate an order',()=>{const s=structuredClone(short),l=structuredClone(long);s.quote.status='FROZEN';l.quote.status='FROZEN';assert.equal(C.recommend([l,s],100,now),null);assert.ok(C.recommend([l,s],100,now,true));assert.ok(C.validate(s,l,.55,1,100,100,now).some(x=>x.includes('LIVE')));});
test('payoff dollar amounts scale quantity and translate 3x P&L loss to 4x buyback',()=>{const e=C.economics(short,long,.5,3);assert.equal(e.maxProfit,150);assert.equal(e.maxLoss,1350);assert.equal(e.takeProfitProfit,135);assert.equal(e.stopLossLoss,450);assert.equal(e.takeProfitCost,15);assert.equal(e.stopLossCost,600);assert.equal(e.lossAlertDebit,2);});
test('LIVE identifies feed, not quote quality; raw 0.10 / 20.00 cannot recommend',()=>{
 const s=put(21,1440,.1,20,-.096789),l=put(22,1430,.1,20,-.088585),before=JSON.stringify([s,l]);
 assert.equal(C.quoteQuality(null,now).usable,false);assert.equal(C.fresh(undefined,now),false);
 const rejected=structuredClone(short);rejected.quoteError='354: permission';assert.equal(C.quoteQuality(rejected,now).usable,false);
 const quality=C.quoteQuality(s,now,false,1659.7);assert.equal(quality.width,19.9);assert.equal(quality.usable,false);assert.match(quality.label,/WIDE/);assert.equal(C.fresh(s,now),true);
 assert.equal(C.recommend([l,s],1659.7,now),null);assert.ok(C.validate(s,l,1,1,1659.7,100,now).some(m=>m.startsWith('Option bid/ask spread exceeds 0.4% of share price')));assert.equal(s.quote.bid-l.quote.ask,-19.9);assert.equal(JSON.stringify([s,l]),before);
 for(const mutate of [r=>r.quote.status='FROZEN',r=>r.quote.at=now-61000,r=>r.quote.ask=.1,r=>delete r.quote.bid,r=>r.quote.brokerConid=999]){const r=structuredClone(short);mutate(r);assert.equal(C.quoteQuality(r,now,false,1659.7).usable,false);}
 assert.ok(Math.abs((1-1440/1661)*100-13.305237808549)<1e-9);
});
const noUsage={trades:0,premium:0,risk:0,pendingRisk:0};
function sized(changes={}){return C.size({allocation:20000,credit:.5,width:5,usage:noUsage,available:100000,...changes});}
test('automatic sizing recomputes premium target and floors without forcing one spread',()=>{
 assert.equal(sized().qty,4);assert.equal(sized({credit:1}).qty,2);assert.equal(sized({allocation:10000}).qty,2);assert.equal(sized({allocation:1000}).qty,0);assert.equal(sized({credit:0}).qty,0);assert.equal(sized({width:10}).qty,0);assert.equal(sized({credit:.55}).qty,3);
 assert.equal(sized({allocation:20000,credit:2.01,width:20}).qty,0);
});
test('sizing respects ten trades, remaining premium/risk, pending risk, funds and fees',()=>{
 assert.equal(sized({usage:{...noUsage,trades:10}}).qty,0);
 assert.equal(sized({usage:{...noUsage,premium:1950}}).qty,1);
 assert.equal(sized({usage:{...noUsage,risk:19550}}).qty,1);
 assert.equal(sized({available:900,feePerSpread:2}).qty,1);
 assert.equal(sized({accountQty:1}).qty,1);assert.equal(sized({accountQty:0}).maxQty,0);assert.equal(sized({accountQty:NaN}).qty,0);
 assert.equal(sized({available:1000,usage:{...noUsage,pendingRisk:600}}).qty,0);
 assert.equal(sized({available:NaN}).qty,0);assert.equal(sized({usage:{...noUsage,risk:NaN}}).qty,0);assert.equal(sized({usage:{...noUsage,error:'Reconcile'}}).qty,0);
 assert.ok(sized().maxQty>4); // Manual sizes may exceed the premium target, never hard caps.
});
test('exposure uses actual filled credit, excludes closed trades and reserves working entries',()=>{
 const entry={bpsx:{credit:.5,width:5},status:'working',qty:4,legs:[{conid:2},{conid:1}]};
 assert.deepEqual(C.exposure({orders:[entry],positions:[]}),{trades:1,premium:200,risk:1800,pendingRisk:1800,error:''});
 entry.status='filled';entry.avgFill=-.6;const positions=[{conid:2,qty:-2},{conid:1,qty:2}];
 assert.deepEqual(C.exposure({orders:[entry],positions}),{trades:1,premium:120,risk:880.0000000000001,pendingRisk:0,error:''});
 assert.equal(C.exposure({orders:[entry],positions:[]}).trades,0);
 positions[0].qty=-1;assert.match(C.exposure({orders:[entry],positions}).error,/Reconcile/);
});
function exitFixture(){
 const entry={id:'entry',symbol:'TEST BPSX',status:'filled',filledQty:2,qty:2,avgFill:-.5,legs:[{...short,side:'SELL',ratio:1},{...long,side:'BUY',ratio:1}],bpsx:{credit:.9,exits:{version:1,enabled:true,status:'WAITING_ENTRY'}}};
 const state={orders:[entry],positions:[{...short,qty:-2},{...long,qty:2}],cash:10000},q={1:{...long.quote},2:{...short.quote}},f={state,q,entry,connected:true,session:{start:now-1000,end:now+3600000},noFill:false,fills:0};
 const a={state:()=>f.state,quotes:()=>q,connected:()=>f.connected,ready:()=>true,now:()=>now,today:()=>date,uid:()=>String(f.state.orders.length),session:()=>f.session,save(){},fill(o){f.fills++;if(f.noFill)return;assert.ok(f.execution.guard(o));o.avgFill=q[2].ask-q[1].bid;o.filledQty=o.qty;o.status='filled';f.state.cash-=o.avgFill*100*o.qty;f.state.positions.forEach(p=>p.qty=0);}};
 f.execution=C.createExecution(a);f.adapter=a;return f;
}
test('paper TP uses actual credit, creates one inverse closing combo and survives reload',()=>{const f=exitFixture();f.q[2].ask=.25;f.q[2].bid=.24;f.q[1].bid=.2;f.execution.manage();assert.equal(f.state.orders.length,2);const exit=f.state.orders[0];assert.equal(exit.bpsxExit.reason,'TP');assert.equal(exit.limit,.05);assert.deepEqual(exit.legs.map(l=>l.side),['BUY','SELL']);assert.equal(f.entry.bpsx.exits.entryCredit,.5);assert.equal(f.entry.bpsx.exits.status,'CLOSED');f.execution.manage();f.state=JSON.parse(JSON.stringify(f.state));f.execution=C.createExecution(f.adapter);f.execution.manage();assert.equal(f.state.orders.length,2);assert.equal(f.fills,1);});
test('paper SL gaps execute at observed debit, not a fabricated threshold; latch stays single',()=>{const f=exitFixture();f.q[2].ask=2.7;f.q[2].bid=2.6;f.q[1].bid=.2;f.noFill=true;f.execution.manage();const exit=f.state.orders[0];assert.equal(exit.bpsxExit.reason,'SL');assert.equal(exit.limit,null);assert.equal(exit.bpsxExit.triggerDebit,2.5);assert.equal(f.entry.bpsx.exits.slDebit,2);f.execution.manage();assert.equal(f.state.orders.length,2);f.noFill=false;f.execution.manage();assert.equal(exit.avgFill,2.5);assert.equal(f.entry.bpsx.exits.status,'CLOSED');});
test('paper exits reject stale/frozen/future/halted/crossed/mismatched/disconnected/out-of-session data',()=>{for(const mutate of [f=>f.connected=false,f=>f.session=null,f=>f.q[2].status='FROZEN',f=>f.q[2].at=now-16000,f=>f.q[2].at=now+1,f=>f.q[2].halted=true,f=>f.q[2].error='10197',f=>f.q[2].brokerConid=77,f=>f.q[2].bid=5]){const f=exitFixture();f.q[2].ask=2.5;mutate(f);f.execution.manage();assert.equal(f.state.orders.length,1);assert.equal(f.fills,0);}});
test('manual cancellation, imbalanced legs and other pending closes cannot duplicate/reverse exposure',()=>{let f=exitFixture();f.q[2].ask=2.5;f.noFill=true;f.execution.manage();f.state.orders[0].status='cancelled';f.execution.manage();assert.equal(f.state.orders.length,2);assert.equal(f.entry.bpsx.exits.status,'EXIT_CANCELLED_MANUALLY');f=exitFixture();f.state.positions[0].qty=-1;f.execution.manage();assert.equal(f.state.orders.length,1);assert.equal(f.entry.bpsx.exits.status,'RECONCILE_LEGS');f=exitFixture();f.state.orders.push({id:'manual',status:'working',conid:2});f.execution.manage();assert.equal(f.entry.bpsx.exits.status,'OTHER_ORDER_REQUIRES_REVIEW');});
test('existing pre-automation positions and unfilled entries are not silently opted into exits',()=>{const f=exitFixture();f.q[2].ask=2.5;delete f.entry.bpsx.exits;f.execution.manage();assert.equal(f.state.orders.length,1);f.entry.bpsx.exits={enabled:true};f.entry.status='working';f.execution.manage();assert.equal(f.state.orders.length,1);});
test('expiry safety close respects verified session end and readonly/changed-book guards',()=>{let f=exitFixture();const ny=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(now)).replaceAll('-','');f.entry.legs.forEach(l=>l.expiry=ny);f.session.end=now+4*60000;f.q[2].ask=.6;f.q[2].bid=.5;f.execution.manage();assert.equal(f.state.orders[0].bpsxExit.reason,'EXPIRY');f=exitFixture();f.q[2].ask=2.5;f.adapter.writable=()=>false;f.execution.manage();assert.equal(f.state.orders.length,1);f.adapter.writable=()=>true;f.noFill=true;f.execution.manage();const exit=f.state.orders[0];f.state={orders:[],positions:[],cash:0};assert.equal(f.execution.guard(exit),false);});
test('latched stop cannot fill outside session or over another manual closing order',()=>{const f=exitFixture();f.q[2].ask=2.5;f.noFill=true;f.execution.manage();const exit=f.state.orders[0];f.session=null;assert.equal(f.execution.guard(exit),false);f.session={start:now-1000,end:now+1000};f.state.orders.push({id:'other',status:'working',conid:2});assert.equal(f.execution.guard(exit),false);});
test('expiration prefers 26 days without stretching outside 20–45',()=>{const exp=d=>new Date(now+d*86400000).toISOString().slice(0,10).replaceAll('-','');assert.equal(C.expiration([exp(45),exp(26),exp(20)],now),exp(26));assert.equal(C.expiration([exp(19),exp(46)],now),null);});

test('spread cap is 0.4% of the share price, not a flat $0.40',()=>{
 const now=Date.now(),mk=(bid,ask)=>({secType:'OPT',right:'P',quote:{bid,ask,status:'LIVE',at:now,brokerConid:1},conid:1});
 assert.equal(C.quoteQuality(mk(1,1.4),now,false,100).usable,true);   // $0.40 on $100
 assert.equal(C.quoteQuality(mk(1,1.5),now,false,100).usable,false);
 assert.equal(C.quoteQuality(mk(1,1.5),now,false,400).usable,true);   // $1.60 allowed on $400
 assert.equal(C.quoteQuality(mk(1,1.3),now,false,50).usable,false);   // only $0.20 on $50
 assert.equal(C.quoteQuality(mk(1,1.1),now,false,null).usable,false); // no share price, no pass
});

test('explain reports why no spread qualifies',()=>{
 const now=Date.now(),row=(k,bid,ask,delta)=>({secType:'OPT',right:'P',conid:k,strike:k,quote:{bid,ask,status:'LIVE',at:now,brokerConid:k,greeks:{delta}}});
 const msg=C.explain([row(90,1,5,-.1),row(85,.5,.6,-.05)],100,now);
 assert.match(msg,/2 puts, 2 with bid\/ask, 1 with delta/);assert.match(msg,/blocked by/);
 assert.match(C.explain([],100,now),/waiting for quotes/);
});
