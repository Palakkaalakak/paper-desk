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
