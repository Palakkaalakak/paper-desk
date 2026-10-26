const test=require('node:test'),assert=require('node:assert/strict'),B=require('./bcs.js');
const now=Date.UTC(2026,9,26,15),exp='20261127';   // 32 days out
const call=(conid,strike,bid,ask,delta,iv=.3)=>({conid,strike,secType:'OPT',right:'C',currency:'USD',mult:100,underConid:9,root:'T',expiry:exp,conid,quote:{bid,ask,status:'LIVE',at:now,brokerConid:conid,greeks:{delta,iv}}});
const chain=[call(1,95,6.0,6.2,.66),call(2,100,3.4,3.6,.55),call(3,105,1.7,1.8,.38),call(4,110,.75,.85,.24),call(5,115,.3,.4,.12)];
test('economics: debit spread max loss, max profit, breakeven, ratio and exit values',()=>{
 const e=B.economics(chain[1],chain[3],3.0,2);assert.equal(e.width,10);assert.equal(e.maxLoss,600);assert.equal(e.maxProfit,1400);assert.equal(e.breakeven,103);
 assert.ok(Math.abs(e.ratio-7/3)<1e-9);assert.ok(Math.abs(e.tpValue-(3+.8*7))<1e-9);assert.ok(Math.abs(e.slValue-1.5)<1e-9);
});
test('size: premium risked stays under 2% of net liquidation, whole spreads rounded down',()=>{
 assert.equal(B.size(10000,1.0).qty,2);assert.equal(B.size(10000,2.5).qty,0);assert.match(B.size(10000,2.5).reason,/risk budget/);assert.equal(B.size(100000,2.4).qty,8);assert.equal(B.size(0,1).qty,0);
});
test('validate: course rules, each reported separately',()=>{
 const l=chain[1],s=chain[3],p=B.pricing(l,s),ok=B.validate(l,s,p.debit,1,101,9,100000,now);assert.deepEqual(ok,[]);
 assert.ok(B.validate(l,s,p.debit,1,15,9,100000,now).some(m=>/above \$20/.test(m)));
 assert.ok(B.validate(chain[2],chain[3],p.debit,1,101,9,100000,now).some(m=>/at the money or one strike in the money/.test(m)));   // long above spot
 assert.ok(B.validate(l,{...chain[2],strike:103},1.9,1,101,9,100000,now).some(m=>/at least \$5 apart/.test(m)));
 assert.ok(B.validate(l,chain[2],4.5,1,101,9,100000,now).some(m=>/Risk:reward/.test(m)));                          // debit too close to the width
 assert.ok(B.validate(l,s,p.debit,50,101,9,100000,now).some(m=>/exceeds 2% of the account/.test(m)));
 const stale={...l,quote:{...l.quote,at:now-90000}};assert.ok(B.validate(stale,s,p.debit,1,101,9,100000,now).some(m=>/Fresh LIVE/.test(m)));
 assert.ok(B.validate(l,s,p.debit,1,101,9,100000,Date.UTC(2026,10,20)).some(m=>/Expiry should be about/.test(m)));  // 7 days left
});
test('recommend: long near the money, short about one standard deviation up with 1:2 or better',()=>{
 const r=B.recommend(chain,101,now,false,100000);assert.ok(r);assert.equal(r.long.strike,100);assert.ok(r.short.strike>=105);assert.ok(r.ratio>=2);
 assert.equal(B.recommend(chain.map(c=>({...c,quote:{...c.quote,status:'FROZEN'}})),101,now,false,100000),null);
 assert.match(B.explain(chain.map(c=>({...c,quote:{...c.quote,at:now-90000}})),101,now),/0 with live quotes/);
});
test('exits: 80% of max profit, 50% of max loss, gap-down hold with two weeks left',()=>{
 const e=B.economics(chain[1],chain[3],3.0,1);   // tp value 8.6, sl value 1.5
 assert.equal(B.exitDecision(e,8.7,30),'TP');assert.equal(B.exitDecision(e,5,30),null);assert.equal(B.exitDecision(e,1.5,30),'SL');
 assert.equal(B.exitDecision(e,.4,20),null);assert.equal(B.exitDecision(e,.4,10),'SL');   // lost >80%: hold only with >= 14 days left
});
test('execution: closes the spread once at the profit target, never twice',()=>{
 const buy={...chain[1],side:'BUY',ratio:1},sell={...chain[3],side:'SELL',ratio:1};
 const entry={id:'e',symbol:'T BCS',status:'filled',filledQty:2,qty:2,avgFill:3.0,legs:[buy,sell],bcs:{debit:3,exits:{enabled:true,status:'WAITING_ENTRY',...B.economics(chain[1],chain[3],3.0,1)}}};
 const state={orders:[entry],positions:[{...chain[1],qty:2},{...chain[3],qty:-2}],cash:1e5},q={2:{bid:9.2,ask:9.4,status:'LIVE',at:now,brokerConid:2},4:{bid:.5,ask:.6,status:'LIVE',at:now,brokerConid:4}};
 let fills=0;const a={state:()=>state,quotes:()=>q,connected:()=>true,ready:()=>true,now:()=>now,today:()=>'20261026',uid:()=>'x'+state.orders.length,session:()=>({start:now-1e3,end:now+3.6e6}),save(){},fill(o){fills++;o.status='filled';o.avgFill=-8;state.positions.forEach(p=>p.qty=0);}};
 const x=B.createExecution(a);x.manage();assert.equal(state.orders.length,2);assert.equal(state.orders[0].bcsExit.reason,'TP');assert.deepEqual(state.orders[0].legs.map(l=>l.side),['SELL','BUY']);
 x.manage();assert.equal(state.orders.length,2);assert.equal(fills,1);assert.equal(entry.bcs.exits.status,'CLOSED');
});
