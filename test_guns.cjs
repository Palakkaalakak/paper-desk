const test=require('node:test'),assert=require('node:assert/strict');
const C=require('./guns.js'),Execution=require('./guns-execution.js');
function fixture(){let now=Date.parse('2026-09-10T13:30:10Z'),equity=100000,live=true,id=0,valid=true;const S={bookId:'b1',positions:[],orders:[]},Q={1:{bid:10,ask:10.02,last:10.01,askSize:10000,bidSize:10000,at:now,receivedAt:now}},inst={conid:1,symbol:'TEST',secType:'STK',mult:1,brokerId:true},plan={setup:1,errors:[],checks:[],entry:10.01,limit:10.04,stop:9.8,tick:.01,session:{start:now-10000,end:now+6*3600000},expiresAt:now+290000};let E;
 const bridge={state:()=>S,quotes:()=>Q,account:()=>({netLiq:equity,bp:equity}),position:cid=>S.positions.find(p=>p.conid===cid),ready:cid=>live&&!!Q[cid],usingTws:()=>live,now:()=>now,uid:()=>String(++id),today:()=>C.day(now),commission:()=>1,marketPrice:(i,side)=>Q[i.conid][side==='BUY'?'ask':'bid'],validate:()=>valid?plan:{...plan,errors:['Stale history']},save:()=>{},sync:()=>{},fill:(o,n,price)=>{let p=S.positions.find(x=>x.conid===o.conid);if(!p){p={conid:o.conid,qty:0};S.positions.push(p);}const old=p.qty;p.qty+=o.side==='BUY'?n:-n;o.filledQty+=n;o.status=o.filledQty===o.qty?'filled':'working';E.after(o,n,price,old,p.qty,0,1);}};
 E=Execution(bridge);return {E,S,Q,inst,plan,bridge,setEquity:x=>equity=x,setLive:x=>live=x,setValid:x=>valid=x,tick:(patch={})=>{now+=1000;Object.assign(Q[1],patch,{at:now,receivedAt:now});},arm:()=>E.arm(plan,inst,{common:true,catalyst:true,room:true}).order};
}
test('risk budget tracks the exact requested current equity examples',()=>{for(const [e,b] of [[100000,1000],[90000,900],[100100,1001]]){const r=C.size(e,1,10,9,e,n=>2);assert.equal(r.budget,b);assert.ok(r.risk<=b);assert.equal(r.qty,Math.floor(b-2));}});
test('whole shares, fees and buying power cap actual risk honestly',()=>{const r=C.size(100000,1,10.04,9.8,100,()=>2);assert.equal(r.qty,9);assert.ok(r.risk<r.budget);assert.ok(r.qty*10.04+r.fees<=100);for(const bad of [NaN,Infinity,-1,0])assert.equal(C.size(bad,1,10,9,1000).qty,0);assert.equal(C.size(100000,1,10,10,1000).qty,0);});
test('pending entry and fill are resized from current equity, not arm budget',()=>{const f=fixture(),o=f.arm();assert.equal(o.guns.budgetAtArm,1000);f.Q[1].last=9.9;f.setEquity(90000);f.E.fill(o);assert.equal(o.guns.budgetAtFill,900);assert.equal(o.qty,f.E.sizing(f.plan,f.inst).qty);f.setEquity(100100);f.tick({last:10.01});f.E.fill(o);assert.equal(o.guns.budgetAtFill,1001);assert.equal(o.status,'filled');assert.equal(f.E.book().active[0].budgetAtFill,1001);});
test('partial entry protects only filled quantity and cancels remainder',()=>{const f=fixture(),o=f.arm();f.Q[1].askSize=7;f.E.fill(o);assert.equal(o.qty,7);assert.equal(o.filledQty,7);assert.ok(o.guns.cancelledQty>0);assert.equal(f.E.book().active[0].qty,7);});
test('disconnected, stale holdings and stale charts cannot open entries',()=>{const f=fixture(),o=f.arm();f.setLive(false);f.E.fill(o);assert.equal(o.filledQty,0);f.setLive(true);f.S.positions.push({conid:2,qty:1});f.E.fill(o);assert.equal(o.filledQty,0);f.S.positions.pop();f.setValid(false);f.E.fill(o);assert.equal(o.filledQty,0);});
test('synthetic midpoint cannot trigger a GUNS entry',()=>{const f=fixture(),o=f.arm();f.Q[1].tradeLast=null;f.E.fill(o);assert.equal(o.filledQty,0);});
test('changed plan requires rearming; expired setup never fills',()=>{let f=fixture(),o=f.arm();f.plan.stop=9.79;f.E.fill(o);assert.equal(o.status,'cancelled');f=fixture();o=f.arm();o.guns.plan.expiresAt=0;f.E.fill(o);assert.equal(o.status,'cancelled');});
test('no fill above entry cap or without displayed liquidity',()=>{const f=fixture(),o=f.arm();f.Q[1].ask=10.05;f.E.fill(o);assert.equal(o.status,'cancelled');const g=fixture(),p=g.arm();g.Q[1].askSize=0;g.E.fill(p);assert.equal(p.filledQty,0);});
test('breakeven and partial latched stop consume each quote only once',()=>{const f=fixture(),o=f.arm();f.Q[1].askSize=7;f.E.fill(o);let b=f.E.book().active[0];f.tick({bid:b.entry+b.initialR+.01});f.E.manage();assert.equal(b.stop,b.entry);f.tick({bid:b.entry-.01,bidSize:3});f.E.manage();assert.equal(b.qty,4);assert.equal(b.stopTriggered,true);f.E.manage();assert.equal(b.qty,4);f.tick({bid:b.entry+.02,bidSize:10});f.E.manage();assert.equal(f.S.positions[0].qty,0);assert.equal(f.E.book().active.length,0);assert.equal(f.E.book().journal.length,1);assert.ok(Number.isFinite(f.E.book().journal[0].actualR));});
test('manual partial close maintains bracket and full close cancels siblings',()=>{const f=fixture(),o=f.arm();f.Q[1].askSize=7;f.E.fill(o);const manual={id:'m',conid:1,side:'SELL',qty:3,filledQty:0,status:'working'};assert.equal(f.E.guard(manual),true);f.bridge.fill(manual,3,10);assert.equal(f.E.book().active[0].qty,4);const sibling={conid:1,side:'SELL',qty:4,filledQty:0,status:'working'};f.S.orders.push(sibling);const full={...manual,id:'f',qty:4,filledQty:0};f.bridge.fill(full,4,10);assert.equal(sibling.status,'cancelled');assert.equal(f.E.book().journal[0].fees,3);});
test('same-symbol buys, oversells and combos are rejected before legs execute',()=>{const f=fixture(),o=f.arm();f.Q[1].askSize=7;f.E.fill(o);for(const order of [{conid:1,side:'BUY',qty:1},{conid:1,side:'SELL',qty:8},{qty:1,legs:[{conid:1,side:'SELL',ratio:1}]}]){Object.assign(order,{filledQty:0,status:'working'});assert.equal(f.E.guard(order),false);assert.equal(order.status,'rejected');}});
test('flatten waits for executable live quotes and never shorts',()=>{const f=fixture(),o=f.arm();f.Q[1].askSize=2;f.E.fill(o);const b=f.E.book().active[0];f.setLive(false);f.E.flatten(b.id);assert.equal(b.qty,2);f.setLive(true);f.tick({bidSize:100});f.E.manage();f.E.manage();assert.equal(f.S.positions[0].qty,0);});
test('New York trading dates honor DST and indicators never fabricate warmup',()=>{assert.equal(C.day(Date.parse('2026-03-09T03:30Z')),'2026-03-08');assert.equal(C.day(Date.parse('2026-11-02T04:30Z')),'2026-11-01');const rows=Array.from({length:20},(_,i)=>({c:i+1,h:i+2,l:i}));assert.equal(C.average(rows,50,false).at(-1),null);assert.equal(C.atr(rows,30),null);assert.equal(C.average(rows,20,false).at(-1),10.5);});
test('missing bars and session cannot produce executable setup',()=>{assert.ok(C.analyze(null,null,{},1,{},Date.now()).errors.length);const p=C.analyze({minute:[],daily:[],sessions:[],minTick:.01},null,{},5,{},Date.now());assert.ok(p.errors.includes('Current exchange session known'));assert.ok(p.errors.includes('Valid trigger, stop and target'));});

const W=require('./guns-workflow.js'),T=require('./guns-tutorial.js');
function chartFixture(){const now=Date.parse('2026-09-10T13:30:10Z'),start=now-10000;return {now,data:{minTick:.01,stockType:'COMMON',updatedAt:now,sessions:[{start,end:start+23400000}],daily:[{t:'2026-09-09',c:9}],minute:Array.from({length:1500},(_,i)=>{const c=8.99+i*.001;return {t:start-(1500-i)*60000,o:c-.001,h:c+.011,l:c-.01,c,v:1000};})},q:{last:10.2,bid:10.2,ask:10.21},notes:{room:true,catalyst:true}};}
test('human confirmation required; heuristic advice is not a chart-quality veto',()=>{
 const f=chartFixture(),n={...f.notes,levels:{3:{trigger:10.2,candleLow:10.1}}};
 let p=C.analyze(f.data,f.q,{},3,n,f.now);assert.ok(p.errors.includes('Chart and setup reviewed by user'));
 p=C.analyze(f.data,f.q,{},3,{...n,chartSetup:3},f.now);assert.deepEqual(p.errors,[]);assert.ok(p.advisories.some(x=>!x.ok));assert.equal(p.levelSource,'user');assert.equal(p.entry,10.21);assert.equal(p.stop,10.09);
 assert.ok(C.analyze(f.data,f.q,{},3,{...n,chartSetup:2},f.now).errors.length);
});
test('missing flag candle low never falls back to ATR',()=>{
 const f=chartFixture();for(const setup of [3,4]){const p=C.analyze(f.data,f.q,{},setup,{...f.notes,chartSetup:setup,levels:{[setup]:{trigger:10.2}}},f.now);assert.ok(p.errors.includes('Valid trigger, stop and target'));assert.equal(p.stop,undefined);}
 const p=C.analyze(f.data,f.q,{},3,{...f.notes,chartSetup:3,levels:{3:{trigger:10.2,candleLow:10.3}}},f.now);assert.ok(p.errors.includes('Valid trigger, stop and target'));
});
test('nested review and news evidence are immutable at arm',()=>{
 const f=fixture(),n={chartSetup:1,levels:{1:{trigger:10}},newsEvidence:{articleId:'first'}},o=f.E.arm(f.plan,f.inst,n).order;
 n.levels[1].trigger=20;n.newsEvidence.articleId='changed';assert.equal(o.guns.notes.levels[1].trigger,10);assert.equal(o.guns.notes.newsEvidence.articleId,'first');
});
test('shortcuts reject unsafe modifiers, repeats, duplicates and malformed bindings',()=>{
 assert.equal(W.chord({code:'Digit1'}),'1');assert.equal(W.chord({code:'KeyQ',altKey:true}),'Alt+Q');assert.equal(W.chord({code:'Digit8',altKey:true}),'Alt+8');
 for(const prop of ['ctrlKey','metaKey','shiftKey','repeat','isComposing'])assert.equal(W.chord({code:'Digit1',[prop]:true}),null);
 assert.equal(W.chord({code:'KeyQ'}),null);assert.equal(W.setBinding(W.defaults,0,'2'),null);assert.equal(W.setBinding(W.defaults,8,'Alt+Q'),null);assert.equal(W.setBinding(W.defaults,0,'bad'),null);
 const b=W.setBinding(W.defaults,0,'Alt+Q');assert.deepEqual(W.bindings({shortcuts:b}),b);assert.deepEqual(W.bindings({shortcuts:['1','1','2','3']}),W.defaults);
});
test('candidate ranking fails closed on missing, stale or mismatched evidence',()=>{
 const now=100000,row={conid:1,rank:0},q={status:'LIVE',last:10,bid:9.99,ask:10.01},e={conid:1,at:now,sessionKnown:true,stockType:'COMMON',previousClose:9,premarketVolume:100000};
 const run=(ev=e,quote=q,ready=true)=>W.candidate(row,quote,ev,ready,C.defaults,now);assert.ok(run().eligible);
 for(const patch of [{at:now-90001},{at:now+5001},{conid:2},{sessionKnown:false},{previousClose:null},{premarketVolume:null},{premarketVolume:-1},{stockType:'ETF'}]){assert.equal(run({...e,...patch}).eligible,false);assert.equal(run({...e,...patch}).score,null);}
 assert.equal(run(null).eligible,false);assert.equal(run(e,q,false).eligible,false);assert.equal(run(e,{...q,status:'DELAYED'}).eligible,false);assert.equal(run(e,{...q,ask:11}).eligible,false);
 const ranked=W.rank([{conid:2,rank:0},row],{1:q,2:q},new Map([[1,e]]),()=>true,C.defaults,now);assert.equal(ranked[0].row.conid,1);
});
test('four guided lessons cover target, slippage, no-chase and partial breakeven',()=>{
 const m=T.model();for(let lesson=0;lesson<4;lesson++){
  let s=m.snapshot();assert.equal(s.lesson,lesson);assert.ok(s.stock.symbol.endsWith('-DEMO'));assert.equal(m.action('confirm',s.stock.setup).stage,0);
  m.action('pick',s.stock.symbol);assert.equal(m.action('news').stage,1);m.action('read');m.action('news');m.action('chart');
  for(const [eq,budget] of [[100000,1000],[90000,900],[100100,1001]]){s=m.action('equity',eq);assert.equal(s.risk.budget,budget);assert.ok(s.risk.risk<=budget+1e-8);}
  m.action('risk');assert.equal(m.action('confirm',99).stage,4);m.action('confirm',s.stock.setup);s=m.action('advance');if(lesson===3)assert.equal(s.position.qty,7);
  for(let i=0;s.stage!==8&&i<3;i++)s=m.action('advance');assert.equal(s.stage,8);assert.match(s.result,[/TARGET/,/STOP:.*Slippage/,/CANCELLED:.*No shares/,/BREAKEVEN STOP: -2.00/][lesson]);assert.equal(s.position,null);if(lesson<3)m.action('next');
 }assert.equal(m.action('restart').lesson,0);
});
test('tutorial chart rejection skips, and separate models do not share progress',()=>{
 const m=T.model(),other=T.model();m.action('pick',T.lessons[0].symbol);m.action('read');m.action('news');assert.match(m.action('reject').result,/SKIPPED/);assert.equal(m.snapshot().position,null);assert.equal(other.snapshot().stage,0);
});


test('S1 always uses the premarket high, never a pivot or saved override',()=>{
 const data={pre:[{h:10},{h:9.8}],pre5:[{h:9.5},{h:9.8},{h:9.6}],tick:.001,atr:.2};
 const one=C.placement(data,{stopMode:'ATR'},1,{trigger:9.8});
 const two=C.placement(data,{stopMode:'ATR'},2);
 assert.equal(one.entry,10.01);assert.equal(one.stop,9.81);assert.equal(one.levelSource,'automatic');assert.equal(two.entry,9.81);
 assert.equal(C.placement({...data,pre:[]},{stopMode:'ATR'},1,{trigger:9.8}).entry,null);
 assert.equal(C.placement({...data,atr:null},{stopMode:'ATR'},1).target,null);
});
test('S4 uses a full cent above and below the candle even on subpenny ticks',()=>{
 const p=C.placement({tick:.001,regular:[{o:9,h:10,l:8.9,c:9.9},{o:9.9,h:9.95,l:9.7,c:9.8}]},{},4);
 assert.equal(p.entry,9.96);assert.equal(p.stop,9.69);
});

test('missing minute groups and unknown volume cannot masquerade as complete bars',()=>{
 const rows=Array.from({length:5},(_,i)=>({t:i*60000,o:10,h:10.1,l:9.9,c:10,v:100}));
 assert.equal(C.aggregate(rows,5)[0].complete,true);
 assert.equal(C.aggregate(rows.filter((_,i)=>i!==2),5)[0].complete,false);
 assert.equal(C.aggregate(rows.map((b,i)=>i===2?{...b,v:null}:b),5)[0].v,null);
 assert.equal(C.validBar({o:10,h:9,l:8,c:10}),false);
});
test('S1 uses the actual forming premarket high; stale updates and absent trades block',()=>{
 const f=chartFixture();f.now-=20000;f.data.updatedAt=f.now;
 const t=Math.floor(f.now/60000)*60000;
 f.data.minute=f.data.minute.filter(b=>b.t<t);
 f.data.minute.push({t,o:10.4,h:10.7,l:10.3,c:10.6,v:100});
 const notes={...f.notes,chartSetup:1};
 const p=C.analyze(f.data,{last:10.6,bid:10.6,ask:10.61},{stopMode:'FIXED',fixedStop:.2},1,notes,f.now);
 assert.equal(p.entry,10.71);
 assert.ok(C.analyze({...f.data,updatedAt:f.now-15001},f.q,{},1,notes,f.now).errors.includes('Chart stream current'));
 const absent=C.analyze(f.data,{last:10.6,tradeLast:null,bid:10.6,ask:10.61},{},1,notes,f.now);
 assert.ok(absent.errors.includes('Price at least $1.50'));
});
test('tutorial candlesticks and levels use shared strategy calculations',()=>{
 for(const l of T.lessons){const p=C.placement(l.source,l.config,l.setup);assert.equal(p.entry,l.entry);assert.equal(p.stop,l.stop);assert.equal(p.target,l.target);
  for(const b of l.candles)assert.equal(C.validBar(b),true);
  const svg=T.candleChart(l,null);assert.equal((svg.match(/class="candle-body"/g)||[]).length,7);assert.ok(!svg.includes('<polyline'));
  assert.ok(svg.includes('data-level="SL" data-price="'+l.stop+'"'));
 }
});

test('five-key migration preserves custom bindings, including conflicts with default 5',()=>{
 assert.equal(W.chord({code:'Digit5'}),'5');
 assert.deepEqual(W.bindings({shortcuts:['Alt+Q','2','3','4']}),['Alt+Q','2','3','4','5']);
 assert.deepEqual(W.bindings({shortcuts:['5','Alt+5','Alt+A','4']}),['5','Alt+5','Alt+A','4','Alt+B']);
 assert.equal(W.setBinding(W.defaults,4,'Alt+Z')[4],'Alt+Z');
});
test('shortlist is at most four qualifying rows, no padding or mutation after ranking',()=>{
 const now=Date.parse('2026-09-10T13:00Z'),rows=Array.from({length:8},(_,i)=>({conid:i+1,rank:i})),quotes={},ev=new Map();
 for(const r of rows){quotes[r.conid]={status:'LIVE',last:10,bid:9.99,ask:10.01};ev.set(r.conid,{conid:r.conid,at:now,sessionKnown:true,stockType:'COMMON',previousClose:9,premarketVolume:50000});}
 ev.get(1).premarketVolume=null;ev.get(2).stockType='ETF';
 const list=W.shortlist(rows,quotes,ev,()=>true,C.defaults,now);assert.deepEqual(list.map(r=>r.conid),[3,4,5,6]);
 quotes[3].last=11;W.rank(rows,quotes,ev,()=>true,C.defaults,now);assert.deepEqual(list.map(r=>r.conid),[3,4,5,6]);
 assert.equal(W.shortlist(rows,quotes,new Map(),()=>true,C.defaults,now).length,0);
});
test('scheduled scan occurs once in T-30/open window with supplied DST/session schedule',()=>{
 for(const open of ['2026-09-10T13:30Z','2026-11-02T14:30Z']){const start=Date.parse(open),sessions=[{start,end:start+23400000}],state={attemptedAt:start-86400000};
 assert.equal(W.scanDue(state,sessions,start-1800001),null);assert.equal(W.scanDue(state,sessions,start-1800000),'scheduled');state.scheduledOpen=start;assert.equal(W.scanDue(state,sessions,start-60000),null);assert.equal(W.scanDue(state,sessions,start+1),null);}
 assert.equal(W.scanDue({},[],1),'initial');assert.equal(W.scanDue({attemptedAt:1},[],2),null);
});
test('strict float excludes unknown, stale, outstanding-only and cap equality',()=>{
 const now=Date.parse('2026-09-10T13:30Z'),row={conid:1},q={status:'LIVE',last:10,bid:9.99,ask:10.01},e={conid:1,at:now,sessionKnown:true,stockType:'COMMON',previousClose:9,premarketVolume:50000};
 const run=f=>W.candidate(row,q,{...e,float:f},true,{...C.defaults,floatMode:'strict'},now);
 assert.equal(run({floatShares:15000000,date:'2026-09-09',source:'Fixture'}).eligible,true);
 for(const f of [null,{outstandingShares:1000000,date:'2026-09-09',source:'Fixture'},{floatShares:100000000,date:'2026-09-09',source:'Fixture'},{floatShares:15000000,date:'2020-01-01',source:'Fixture'}])assert.equal(run(f).eligible,false);
});
test('explicit hover overrides S1 but rejects changed candle, wrong contract or frame',()=>{
 const f=chartFixture();Object.assign(f.data,{symbol:'TEST',conid:1});const candle=f.data.minute.at(-20),hover={enabled:true,symbol:'TEST',conid:1,timeframe:'1',candle:{...candle}},n={...f.notes,chartSetup:1,hover},cfg={stopMode:'FIXED',fixedStop:.2};
 const p=C.analyze(f.data,f.q,cfg,1,n,f.now);assert.equal(p.levelSource,'hover');assert.equal(p.entry,C.round(candle.h+.01,.01,true));assert.notEqual(p.entry,C.analyze(f.data,f.q,cfg,1,{...n,hover:null},f.now).entry);
 for(const patch of [{symbol:'OTHER'},{conid:2},{timeframe:'d'},{candle:{...candle,h:candle.h+.1}},{candle:{...candle,t:f.data.sessions[0].start}}])assert.ok(C.analyze(f.data,f.q,cfg,1,{...n,hover:{...hover,...patch}},f.now).errors.some(e=>e.startsWith('Hover candle')));
});
test('hover flag uses same candle low and S5 cannot use a later candle',()=>{
 const candle={t:1,o:10,h:10.1,l:9.9,c:10};for(const setup of [3,4]){const p=C.placement({tick:.01},{},setup,{mode:'hover',candle});assert.equal(p.entry,10.11);assert.equal(p.stop,9.89);}
 const f=chartFixture(),start=f.data.sessions[0].start;Object.assign(f.data,{symbol:'TEST',conid:1,updatedAt:start+121000});
 f.data.minute.push({t:start,o:10.5,h:10.53,l:10.50,c:10.52,v:100},{t:start+60000,o:10.52,h:10.54,l:10.51,c:10.53,v:100});
 const h={enabled:true,symbol:'TEST',conid:1,timeframe:'1',candle:f.data.minute.at(-1)};
 assert.ok(C.analyze(f.data,f.q,{},5,{...f.notes,chartSetup:5,hover:h},start+121000).errors.some(e=>e.startsWith('Hover candle')));
 const x=fixture(),o=x.E.arm(x.plan,x.inst,{hover:h}).order;h.candle.h=99;assert.notEqual(o.guns.notes.hover.candle.h,99);
});

test('late schedule response does not duplicate an initial T-30 scan',()=>{const start=Date.parse('2026-09-10T13:30Z');assert.equal(W.scanDue({attemptedAt:start-1700000},[{start}],start-1600000),null);});
