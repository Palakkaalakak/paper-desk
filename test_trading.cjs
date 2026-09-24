const test=require('node:test'),assert=require('node:assert/strict');
const T=require('./trading.js');
const now=Date.parse('2026-09-10T14:30:00Z'),inst={conid:123,symbol:'TEST',secType:'STK',mult:1};
function history(){return {...inst,minTick:.01,updatedAt:now,minute:Array.from({length:450},(_,i)=>({t:now-(450-i)*60000,o:10+i*.01,h:10.1+i*.01,l:9.9+i*.01,c:10+i*.01,v:100})),daily:Array.from({length:20},(_,i)=>({t:new Date(Date.parse('2026-08-20')+i*86400000).toISOString().slice(0,10),o:10,h:11,l:9,c:10}))};}
function args(spec={},extra={}){return {mode:'Trading',inst,spec:{entry:20,stop:'',target:'',timeframe:'5',riskPct:1,rewardR:2,trackMinutes:5,type:'LMT',...spec},data:history(),equity:100000,bp:200000,fee:()=>1,now,...extra};}
test('ATR follows selected completed timeframe, not an unfinished candle',()=>{
  const h=history();const one=T.atrFor(h,'1',now),five=T.atrFor(h,'5',now),fifteen=T.atrFor(h,'15',now);
  assert.ok(five.value>one.value);assert.ok(fifteen.value>five.value);
  h.minute.push({t:now,o:15,h:1000,l:1,c:15});assert.equal(T.atrFor(h,'5',now).value,five.value);
  assert.equal(T.atrFor(h,'d',now).value,2);
});
test('Trading sizes 1% risk including fees and supports TP later / explicit levels',()=>{
  const p=T.calculate(args());assert.equal(p.budget,1000);assert.ok(p.qty*p.priceR+p.fees<=1000+1e-8);
  assert.ok(Math.abs((p.target-p.entry)/p.priceR-2)<.1);
  assert.equal(T.calculate(args({targetLater:true})).target,null);
  const manual=T.calculate(args({stop:19,target:23},{data:null}));assert.equal(manual.stop,19);assert.equal(manual.target,23);assert.equal(manual.qty,998);
});
test('Custom never manufactures ATR, SL, TP or quantity',()=>{
  const p=T.calculate(args({qty:25},{mode:'Custom',data:null}));assert.equal(p.qty,25);assert.equal(p.stop,null);assert.equal(p.target,null);assert.equal(p.atr,null);assert.equal(p.budget,null);
  assert.throws(()=>T.calculate(args({qty:''},{mode:'Custom',data:null})),/whole-share/);
});
test('missing, stale, mismatched, invalid and incomplete history cannot fabricate ATR',()=>{
  assert.throws(()=>T.calculate(args({}, {data:null})),/match/);
  const h=history();h.minute.pop();assert.throws(()=>T.atrFor(h,'5',now),/Latest completed/);
  h.updatedAt=now-100000;assert.throws(()=>T.atrFor(h,'5',now),/Fresh/);
  assert.throws(()=>T.calculate(args({stop:21})),/below entry/);
  assert.throws(()=>T.calculate(args({trackMinutes:31})),/1–30/);
  assert.throws(()=>T.calculate(args({qty:1},{mode:'Custom',bp:NaN})),/buying power/);
});
function fixture(mode='Trading'){
  let time=now,id=0,ready=true;const S={bookId:'b1',account:{mode},cash:100000,realized:0,positions:[],orders:[],trades:[]},Q={123:{at:now,bid:19.99,ask:20,last:20,askSize:1,bidSize:1,status:'LIVE'}};
  const E=T.create({state:()=>S,mode:()=>S.account.mode,now:()=>time,uid:()=>String(++id),today:()=> '2026-09-10',account:()=>({netLiq:S.cash+S.positions.reduce((v,p)=>v+p.qty*p.avgCost,0),bp:S.cash*2}),position:cid=>S.positions.find(p=>p.conid===cid),ready:()=>ready,usingTws:()=>true,quotes:()=>Q,commission:()=>1,save(){},sync(){},
    fill(o,n,price){let p=S.positions.find(x=>x.conid===o.conid);if(!p){p={...inst,qty:0,avgCost:0};S.positions.push(p);}const old=p.qty,cost=p.avgCost,sign=o.side==='BUY'?1:-1;
      if(!old||Math.sign(old)===sign)p.avgCost=(Math.abs(old)*cost+n*price)/(Math.abs(old)+n);p.qty+=sign*n;
      S.cash-=sign*n*price+1;S.realized+=old&&Math.sign(old)!==sign?(price-cost)*Math.min(n,Math.abs(old))*Math.sign(old)-1:-1;
      o.filledQty+=n;o.status='filled';E.after(o,n,price,old,p.qty,0,1,cost);return true;}});
  return {S,Q,E,advance(ms){time+=ms;Q[123].at=time;},ready(v){ready=v;}};
}
test('confirmed Trading order fills full risk size, preserves SL and closes at TP with budget R',()=>{
  const f=fixture(),p=f.E.plan(inst,args({stop:19,strategy:'Breakout'}).spec,null),o=f.E.arm(inst,p,'b1');
  assert.equal(o.qty,998);f.E.fill(o);assert.equal(f.S.positions[0].qty,998);assert.equal(f.E.book().active[0].stop,19);
  f.Q[123].bid=21;f.Q[123].ask=21.01;f.E.manage();assert.equal(f.E.book().active[0].stop,19);
  f.Q[123].bid=22.2;f.Q[123].ask=22.21;f.E.manage();const b=f.E.book().journal[0];assert.equal(b.outcome,'TARGET');assert.equal(b.exitPrice,22);assert.equal(b.net,1994);assert.equal(b.budgetR,1.994);assert.equal(b.strategy,'Breakout');assert.equal(b.tracking.status,'observing');
  assert.deepEqual(f.E.instruments().map(x=>[x.conid,x.priority]),[[123,1]],'Post-exit tracking has lower priority than active execution');
  assert.deepEqual(f.E.instruments().map(i=>[i.conid,i.priority]),[[123,1]]);
  const cash=f.S.cash;f.E.manage();assert.equal(f.S.cash,cash);
});
test('TP can be supplied later and closed exactly once; stale data never fills',()=>{
  const f=fixture(),p=f.E.plan(inst,args({stop:19,targetLater:true}).spec,null),o=f.E.arm(inst,p,'b1');f.ready(false);assert.equal(f.E.fill(o),false);f.ready(true);f.E.fill(o);
  const b=f.E.book().active[0];assert.equal(b.target,null);f.Q[123].bid=25;f.Q[123].ask=25.01;f.E.manage();assert.equal(f.E.book().active.length,1);
  f.E.setTarget(b.id,24);assert.equal(f.E.book().active.length,0);assert.equal(f.E.book().journal[0].exitPrice,24);
});
test('stop latches with missing displayed size, guards duplicate entry and account changes',()=>{
  const f=fixture(),p=f.E.plan(inst,args({stop:19}).spec,null);assert.throws(()=>f.E.arm(inst,p,'other'),/Portfolio/);const o=f.E.arm(inst,p,'b1');assert.throws(()=>f.E.arm(inst,p,'b1'),/already/);f.E.fill(o);
  f.Q[123].bid=18.5;f.Q[123].ask=18.51;f.Q[123].bidSize=0;f.E.manage();assert.equal(f.E.book().active[0].stopTriggered,true);
  f.Q[123].bid=19.2;f.Q[123].ask=19.21;f.Q[123].bidSize=1;f.E.manage();assert.equal(f.E.book().journal[0].outcome,'STOP');
});
test('ordinary partial exits journal fees and realized dollars without inventing budget R',()=>{
  const f=fixture('Custom');f.E.after({...inst,id:'a',side:'BUY',strategyType:'Manual'},10,10,0,10,-1,1,0);
  f.E.after({...inst,id:'b',side:'SELL'},4,11,10,6,3,1,10);assert.equal(f.E.book().active[0].qty,6);
  f.E.after({...inst,id:'c',side:'SELL'},6,12,6,0,11,1,10);const b=f.E.book().journal[0];assert.equal(b.net,13);assert.equal(b.fees,3);assert.equal(b.budgetR,null);assert.equal(b.strategy,'Manual');
});
const quote=(at,bid)=>({at,bid,ask:bid+.01,status:'LIVE'});
test('tracking records distinct observed quotes, later TP and additional R without changing results',()=>{
  const b={entry:10,exitPrice:11,priceR:1,target:12,net:999};T.beginTracking(b,now,1);
  T.observe(b,quote(now+1000,10.5),true,now+1000);T.observe(b,quote(now+1000,10.5),true,now+1100);
  T.observe(b,quote(now+2000,12.5),true,now+2000);
  assert.equal(b.tracking.samples,2);assert.equal(b.tracking.extraR,1.5);assert.equal(b.tracking.adverse,.5);assert.equal(b.tracking.targetHitAt,now+2000);assert.equal(b.net,999);
});
test('tracking marks disconnected/reload gaps and excludes prices after the configured horizon',()=>{
  const b={exitPrice:11,priceR:1,target:12};T.beginTracking(b,now,1);
  T.observe(b,quote(now+1000,11),true,now+1000);
  const saved=JSON.parse(JSON.stringify(b));T.observe(saved,quote(now+61000,99),true,now+61000);
  assert.equal(saved.tracking.max,11);assert.equal(saved.tracking.status,'finished with gaps');assert.ok(saved.tracking.gapMs>50000);
  const empty={};T.beginTracking(empty,now,1);T.observe(empty,null,false,now+60000);assert.equal(empty.tracking.status,'no data');assert.equal(empty.tracking.gapMs,60000);
});
